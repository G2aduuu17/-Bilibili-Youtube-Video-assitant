from __future__ import annotations

from redis import Redis
from rq import Queue

from .config import Settings


def get_queue(settings: Settings) -> Queue:
    connection = Redis.from_url(settings.redis_url)
    return Queue(settings.rq_queue_name, connection=connection, default_timeout=60 * 60 * 4)


def enqueue_task(settings: Settings, task_id: str) -> None:
    get_queue(settings).enqueue("app.worker.run_task", task_id, job_id=task_id)

