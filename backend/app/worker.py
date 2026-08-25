from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from .config import get_settings


LOGGER = logging.getLogger(__name__)


class TaskCancelled(Exception):
    """Raised when a user requests cooperative cancellation."""


PROGRESS_BY_STAGE = {
    "acquire": (8, "正在获取视频"),
    "probe": (14, "正在读取视频信息"),
    "audio": (22, "正在提取音频"),
    "asr": (38, "正在进行语音识别"),
    "frames": (48, "正在抽取关键画面"),
    "segments": (52, "正在划分分析片段"),
    "multimodal_analysis": (78, "正在进行多模态分析"),
    "entity_verification": (85, "正在核验专名"),
    "entity_corrections": (89, "正在应用修正"),
    "summary": (94, "正在生成综合结论"),
    "report": (98, "正在渲染报告"),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _mongo_collection() -> tuple[MongoClient, Any, Any, Any]:
    settings = get_settings()
    client = MongoClient(settings.mongodb_url)
    database = client[settings.mongodb_database]
    return client, database.tasks, database.reports, database.segments


def _project_ml_root() -> Path:
    root = get_settings().project_ml_root.resolve()
    if not root.exists():
        raise RuntimeError(f"找不到 Project-ML 目录：{root}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _update_progress(tasks: Any, task_id: str, stage: str, message: str) -> None:
    percent, fallback = PROGRESS_BY_STAGE.get(stage, (5, "正在准备任务"))
    task = tasks.find_one({"_id": task_id}, {"cancel_requested": 1})
    if task and task.get("cancel_requested"):
        raise TaskCancelled("任务已被用户取消")
    tasks.update_one(
        {"_id": task_id},
        {
            "$set": {
                "status": "processing",
                "progress": {
                    "stage": stage,
                    "percent": percent,
                    "message": message or fallback,
                },
                "updated_at": utcnow(),
            }
        },
    )


def _store_segments(segments: Any, task_id: str, run_dir: Path) -> None:
    if not isinstance(segments, list):
        return
    for item in segments:
        if not isinstance(item, dict) or not item.get("segment_id"):
            continue
        document = {**item, "_id": f"{task_id}:{item['segment_id']}", "task_id": task_id}
        segments.replace_one({"_id": document["_id"]}, document, upsert=True)


def run_task(task_id: str) -> None:
    """RQ entrypoint: run one Project-ML job and persist its report."""
    settings = get_settings()
    client, tasks, reports, segments = _mongo_collection()
    task = tasks.find_one({"_id": task_id})
    if not task:
        client.close()
        return

    run_dir = settings.task_directory(task_id)
    try:
        if task.get("cancel_requested"):
            raise TaskCancelled("任务在开始前已被取消")

        root = _project_ml_root()
        from video_assistant.config import Settings as PipelineSettings
        from video_assistant.pipeline import VideoAnalysisPipeline

        source = task.get("source") or {}
        pipeline_settings = PipelineSettings.from_env(root)
        report_mode = source.get("report_mode", "detailed")
        problems = pipeline_settings.validate(require_api_key=True)
        if problems:
            raise RuntimeError("；".join(problems))

        source_input = source.get("local_path") or source.get("url")
        if not source_input:
            raise RuntimeError("任务缺少视频来源")

        _update_progress(tasks, task_id, "acquire", "任务已开始")

        def progress_callback(stage: str, message: str) -> None:
            _update_progress(tasks, task_id, stage, message)

        pipeline = VideoAnalysisPipeline(
            settings=pipeline_settings,
            run_dir=run_dir,
            cookie_file=None,
            progress_callback=progress_callback,
            mode=report_mode,
            verify_entities=source.get("verify_entities"),
        )
        report_path = pipeline.run(str(source_input))

        summary = _read_json(run_dir / "summary.json", {})
        metadata = _read_json(run_dir / "metadata.json", {})
        analysis = _read_json(run_dir / "analysis_corrected.json", [])
        _store_segments(segments, task_id, analysis)

        reports.replace_one(
            {"task_id": task_id},
            {
                "task_id": task_id,
                "title": str(summary.get("title") or metadata.get("title") or "视频分析报告"),
                "summary": summary,
                "markdown": Path(report_path).read_text(encoding="utf-8"),
                "updated_at": utcnow(),
            },
            upsert=True,
        )
        tasks.update_one(
            {"_id": task_id},
            {
                "$set": {
                    "status": "completed",
                    "progress": {
                        "stage": "completed",
                        "percent": 100,
                        "message": "报告已生成",
                    },
                    "run_dir": str(run_dir),
                    "report_path": str(report_path),
                    "completed_at": utcnow(),
                    "updated_at": utcnow(),
                    "error": None,
                }
            },
        )
    except TaskCancelled as exc:
        tasks.update_one(
            {"_id": task_id},
            {
                "$set": {
                    "status": "cancelled",
                    "progress": {"stage": "cancelled", "percent": 0, "message": "任务已取消"},
                    "error": str(exc),
                    "updated_at": utcnow(),
                }
            },
        )
    except Exception as exc:  # RQ records the exception separately as well.
        LOGGER.exception("任务 %s 执行失败", task_id)
        tasks.update_one(
            {"_id": task_id},
            {
                "$set": {
                    "status": "failed",
                    "progress": {"stage": "failed", "percent": 0, "message": "任务处理失败"},
                    "error": str(exc),
                    "updated_at": utcnow(),
                }
            },
        )
        raise
    finally:
        client.close()
