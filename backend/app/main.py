from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from redis import Redis
from rq.job import Job

from .config import Settings, get_settings
from .queue import enqueue_task, get_queue
from .repository import (
    ensure_indexes,
    find_task,
    insert_task,
    list_tasks,
    now,
    to_task_response,
    update_task,
)
from .schemas import ReportResponse, TaskListResponse, TaskResponse, UrlTaskRequest
from .storage import resolve_asset, save_upload, task_directory


LOGGER = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    client = AsyncMongoClient(settings.mongodb_url)
    database = client[settings.mongodb_database]
    await database.command("ping")
    await ensure_indexes(database)
    app.state.mongodb_client = client
    app.state.database = database
    yield
    await client.close()


app = FastAPI(title="VideoDevour Online API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_database(request: Request) -> AsyncDatabase:
    return request.app.state.database


def new_task_document(task_id: str, source: dict) -> dict:
    timestamp = utcnow()
    return {
        "_id": task_id,
        "source": source,
        "status": "queued",
        "progress": {"stage": "queued", "percent": 0, "message": "任务已加入队列"},
        "cancel_requested": False,
        "error": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


async def create_and_enqueue(database: AsyncDatabase, source: dict) -> TaskResponse:
    settings = get_settings()
    task_id = str(uuid4())
    task = new_task_document(task_id, source)
    await insert_task(database, task)
    try:
        enqueue_task(settings, task_id)
    except Exception as exc:
        await update_task(database, task_id, {"status": "failed", "error": f"队列不可用：{exc}"})
        raise HTTPException(status_code=503, detail="任务队列暂不可用") from exc
    return TaskResponse(**to_task_response(task))


@app.get("/api/v1/health")
async def health(database: AsyncDatabase = Depends(get_database)):
    await database.command("ping")
    redis = Redis.from_url(get_settings().redis_url)
    redis.ping()
    return {"status": "healthy", "time": utcnow()}


@app.post("/api/v1/tasks/upload", response_model=TaskResponse, status_code=202)
async def create_upload_task(
    file: UploadFile = File(...),
    report_mode: Literal["fast", "standard", "detailed"] = Form("detailed"),
    verify_entities: bool = Form(True),
    database: AsyncDatabase = Depends(get_database),
):
    filename = file.filename or ""
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 MP4、MOV、MKV、WebM、AVI、M4V 视频")

    task_id = str(uuid4())
    settings = get_settings()
    directory = task_directory(settings, task_id)
    source_file = await save_upload(file, directory, settings.max_upload_bytes)
    timestamp = utcnow()
    task = {
        "_id": task_id,
        "source": {
            "type": "upload",
            "filename": filename,
            "local_path": str(source_file),
            "report_mode": report_mode,
            "verify_entities": verify_entities,
        },
        "status": "queued",
        "progress": {"stage": "queued", "percent": 0, "message": "文件上传完成，等待处理"},
        "cancel_requested": False,
        "error": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    await insert_task(database, task)
    try:
        enqueue_task(settings, task_id)
    except Exception as exc:
        await update_task(database, task_id, {"status": "failed", "error": f"队列不可用：{exc}"})
        raise HTTPException(status_code=503, detail="任务队列暂不可用") from exc
    return TaskResponse(**to_task_response(task))


@app.post("/api/v1/tasks/url", response_model=TaskResponse, status_code=202)
async def create_url_task(
    request: UrlTaskRequest,
    database: AsyncDatabase = Depends(get_database),
):
    return await create_and_enqueue(
        database,
        {
            "type": "url",
            "url": str(request.url),
            "filename": None,
            "report_mode": request.report_mode,
            "verify_entities": request.verify_entities,
        },
    )


@app.get("/api/v1/tasks", response_model=TaskListResponse)
async def get_tasks(
    limit: int = 30,
    database: AsyncDatabase = Depends(get_database),
):
    limit = min(max(limit, 1), 100)
    tasks = await list_tasks(database, limit)
    return {"items": [to_task_response(task) for task in tasks]}


@app.get("/api/v1/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, database: AsyncDatabase = Depends(get_database)):
    task = await find_task(database, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return to_task_response(task)


@app.post("/api/v1/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(task_id: str, database: AsyncDatabase = Depends(get_database)):
    task = await find_task(database, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] in {"completed", "failed", "cancelled"}:
        return to_task_response(task)

    await update_task(database, task_id, {"cancel_requested": True})
    if task["status"] == "queued":
        try:
            job = Job.fetch(task_id, connection=get_queue(get_settings()).connection)
            job.cancel()
        except Exception:
            LOGGER.info("任务 %s 已不在队列中，将在 Worker 下一阶段停止", task_id)
    await update_task(
        database,
        task_id,
        {
            "status": "cancelled" if task["status"] == "queued" else task["status"],
            "progress": {"stage": "cancelling", "percent": task["progress"]["percent"], "message": "正在取消任务"},
        },
    )
    updated = await find_task(database, task_id)
    return to_task_response(updated)


@app.get("/api/v1/tasks/{task_id}/report", response_model=ReportResponse)
async def get_report(task_id: str, database: AsyncDatabase = Depends(get_database)):
    task = await find_task(database, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    report = await database.reports.find_one({"task_id": task_id})
    if not report:
        raise HTTPException(status_code=409, detail="报告尚未生成")
    return {
        "task_id": task_id,
        "title": report.get("title") or "视频分析报告",
        "summary": report.get("summary") or {},
        "markdown": report.get("markdown") or "",
        "assets_base_url": f"/api/v1/tasks/{task_id}/assets/",
        "updated_at": report.get("updated_at") or utcnow(),
    }


@app.get("/api/v1/tasks/{task_id}/assets/{asset_path:path}")
async def get_asset(task_id: str, asset_path: str, database: AsyncDatabase = Depends(get_database)):
    if not await find_task(database, task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    path = resolve_asset(task_directory(get_settings(), task_id), asset_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="资源不存在")
    return FileResponse(path)
