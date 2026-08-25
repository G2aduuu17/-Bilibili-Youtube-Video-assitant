from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def load_project_env(project_root: Path) -> None:
    """Load .env when python-dotenv is available."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(project_root / ".env", override=False)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(slots=True)
class Settings:
    project_root: Path
    output_root: Path
    dashscope_api_key: str
    qwen_model: str
    qwen_base_url: str
    asr_model: str
    asr_websocket_url: str
    ffmpeg_bin: str
    ffprobe_bin: str
    ytdlp_format: str
    entity_verify_enabled: bool
    entity_verify_model: str
    entity_verify_search_strategy: str
    entity_verify_min_confidence: float
    entity_verify_max_entities: int
    entity_glossary_path: Path
    segment_seconds: int
    max_parallel_segment_analyses: int
    scan_interval_seconds: float
    periodic_frame_seconds: float
    min_scene_gap_seconds: float
    scene_threshold: float
    max_frames: int
    max_images_per_segment: int
    image_max_width: int
    request_timeout_seconds: int
    download_attempts: int
    download_retries_per_attempt: int
    download_socket_timeout_seconds: int
    fast_summary_enabled: bool
    fast_summary_model: str
    fast_summary_max_transcript_chars: int
    fast_asr_model: str
    fast_asr_chunk_seconds: int
    fast_asr_parallelism: int
    language_hints: tuple[str, ...]

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "Settings":
        root = (project_root or Path(__file__).resolve().parents[1]).resolve()
        load_project_env(root)

        output_value = os.getenv("VIDEO_ASSISTANT_OUTPUT_DIR", "runs")
        output_root = Path(output_value)
        if not output_root.is_absolute():
            output_root = root / output_root

        hints = tuple(
            part.strip()
            for part in os.getenv("ASR_LANGUAGE_HINTS", "zh,en").split(",")
            if part.strip()
        )
        glossary_path = Path(
            os.getenv("ENTITY_GLOSSARY_PATH", "entity_glossary.json")
        ).expanduser()
        if not glossary_path.is_absolute():
            glossary_path = root / glossary_path

        return cls(
            project_root=root,
            output_root=output_root.resolve(),
            dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
            qwen_model=os.getenv("QWEN_MODEL", "qwen3.7-plus").strip(),
            qwen_base_url=os.getenv(
                "DASHSCOPE_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ).rstrip("/"),
            asr_model=os.getenv("ASR_MODEL", "paraformer-realtime-v2").strip(),
            asr_websocket_url=os.getenv(
                "DASHSCOPE_WEBSOCKET_URL",
                "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
            ).rstrip("/"),
            ffmpeg_bin=os.getenv("FFMPEG_BIN", "ffmpeg").strip(),
            ffprobe_bin=os.getenv("FFPROBE_BIN", "ffprobe").strip(),
            ytdlp_format=os.getenv(
                "YTDLP_FORMAT",
                "bv*[vcodec^=avc1]+ba/b[vcodec^=avc1]/bv*+ba/b",
            ).strip(),
            entity_verify_enabled=_env_bool("ENTITY_VERIFY_ENABLED", True),
            entity_verify_model=os.getenv(
                "ENTITY_VERIFY_MODEL",
                "qwen-plus",
            ).strip(),
            entity_verify_search_strategy=os.getenv(
                "ENTITY_VERIFY_SEARCH_STRATEGY",
                "turbo",
            ).strip(),
            entity_verify_min_confidence=_env_float(
                "ENTITY_VERIFY_MIN_CONFIDENCE",
                0.85,
            ),
            entity_verify_max_entities=_env_int(
                "ENTITY_VERIFY_MAX_ENTITIES",
                20,
            ),
            entity_glossary_path=glossary_path.resolve(),
            segment_seconds=_env_int("SEGMENT_SECONDS", 60),
            max_parallel_segment_analyses=_env_int(
                "MAX_PARALLEL_SEGMENT_ANALYSES",
                2,
            ),
            scan_interval_seconds=_env_float("FRAME_SCAN_INTERVAL_SECONDS", 1.0),
            periodic_frame_seconds=_env_float("PERIODIC_FRAME_SECONDS", 30.0),
            min_scene_gap_seconds=_env_float("MIN_SCENE_GAP_SECONDS", 8.0),
            scene_threshold=_env_float("SCENE_THRESHOLD", 0.22),
            max_frames=_env_int("MAX_TOTAL_FRAMES", 500),
            max_images_per_segment=_env_int("MAX_IMAGES_PER_SEGMENT", 8),
            image_max_width=_env_int("IMAGE_MAX_WIDTH", 1280),
            request_timeout_seconds=_env_int("MODEL_REQUEST_TIMEOUT_SECONDS", 180),
            download_attempts=_env_int("DOWNLOAD_ATTEMPTS", 3),
            download_retries_per_attempt=_env_int(
                "DOWNLOAD_RETRIES_PER_ATTEMPT",
                2,
            ),
            download_socket_timeout_seconds=_env_int(
                "DOWNLOAD_SOCKET_TIMEOUT_SECONDS",
                60,
            ),
            fast_summary_enabled=_env_bool("FAST_SUMMARY_ENABLED", False),
            fast_summary_model=os.getenv(
                "FAST_SUMMARY_MODEL",
                "qwen3.7-flash",
            ).strip(),
            fast_summary_max_transcript_chars=_env_int(
                "FAST_SUMMARY_MAX_TRANSCRIPT_CHARS",
                24_000,
            ),
            fast_asr_model=os.getenv(
                "FAST_ASR_MODEL",
                "qwen3-asr-flash",
            ).strip(),
            fast_asr_chunk_seconds=_env_int("FAST_ASR_CHUNK_SECONDS", 180),
            fast_asr_parallelism=_env_int("FAST_ASR_PARALLELISM", 4),
            language_hints=hints,
        )

    def validate(self, require_api_key: bool = True) -> list[str]:
        problems: list[str] = []
        if require_api_key and not self.dashscope_api_key:
            problems.append("缺少 DASHSCOPE_API_KEY。请复制 .env.example 为 .env 后填写。")
        if not self._command_exists(self.ffmpeg_bin):
            problems.append(
                f"找不到 FFmpeg：{self.ffmpeg_bin!r}。请安装 FFmpeg 或设置 FFMPEG_BIN。"
            )
        if not self._command_exists(self.ffprobe_bin):
            problems.append(
                f"找不到 FFprobe：{self.ffprobe_bin!r}。请安装 FFmpeg 或设置 FFPROBE_BIN。"
            )
        if self.segment_seconds <= 0:
            problems.append("SEGMENT_SECONDS 必须大于 0。")
        if self.max_parallel_segment_analyses <= 0:
            problems.append("MAX_PARALLEL_SEGMENT_ANALYSES 必须大于 0。")
        if not self.ytdlp_format:
            problems.append("YTDLP_FORMAT 不能为空。")
        if self.entity_verify_enabled and not self.entity_verify_model:
            problems.append("ENTITY_VERIFY_MODEL 不能为空。")
        if not 0 <= self.entity_verify_min_confidence <= 1:
            problems.append("ENTITY_VERIFY_MIN_CONFIDENCE 必须介于 0 和 1。")
        if self.entity_verify_max_entities <= 0:
            problems.append("ENTITY_VERIFY_MAX_ENTITIES 必须大于 0。")
        if self.max_images_per_segment <= 0:
            problems.append("MAX_IMAGES_PER_SEGMENT 必须大于 0。")
        if self.download_attempts <= 0:
            problems.append("DOWNLOAD_ATTEMPTS 必须大于 0。")
        if self.download_retries_per_attempt < 0:
            problems.append("DOWNLOAD_RETRIES_PER_ATTEMPT 不能小于 0。")
        if self.download_socket_timeout_seconds <= 0:
            problems.append("DOWNLOAD_SOCKET_TIMEOUT_SECONDS 必须大于 0。")
        if not self.fast_summary_model:
            problems.append("FAST_SUMMARY_MODEL 不能为空。")
        if self.fast_summary_max_transcript_chars <= 0:
            problems.append("FAST_SUMMARY_MAX_TRANSCRIPT_CHARS 必须大于 0。")
        if not self.fast_asr_model:
            problems.append("FAST_ASR_MODEL 不能为空。")
        if not 30 <= self.fast_asr_chunk_seconds <= 300:
            problems.append("FAST_ASR_CHUNK_SECONDS 必须介于 30 和 300 秒。")
        if self.fast_asr_parallelism <= 0:
            problems.append("FAST_ASR_PARALLELISM 必须大于 0。")
        return problems

    @staticmethod
    def _command_exists(command: str) -> bool:
        candidate = Path(command)
        if candidate.parent != Path(".") or candidate.is_absolute():
            return candidate.exists()
        return shutil.which(command) is not None
