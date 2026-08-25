from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.asynchronous.database import AsyncDatabase


def now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(database: AsyncDatabase) -> None:
    await database.tasks.create_indexes(
        [
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("status", ASCENDING), ("updated_at", ASCENDING)]),
            IndexModel([("source.hash", ASCENDING)], sparse=True),
        ]
    )
    await database.reports.create_indexes(
        [IndexModel([("task_id", ASCENDING)], unique=True)]
    )
    await database.segments.create_indexes(
        [IndexModel([("task_id", ASCENDING), ("segment_id", ASCENDING)], unique=True)]
    )


async def insert_task(database: AsyncDatabase, task: dict[str, Any]) -> None:
    await database.tasks.insert_one(task)


async def find_task(database: AsyncDatabase, task_id: str) -> dict[str, Any] | None:
    return await database.tasks.find_one({"_id": task_id})


async def update_task(
    database: AsyncDatabase,
    task_id: str,
    updates: dict[str, Any],
) -> None:
    updates["updated_at"] = now()
    await database.tasks.update_one({"_id": task_id}, {"$set": updates})


async def list_tasks(database: AsyncDatabase, limit: int = 30) -> list[dict[str, Any]]:
    cursor = database.tasks.find({}).sort("created_at", DESCENDING).limit(limit)
    return await cursor.to_list(length=limit)


def to_task_response(task: dict[str, Any]) -> dict[str, Any]:
    source = task.get("source") or {}
    return {
        "task_id": task["_id"],
        "source_type": source.get("type", "upload"),
        "filename": source.get("filename"),
        "source_url": source.get("url"),
        "status": task.get("status", "queued"),
        "progress": task.get(
            "progress",
            {"stage": "queued", "percent": 0, "message": "等待处理"},
        ),
        "error": task.get("error"),
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
    }

