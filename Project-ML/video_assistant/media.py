from __future__ import annotations

import json
import logging
import math
import re
import shutil
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Settings
from .modes import AnalysisMode, ModeProfile, build_mode_profile
from .models import TranscriptItem, VideoFrame
from .utils import run_command


LOGGER = logging.getLogger("video_assistant")
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".m4v"}
AUDIO_SUFFIXES = {".m4a", ".mp3", ".aac", ".opus", ".ogg", ".wav", ".webm"}
SUBTITLE_SUFFIXES = {".vtt", ".srt"}
SUBTITLE_TIMESTAMP = re.compile(
    r"(?P<start>(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d+)\s+-->\s+"
    r"(?P<end>(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d+)"
)
SUBTITLE_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class AcquiredMedia:
    path: Path | None
    kind: str
    metadata: dict[str, Any]


def is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def acquire_video(
    source: str,
    run_dir: Path,
    settings: Settings,
    cookie_file: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Backward-compatible detailed-mode video acquisition."""
    profile = build_mode_profile(settings, AnalysisMode.DETAILED)
    acquired = acquire_media(source, run_dir, settings, profile, cookie_file)
    if acquired.path is None:
        raise RuntimeError("深度模式没有获取到可分析的视频文件。")
    return acquired.path, acquired.metadata


def acquire_media(
    source: str,
    run_dir: Path,
    settings: Settings,
    profile: ModeProfile,
    cookie_file: Path | None = None,
) -> AcquiredMedia:
    """Acquire only the media required by the selected analysis mode."""
    local_path = Path(source).expanduser()
    if local_path.exists():
        resolved = local_path.resolve()
        if not resolved.is_file():
            raise ValueError(f"输入路径不是文件：{resolved}")
        return AcquiredMedia(
            path=resolved,
            kind="local",
            metadata={
                "input_type": "local",
                "source": str(resolved),
                "title": resolved.stem,
                "media_kind": "local",
            },
        )

    if not is_http_url(source):
        raise ValueError("输入必须是存在的本地视频文件，或 http/https 视频链接。")

    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError("缺少 yt-dlp，请先执行：pip install -r requirements.txt") from exc

    download_dir = run_dir / "source"
    download_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(download_dir / "source.%(ext)s")
    common_options: dict[str, Any] = {
        "outtmpl": output_template,
        "noplaylist": True,
        # Do not force-overwrite: yt-dlp disables --continue when this is True.
        # Keeping partial files lets a retry resume an interrupted CDN download.
        "overwrites": False,
        "continuedl": True,
        # Retry a few times on the same resolved URL, then let the outer loop
        # create a new extractor instance and refresh Bilibili's CDN URL.
        "retries": settings.download_retries_per_attempt,
        "fragment_retries": settings.download_retries_per_attempt,
        "extractor_retries": settings.download_retries_per_attempt,
        "socket_timeout": settings.download_socket_timeout_seconds,
        # Download platform-provided captions when available. The pipeline can
        # then use them instead of making a separate ASR request.
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["zh-Hans", "zh-CN", "zh", "en", "en.*"],
        "subtitlesformat": "vtt/best",
        "quiet": True,
        "no_warnings": False,
    }
    ffmpeg_path = Path(settings.ffmpeg_bin).expanduser()
    if ffmpeg_path.is_file():
        # yt-dlp launches FFmpeg itself when separate video/audio streams need
        # merging, so it must receive the same bundled binary location used by
        # the rest of the pipeline.
        common_options["ffmpeg_location"] = str(ffmpeg_path.resolve().parent)
    else:
        ffmpeg_on_path = shutil.which(settings.ffmpeg_bin)
        if ffmpeg_on_path:
            common_options["ffmpeg_location"] = str(Path(ffmpeg_on_path).resolve().parent)
    if cookie_file:
        common_options["cookiefile"] = str(cookie_file.resolve())

    if profile.subtitle_short_circuit:
        subtitle_options = {
            **common_options,
            "skip_download": True,
        }
        LOGGER.info("极速模式优先获取平台字幕：%s", source)
        information = _extract_with_retries(
            YoutubeDL,
            subtitle_options,
            source,
            settings,
            operation="平台字幕获取",
        )
        subtitle_transcript = load_downloaded_subtitles(download_dir)
        if subtitle_transcript:
            metadata = _metadata_from_information(information, source, None)
            metadata["media_kind"] = "subtitle_only"
            return AcquiredMedia(None, "subtitle_only", metadata)

        LOGGER.info("没有可用平台字幕，极速模式仅下载音频流")
        audio_options = {
            **common_options,
            "format": profile.download_format,
            "writesubtitles": False,
            "writeautomaticsub": False,
        }
        information = _extract_with_retries(
            YoutubeDL,
            audio_options,
            source,
            settings,
            operation="音频下载",
        )
        audio_path = _latest_media_file(download_dir, AUDIO_SUFFIXES)
        if audio_path is None:
            raise RuntimeError("yt-dlp 已结束，但没有找到下载后的音频文件。")
        metadata = _metadata_from_information(information, source, audio_path)
        metadata["media_kind"] = "audio"
        return AcquiredMedia(audio_path, "audio", metadata)

    video_options = {
        **common_options,
        "format": profile.download_format,
        "merge_output_format": "mp4",
    }
    LOGGER.info("%s模式下载在线视频：%s", profile.mode.value, source)
    information = _extract_with_retries(
        YoutubeDL,
        video_options,
        source,
        settings,
        operation="在线视频下载",
    )
    video_path = _latest_media_file(download_dir, VIDEO_SUFFIXES)
    if video_path is None:
        raise RuntimeError("yt-dlp 已结束，但没有找到下载后的视频文件。")
    metadata = _metadata_from_information(information, source, video_path)
    metadata["media_kind"] = "video"
    return AcquiredMedia(video_path, "video", metadata)


def _extract_with_retries(
    youtube_dl: Any,
    options: dict[str, Any],
    source: str,
    settings: Settings,
    operation: str,
) -> dict[str, Any]:
    information: dict[str, Any] | None = None
    last_error: Exception | None = None
    for attempt in range(1, settings.download_attempts + 1):
        try:
            # A new YoutubeDL instance re-extracts the page and can obtain a
            # different, fresh CDN URL instead of retrying one timed-out host.
            with youtube_dl(options) as downloader:
                information = downloader.extract_info(source, download=True)
            break
        except Exception as exc:
            last_error = exc
            if attempt == settings.download_attempts:
                break
            delay_seconds = min(8, 2**attempt)
            LOGGER.warning(
                "%s第 %d/%d 次失败，将在 %d 秒后重新解析并重试：%s",
                operation,
                attempt,
                settings.download_attempts,
                delay_seconds,
                exc,
            )
            time.sleep(delay_seconds)

    if information is None:
        raise RuntimeError(
            f"{operation}失败，已重新解析并尝试 "
            f"{settings.download_attempts} 次：{last_error}"
        ) from last_error
    return information


def _latest_media_file(download_dir: Path, suffixes: set[str]) -> Path | None:
    candidates = sorted(
        (
            path
            for path in download_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in suffixes
            and not path.name.endswith(".part")
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    preferred = download_dir / "source.mp4"
    if preferred.exists() and preferred.suffix.lower() in suffixes:
        return preferred
    return candidates[0] if candidates else None


def _metadata_from_information(
    information: dict[str, Any],
    source: str,
    media_path: Path | None,
) -> dict[str, Any]:
    metadata = {
        "input_type": "url",
        "source": source,
        "title": information.get("title") or (media_path.stem if media_path else "在线视频"),
        "uploader": information.get("uploader"),
        "channel": information.get("channel"),
        "description": information.get("description"),
        "tags": information.get("tags") or [],
        "categories": information.get("categories") or [],
        "webpage_url": information.get("webpage_url") or source,
        "duration": information.get("duration"),
        "video_id": information.get("id"),
    }
    if media_path is not None:
        metadata["downloaded_file"] = str(media_path)
    return metadata


def load_downloaded_subtitles(download_dir: Path) -> list[TranscriptItem]:
    """Load the best available yt-dlp subtitle file from one video download."""
    candidates = [
        path
        for path in download_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUBTITLE_SUFFIXES
    ]

    def preference(path: Path) -> tuple[int, str]:
        name = path.name.lower()
        if any(language in name for language in ("zh-hans", "zh-cn", ".zh.")):
            return (0, name)
        if "zh" in name:
            return (1, name)
        if "en" in name:
            return (2, name)
        return (3, name)

    for path in sorted(candidates, key=preference):
        transcript = _parse_subtitle_file(path)
        if transcript:
            LOGGER.info("使用平台字幕：%s（%d 条）", path.name, len(transcript))
            return transcript
    return []


def _parse_subtitle_file(path: Path) -> list[TranscriptItem]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    items: list[TranscriptItem] = []
    index = 0
    while index < len(lines):
        match = SUBTITLE_TIMESTAMP.search(lines[index])
        if not match:
            index += 1
            continue
        start = _subtitle_seconds(match.group("start"))
        end = _subtitle_seconds(match.group("end"))
        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index].strip())
            index += 1
        text = unescape(SUBTITLE_TAGS.sub("", " ".join(text_lines))).strip()
        if text and end > start:
            items.append(TranscriptItem(start=start, end=end, text=text))
        index += 1
    return items


def _subtitle_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    seconds = float(parts[-1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + seconds
    return int(parts[0]) * 60 + seconds


def probe_video(video_path: Path, settings: Settings) -> dict[str, Any]:
    result = run_command(
        [
            settings.ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name,size,bit_rate",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,r_frame_rate",
            "-of",
            "json",
            str(video_path),
        ],
        "读取视频信息",
    )
    payload = json.loads(result.stdout)
    duration = float(payload.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("无法从视频中读取有效时长。")
    payload["duration_seconds"] = duration
    return payload


def extract_audio(video_path: Path, audio_path: Path, settings: Settings) -> Path:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            settings.ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ],
        "提取 16kHz 单声道音频",
    )
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg 没有生成有效音频文件。")
    return audio_path


def extract_audio_chunks(
    media_path: Path,
    output_dir: Path,
    settings: Settings,
    chunk_seconds: int,
) -> list[Path]:
    """Create small independent WAV chunks for parallel fast-mode ASR."""
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(output_dir.glob("chunk_*.wav"))
    if existing and all(path.stat().st_size > 0 for path in existing):
        return existing

    output_pattern = output_dir / "chunk_%03d.wav"
    run_command(
        [
            settings.ffmpeg_bin,
            "-y",
            "-i",
            str(media_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "segment",
            "-segment_time",
            str(chunk_seconds),
            "-reset_timestamps",
            "1",
            str(output_pattern),
        ],
        f"按 {chunk_seconds} 秒切分极速转写音频",
    )
    chunks = sorted(output_dir.glob("chunk_*.wav"))
    if not chunks or any(path.stat().st_size == 0 for path in chunks):
        raise RuntimeError("FFmpeg 没有生成有效的极速转写音频分片。")
    return chunks


def _write_jpeg(path: Path, frame: Any, quality: int = 88) -> None:
    import cv2

    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError(f"无法编码视频帧：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded.tofile(path)


def _resize_for_output(frame: Any, max_width: int) -> Any:
    import cv2

    height, width = frame.shape[:2]
    if width <= max_width:
        return frame
    scale = max_width / width
    return cv2.resize(
        frame,
        (max_width, max(1, int(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def sample_video_frames(
    video_path: Path,
    run_dir: Path,
    duration: float,
    settings: Settings,
) -> list[VideoFrame]:
    """
    Sample periodic evidence frames plus visual scene changes.

    Periodic frames are always kept. Scene frames share a separate budget so
    early high-motion content cannot consume all evidence slots.
    """
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "缺少 OpenCV/NumPy，请先执行：pip install -r requirements.txt"
        ) from exc

    frame_dir = run_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV 无法打开视频：{video_path}")

    periodic_count = max(1, math.ceil(duration / settings.periodic_frame_seconds))
    scene_budget = max(0, settings.max_frames - periodic_count)
    saved: list[VideoFrame] = []
    scene_saved = 0
    last_scene_time = -float("inf")
    previous_scan = None
    timestamp = 0.0

    LOGGER.info(
        "抽取视觉证据：扫描间隔 %.1fs，定时保底 %.1fs，场景阈值 %.2f",
        settings.scan_interval_seconds,
        settings.periodic_frame_seconds,
        settings.scene_threshold,
    )

    try:
        while timestamp < duration:
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                timestamp += settings.scan_interval_seconds
                continue

            preview = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
            preview = cv2.cvtColor(preview, cv2.COLOR_BGR2GRAY)
            scene_score = 0.0
            if previous_scan is not None:
                difference = cv2.absdiff(preview, previous_scan)
                scene_score = float(np.mean(difference) / 255.0)
            previous_scan = preview

            periodic_index = round(timestamp / settings.periodic_frame_seconds)
            periodic_target = periodic_index * settings.periodic_frame_seconds
            is_periodic = (
                abs(timestamp - periodic_target)
                <= settings.scan_interval_seconds / 2 + 1e-6
            )
            is_scene = (
                scene_saved < scene_budget
                and scene_score >= settings.scene_threshold
                and timestamp - last_scene_time >= settings.min_scene_gap_seconds
            )

            if is_periodic or is_scene:
                reason = "periodic" if is_periodic else "scene"
                output_frame = _resize_for_output(frame, settings.image_max_width)
                filename = f"frame_{len(saved) + 1:05d}_{timestamp:010.2f}.jpg"
                path = frame_dir / filename
                _write_jpeg(path, output_frame)
                saved.append(
                    VideoFrame(
                        timestamp=round(timestamp, 3),
                        path=path.relative_to(run_dir).as_posix(),
                        reason=reason,
                        scene_score=round(scene_score, 4),
                    )
                )
                if is_scene and not is_periodic:
                    scene_saved += 1
                    last_scene_time = timestamp

            timestamp += settings.scan_interval_seconds
    finally:
        capture.release()

    if not saved:
        raise RuntimeError("没有从视频中抽取到任何画面。")

    LOGGER.info(
        "已保存 %d 张视觉证据帧（其中场景变化帧 %d 张）",
        len(saved),
        scene_saved,
    )
    return saved


def copy_report_asset(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
