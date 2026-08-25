from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .config import Settings


SAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def task_directory(settings: Settings, task_id: str) -> Path:
    directory = settings.task_directory(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_filename(filename: str) -> str:
    candidate = SAFE_FILE_NAME.sub("_", Path(filename).name).strip("._")
    return candidate or "video.mp4"


async def save_upload(
    upload: UploadFile,
    task_dir: Path,
    maximum_size: int,
) -> Path:
    source_dir = task_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    destination = source_dir / safe_filename(upload.filename or "video.mp4")
    written = 0

    try:
        with destination.open("wb") as stream:
            while chunk := await upload.read(1024 * 1024):
                written += len(chunk)
                if written > maximum_size:
                    raise HTTPException(status_code=413, detail="上传文件超过大小限制")
                stream.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    if written == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="上传文件为空")
    return destination


def resolve_asset(task_dir: Path, relative_path: str) -> Path:
    target = (task_dir / relative_path).resolve()
    if task_dir.resolve() not in target.parents and target != task_dir.resolve():
        raise HTTPException(status_code=400, detail="非法资源路径")
    return target

