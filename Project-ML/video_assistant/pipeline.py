from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .asr import DashScopeASR, FastDashScopeASR, transcript_to_srt
from .config import Settings
from .entities import (
    ENTITY_VERIFIER_VERSION,
    EntityVerifier,
    correct_analyses,
    correct_transcript,
    merge_entity_glossary,
)
from .media import (
    AcquiredMedia,
    acquire_media,
    extract_audio,
    extract_audio_chunks,
    load_downloaded_subtitles,
    probe_video,
    sample_video_frames,
)
from .modes import (
    AnalysisMode,
    build_mode_profile,
    settings_for_profile,
)
from .models import TranscriptItem, VideoFrame, build_segments
from .qwen import (
    SEGMENT_ANALYSIS_VERSION,
    SUMMARY_SCHEMA_VERSION,
    QwenMultimodalClient,
)
from .report import render_markdown_report
from .utils import read_json, write_json


LOGGER = logging.getLogger("video_assistant")


def _fingerprint(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class VideoAnalysisPipeline:
    def __init__(
        self,
        settings: Settings,
        run_dir: Path,
        force: bool = False,
        cookie_file: Path | None = None,
        progress_callback: Callable[[str, str], None] | None = None,
        mode: str | AnalysisMode | None = None,
        verify_entities: bool | None = None,
    ):
        inferred_mode = (
            AnalysisMode.FAST
            if mode is None and settings.fast_summary_enabled
            else mode
        )
        self.profile = build_mode_profile(
            settings,
            inferred_mode,
            verify_entities=verify_entities,
        )
        self.settings = settings_for_profile(settings, self.profile)
        self.run_dir = run_dir.resolve()
        self.force = force
        self.cookie_file = cookie_file
        self.progress_callback = progress_callback
        self.state_path = self.run_dir / "state.json"
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def run(self, source: str) -> Path:
        state = self._load_or_create_state(source)
        try:
            self._notify("acquire", "正在获取视频")
            acquired = self._acquire(source)
            media_path = acquired.path
            source_metadata = acquired.metadata
            self._step(state, "acquire", "completed")

            subtitle_transcript = self._load_platform_subtitles()
            if media_path is not None:
                probe = self._probe(media_path)
                duration = float(probe["duration_seconds"])
            else:
                duration = float(source_metadata.get("duration") or 0)
                if duration <= 0 and subtitle_transcript:
                    duration = max(item.end for item in subtitle_transcript)
                if duration <= 0:
                    raise RuntimeError("无法从平台元数据或字幕中读取有效时长。")
                probe = {
                    "duration_seconds": duration,
                    "source": "platform_metadata",
                    "format": {},
                    "streams": [],
                }
            source_metadata.update(
                {
                    "duration_seconds": duration,
                    "probe": probe,
                    "qwen_model": self.settings.qwen_model,
                    "asr_model": self.settings.asr_model,
                    "entity_verify_model": (
                        self.settings.entity_verify_model
                        if self.settings.entity_verify_enabled
                        and not self.profile.text_only
                        else None
                    ),
                    "transcript_source": (
                        "platform_subtitle" if subtitle_transcript else "asr"
                    ),
                    "analysis_mode": self.profile.mode.value,
                    "media_kind": acquired.kind,
                }
            )
            write_json(self.run_dir / "metadata.json", source_metadata)
            self._step(state, "probe", "completed")

            if subtitle_transcript:
                self._persist_transcript(subtitle_transcript)
                self._step(state, "audio", "skipped")
                self._step(state, "asr", "completed")
                transcript = subtitle_transcript
                frames = (
                    []
                    if not self.profile.use_frames
                    else self._sample_frames(self._require_media(media_path), duration)
                )
                self._step(
                    state,
                    "frames",
                    "skipped" if not self.profile.use_frames else "completed",
                )
            elif self.profile.text_only:
                required_media = self._require_media(media_path)
                transcript = self._transcribe_fast(required_media, duration)
                self._step(state, "audio", "completed")
                self._step(state, "asr", "completed")
                frames = []
                self._step(state, "frames", "skipped")
            else:
                # ASR and visual frame sampling only depend on the downloaded
                # video, so overlap them instead of waiting for ASR to finish.
                required_media = self._require_media(media_path)
                with ThreadPoolExecutor(max_workers=2) as executor:
                    audio_future = executor.submit(self._extract_audio, required_media)
                    frames_future = executor.submit(
                        self._sample_frames,
                        required_media,
                        duration,
                    )
                    audio_path = audio_future.result()
                    self._step(state, "audio", "completed")
                    transcript = self._transcribe(audio_path)
                    self._step(state, "asr", "completed")
                    frames = frames_future.result()
                    self._step(state, "frames", "completed")

            if self.profile.text_only:
                analyses = self._build_fast_analysis(
                    source_metadata,
                    transcript,
                    duration,
                )
                write_json(self.run_dir / "segments.json", analyses)
                write_json(self.run_dir / "analysis.json", analyses)
                write_json(self.run_dir / "analysis_corrected.json", analyses)
                self._step(state, "segments", "completed")
                self._step(state, "multimodal_analysis", "skipped")
                verification = {
                    "enabled": False,
                    "model": None,
                    "confidence_threshold": self.settings.entity_verify_min_confidence,
                    "corrections": [],
                    "unresolved": [],
                    "search_results": [],
                }
                write_json(self.run_dir / "entity_verification.json", verification)
                self._step(state, "entity_verification", "skipped")
            else:
                segments = build_segments(
                    duration=duration,
                    segment_seconds=self.profile.segment_seconds,
                    transcript=transcript,
                    frames=frames,
                )
                write_json(
                    self.run_dir / "segments.json",
                    [segment.to_dict() for segment in segments],
                )
                self._step(state, "segments", "completed")

                analyses = self._analyze_segments(segments)
                self._step(state, "multimodal_analysis", "completed")

                verification = self._verify_entities(
                    source_metadata,
                    transcript,
                    analyses,
                )
                self._step(state, "entity_verification", "completed")

            corrected_transcript, corrected_analyses = self._apply_corrections(
                transcript,
                analyses,
                verification,
            )
            self._step(state, "entity_corrections", "completed")

            summary = self._summarize(
                source_metadata,
                corrected_analyses,
                verification,
            )
            self._step(state, "summary", "completed")

            report_path = self.run_dir / "report.md"
            report_path.write_text(
                render_markdown_report(
                    source_metadata,
                    summary,
                    corrected_analyses,
                    verification,
                ),
                encoding="utf-8",
            )
            self._step(state, "report", "completed")
            state["status"] = "completed"
            state.pop("failed_at", None)
            state["completed_at"] = datetime.now().isoformat()
            state["report"] = str(report_path)
            write_json(self.state_path, state)
            LOGGER.info("全流程完成：%s", report_path)
            return report_path
        except Exception as exc:
            state["status"] = "failed"
            state["error"] = str(exc)
            state["failed_at"] = datetime.now().isoformat()
            write_json(self.state_path, state)
            raise

    def _load_or_create_state(self, source: str) -> dict[str, Any]:
        if self.state_path.exists():
            state = read_json(self.state_path)
            previous_source = state.get("source_input")
            if previous_source and previous_source != source and not self.force:
                raise ValueError(
                    "该 run_dir 属于另一个输入。若确认覆盖，请增加 --force。"
                )
        else:
            state = {
                "source_input": source,
                "created_at": datetime.now().isoformat(),
                "steps": {},
            }
        state.update({"source_input": source, "status": "processing", "error": None})
        state.pop("failed_at", None)
        write_json(self.state_path, state)
        return state

    def _step(self, state: dict[str, Any], name: str, status: str) -> None:
        state.setdefault("steps", {})[name] = {
            "status": status,
            "updated_at": datetime.now().isoformat(),
        }
        state["current_step"] = name
        write_json(self.state_path, state)
        self._notify(name, "已完成" if status == "completed" else status)

    def _notify(self, stage: str, message: str) -> None:
        if self.progress_callback:
            self.progress_callback(stage, message)

    def _acquire(self, source: str) -> AcquiredMedia:
        cache_path = self.run_dir / "source_metadata.json"
        if cache_path.exists() and not self.force:
            metadata = read_json(cache_path)
            if metadata.get("acquisition_mode") == self.profile.mode.value:
                media_kind = str(metadata.get("media_kind") or "video")
                candidate_value = metadata.get("resolved_media_path")
                if candidate_value:
                    candidate = Path(str(candidate_value))
                    if candidate.is_file():
                        LOGGER.info("复用已获取的媒体：%s", candidate)
                        return AcquiredMedia(candidate, media_kind, metadata)
                if media_kind == "subtitle_only" and self._load_platform_subtitles():
                    LOGGER.info("复用已获取的平台字幕")
                    return AcquiredMedia(None, media_kind, metadata)

        acquired = acquire_media(
            source,
            self.run_dir,
            self.settings,
            self.profile,
            self.cookie_file,
        )
        metadata = dict(acquired.metadata)
        metadata["acquisition_mode"] = self.profile.mode.value
        if acquired.path is not None:
            metadata["resolved_media_path"] = str(acquired.path.resolve())
        write_json(cache_path, metadata)
        return AcquiredMedia(acquired.path, acquired.kind, metadata)

    @staticmethod
    def _require_media(media_path: Path | None) -> Path:
        if media_path is None:
            raise RuntimeError("当前分析步骤需要媒体文件，但获取阶段只返回了字幕。")
        return media_path

    def _probe(self, video_path: Path) -> dict[str, Any]:
        cache_path = self.run_dir / "probe.json"
        if cache_path.exists() and not self.force:
            LOGGER.info("复用视频探测结果")
            return read_json(cache_path)
        value = probe_video(video_path, self.settings)
        write_json(cache_path, value)
        return value

    def _extract_audio(self, video_path: Path) -> Path:
        audio_path = self.run_dir / "audio" / "audio_16k_mono.wav"
        if audio_path.exists() and audio_path.stat().st_size > 0 and not self.force:
            LOGGER.info("复用已提取音频：%s", audio_path)
            return audio_path
        return extract_audio(video_path, audio_path, self.settings)

    def _transcribe(self, audio_path: Path) -> list[TranscriptItem]:
        cache_path = self.run_dir / "transcript.json"
        if cache_path.exists() and not self.force:
            LOGGER.info("复用语音转写")
            return [TranscriptItem(**item) for item in read_json(cache_path)]

        transcript = DashScopeASR(self.settings).transcribe(audio_path)
        self._persist_transcript(transcript)
        return transcript

    def _transcribe_fast(
        self,
        media_path: Path,
        duration: float,
    ) -> list[TranscriptItem]:
        cache_path = self.run_dir / "transcript.json"
        if cache_path.exists() and not self.force:
            LOGGER.info("复用极速语音转写")
            return [TranscriptItem(**item) for item in read_json(cache_path)]

        chunk_dir = self.run_dir / "audio" / "fast_chunks"
        chunks = extract_audio_chunks(
            media_path,
            chunk_dir,
            self.settings,
            self.settings.fast_asr_chunk_seconds,
        )
        try:
            transcript = FastDashScopeASR(self.settings).transcribe(chunks, duration)
        except Exception as exc:
            LOGGER.warning("并行极速转写失败，回退到稳定实时 ASR：%s", exc)
            audio_path = self._extract_audio(media_path)
            transcript = DashScopeASR(self.settings).transcribe(audio_path)
        self._persist_transcript(transcript)
        return transcript

    def _persist_transcript(self, transcript: list[TranscriptItem]) -> None:
        write_json(
            self.run_dir / "transcript.json",
            [item.to_dict() for item in transcript],
        )
        (self.run_dir / "transcript.srt").write_text(
            transcript_to_srt(transcript),
            encoding="utf-8",
        )

    def _load_platform_subtitles(self) -> list[TranscriptItem]:
        if not (self.run_dir / "source").is_dir():
            return []
        try:
            return load_downloaded_subtitles(self.run_dir / "source")
        except OSError as exc:
            LOGGER.warning("读取平台字幕失败，将回退到 ASR：%s", exc)
            return []

    def _build_fast_analysis(
        self,
        metadata: dict[str, Any],
        transcript: list[TranscriptItem],
        duration: float,
    ) -> list[dict[str, Any]]:
        transcript_text = "\n".join(
            f"[{item.start:.2f}-{item.end:.2f}] {item.text}"
            for item in transcript
        )
        clipped_text = transcript_text[: self.settings.fast_summary_max_transcript_chars]
        if len(clipped_text) < len(transcript_text):
            clipped_text += "\n[转写内容过长，已截取前段用于极速摘要]"
        LOGGER.info("极速摘要使用 %d 字符的%s", len(clipped_text), metadata["transcript_source"])
        return [
            {
                "segment_id": "seg_0001",
                "start": 0.0,
                "end": duration,
                "title": str(metadata.get("title") or "视频摘要"),
                "summary": clipped_text,
                "key_points": [],
                "keywords": [],
                "visual_events": [],
                "on_screen_text": [],
                "named_entities": [],
                "notable_quotes": [],
                "transcript_corrections": [],
                "importance_score": 1.0,
                "representative_frame": "",
                "evidence_frames": [],
                "analysis_mode": "fast",
            }
        ]

    def _sample_frames(self, video_path: Path, duration: float) -> list[VideoFrame]:
        cache_path = self.run_dir / "frames.json"
        if cache_path.exists() and not self.force:
            LOGGER.info("复用已抽取的视觉证据")
            return [VideoFrame(**item) for item in read_json(cache_path)]

        frames = sample_video_frames(
            video_path,
            self.run_dir,
            duration,
            self.settings,
        )
        write_json(cache_path, [frame.to_dict() for frame in frames])
        return frames

    def _analyze_segments(self, segments: list[Any]) -> list[dict[str, Any]]:
        analysis_dir = self.run_dir / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        cache_record_path = self.run_dir / "analysis_cache.json"
        cache_records = (
            read_json(cache_record_path)
            if cache_record_path.exists() and not self.force
            else {}
        )
        if not isinstance(cache_records, dict):
            cache_records = {}
        next_cache_records: dict[str, str] = {}
        analyses_by_index: dict[int, dict[str, Any]] = {}
        pending: list[tuple[int, Any, Path]] = []

        for index, segment in enumerate(segments, start=1):
            cache_path = analysis_dir / f"{segment.segment_id}.json"
            input_fingerprint = _fingerprint(
                {
                    "segment": segment.to_dict(),
                    "model": self.settings.qwen_model,
                    "analysis_version": SEGMENT_ANALYSIS_VERSION,
                }
            )
            next_cache_records[segment.segment_id] = input_fingerprint
            if (
                cache_path.exists()
                and not self.force
                and cache_records.get(segment.segment_id) == input_fingerprint
            ):
                LOGGER.info("复用多模态分析：%s", segment.segment_id)
                analyses_by_index[index] = read_json(cache_path)
                continue

            pending.append((index, segment, cache_path))

        if pending:
            worker_count = min(
                self.settings.max_parallel_segment_analyses,
                len(pending),
            )
            self._notify(
                "multimodal_analysis",
                f"正在并发分析 {len(pending)} 个片段（并发上限：{worker_count}）",
            )

            def analyze_one(segment: Any) -> dict[str, Any]:
                # A client per thread avoids sharing a synchronous HTTP session.
                client = QwenMultimodalClient(self.settings)
                return client.analyze_segment(segment, self.run_dir)

            failures: list[tuple[str, Exception]] = []
            completed = 0
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="segment-analysis",
            ) as executor:
                futures = {
                    executor.submit(analyze_one, segment): (index, segment, cache_path)
                    for index, segment, cache_path in pending
                }
                for future in as_completed(futures):
                    index, segment, cache_path = futures[future]
                    try:
                        analysis = future.result()
                    except Exception as exc:
                        failures.append((str(segment.segment_id), exc))
                        LOGGER.exception("多模态分析失败：%s", segment.segment_id)
                    else:
                        # The main thread owns cache writes, so concurrent workers
                        # never write the same JSON file at once.
                        write_json(cache_path, analysis)
                        analyses_by_index[index] = analysis
                    completed += 1
                    self._notify(
                        "multimodal_analysis",
                        f"已完成 {completed}/{len(pending)} 个片段分析",
                    )

            if failures:
                segment_id, error = failures[0]
                raise RuntimeError(f"片段 {segment_id} 的多模态分析失败：{error}") from error

        analyses = [analyses_by_index[index] for index in range(1, len(segments) + 1)]

        write_json(self.run_dir / "analysis.json", analyses)
        write_json(cache_record_path, next_cache_records)
        return analyses

    def _verify_entities(
        self,
        metadata: dict[str, Any],
        transcript: list[TranscriptItem],
        analyses: list[dict[str, Any]],
    ) -> dict[str, Any]:
        cache_path = self.run_dir / "entity_verification.json"
        input_fingerprint = _fingerprint(
            {
                "metadata": metadata,
                "transcript": [item.to_dict() for item in transcript],
                "analyses": analyses,
                "model": self.settings.entity_verify_model,
                "confidence": self.settings.entity_verify_min_confidence,
                "verifier_version": ENTITY_VERIFIER_VERSION,
            }
        )
        if cache_path.exists() and not self.force:
            cached = read_json(cache_path)
            if cached.get("input_fingerprint") == input_fingerprint:
                LOGGER.info("复用实体联网核验结果")
                verification = cached
            else:
                verification = None
        else:
            verification = None

        if verification is None:
            if not self.settings.entity_verify_enabled:
                verification = {
                    "enabled": False,
                    "model": None,
                    "confidence_threshold": self.settings.entity_verify_min_confidence,
                    "corrections": [],
                    "unresolved": [],
                    "search_results": [],
                }
            else:
                verification = EntityVerifier(self.settings).verify(
                    metadata,
                    transcript,
                    analyses,
                )
        source_text = json.dumps(
            {
                "metadata": metadata,
                "transcript": [item.to_dict() for item in transcript],
                "analyses": analyses,
            },
            ensure_ascii=False,
        )
        verification = merge_entity_glossary(
            verification,
            self.settings.entity_glossary_path,
            source_text,
        )
        verification["input_fingerprint"] = input_fingerprint
        write_json(cache_path, verification)
        return verification

    def _apply_corrections(
        self,
        transcript: list[TranscriptItem],
        analyses: list[dict[str, Any]],
        verification: dict[str, Any],
    ) -> tuple[list[TranscriptItem], list[dict[str, Any]]]:
        corrected_transcript = correct_transcript(transcript, verification)
        corrected_analyses = correct_analyses(analyses, verification)
        write_json(
            self.run_dir / "transcript_corrected.json",
            [item.to_dict() for item in corrected_transcript],
        )
        (self.run_dir / "transcript_corrected.srt").write_text(
            transcript_to_srt(corrected_transcript),
            encoding="utf-8",
        )
        write_json(self.run_dir / "analysis_corrected.json", corrected_analyses)
        return corrected_transcript, corrected_analyses

    def _summarize(
        self,
        metadata: dict[str, Any],
        analyses: list[dict[str, Any]],
        verification: dict[str, Any],
    ) -> dict[str, Any]:
        cache_path = self.run_dir / "summary.json"
        cache_record_path = self.run_dir / "summary_cache.json"
        input_fingerprint = _fingerprint(
            {
                "title": metadata.get("title"),
                "analyses": analyses,
                "corrections": verification.get("corrections", []),
                "model": self.settings.qwen_model,
                "summary_schema_version": SUMMARY_SCHEMA_VERSION,
            }
        )
        if cache_path.exists() and cache_record_path.exists() and not self.force:
            cache_record = read_json(cache_record_path)
            if cache_record.get("input_fingerprint") == input_fingerprint:
                LOGGER.info("复用视频汇总")
                return read_json(cache_path)
        client = QwenMultimodalClient(self.settings)
        summary = client.summarize_video(
            str(metadata.get("title") or "视频分析"),
            analyses,
        )
        write_json(cache_path, summary)
        write_json(
            cache_record_path,
            {"input_fingerprint": input_fingerprint},
        )
        return summary
