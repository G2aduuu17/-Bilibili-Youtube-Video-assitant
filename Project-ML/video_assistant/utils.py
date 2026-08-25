from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


LOGGER = logging.getLogger("video_assistant")


def setup_logging(run_dir: Path | None = None, verbose: bool = False) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.FileHandler(run_dir / "pipeline.log", encoding="utf-8")
        )
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
        force=True,
    )


def run_command(command: Sequence[str], description: str) -> subprocess.CompletedProcess[str]:
    LOGGER.info("%s", description)
    LOGGER.debug("Command: %s", subprocess.list2cmdline(list(command)))
    try:
        return subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"执行 {description} 失败：找不到命令 {command[0]!r}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"{description}失败：{detail}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def slugify(value: str, fallback: str = "video") -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value, flags=re.UNICODE)
    value = value.strip("-_")
    return value[:80] or fallback


def create_run_dir(output_root: Path, source: str) -> Path:
    source_path = Path(source)
    if source_path.exists():
        label = source_path.stem
    else:
        match = re.search(r"(BV[0-9A-Za-z]+|av\d+)", source, re.IGNORECASE)
        label = match.group(1) if match else "online-video"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / f"{slugify(label)}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def format_timestamp(seconds: float, include_millis: bool = False) -> str:
    seconds = max(0.0, seconds)
    if not include_millis:
        whole = int(seconds)
        hours, remainder = divmod(whole, 3600)
        minutes, whole_seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"

    total_millis = int(round(seconds * 1000))
    hours, remainder = divmod(total_millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


def parse_json_payload(content: str) -> dict[str, Any]:
    """Extract one JSON object from plain text or a Markdown code fence."""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content)
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型返回内容中没有 JSON 对象。")
        value = json.loads(content[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型返回的 JSON 顶层必须是对象。")
    return value
