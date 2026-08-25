from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


TaskStatus = Literal["queued", "processing", "completed", "failed", "cancelled"]


class UrlTaskRequest(BaseModel):
    url: HttpUrl
    report_mode: Literal["fast", "standard", "detailed"] = "detailed"
    verify_entities: bool = True


class TaskProgress(BaseModel):
    stage: str
    percent: int = Field(ge=0, le=100)
    message: str


class TaskResponse(BaseModel):
    task_id: str
    source_type: Literal["upload", "url"]
    filename: str | None = None
    source_url: str | None = None
    status: TaskStatus
    progress: TaskProgress
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    items: list[TaskResponse]


class ReportResponse(BaseModel):
    task_id: str
    title: str
    summary: dict[str, Any]
    markdown: str
    assets_base_url: str
    updated_at: datetime
