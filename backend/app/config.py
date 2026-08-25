from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "VideoDevour Online API"
    app_env: str = "development"
    app_secret: str = "change-me"
    app_cors_origins: str = "http://localhost:5173,http://localhost:8080"

    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_database: str = "video_devour"
    redis_url: str = "redis://localhost:6379/0"
    rq_queue_name: str = "video-analysis"

    task_storage_root: Path = Path("data/tasks")
    project_ml_root: Path = Path("Project-ML")
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.app_cors_origins.split(",") if origin.strip()]

    def task_directory(self, task_id: str) -> Path:
        return (self.task_storage_root.resolve() / task_id).resolve()


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.task_storage_root.mkdir(parents=True, exist_ok=True)
    return settings

