from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .config import Settings


class AnalysisMode(str, Enum):
    FAST = "fast"
    STANDARD = "standard"
    DETAILED = "detailed"

    @classmethod
    def parse(cls, value: str | "AnalysisMode" | None) -> "AnalysisMode":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.DETAILED
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            supported = ", ".join(mode.value for mode in cls)
            raise ValueError(f"不支持的分析模式：{value}；可选值：{supported}") from exc


@dataclass(frozen=True, slots=True)
class ModeProfile:
    mode: AnalysisMode
    download_format: str
    subtitle_short_circuit: bool
    use_frames: bool
    use_multimodal: bool
    verify_entities: bool
    segment_seconds: int
    summary_model: str

    @property
    def text_only(self) -> bool:
        return not self.use_frames and not self.use_multimodal


STANDARD_VIDEO_FORMAT = (
    "bv*[height<=720][vcodec^=avc1]+ba/"
    "b[height<=720][vcodec^=avc1]/"
    "bv*[height<=720]+ba/b[height<=720]"
)
FAST_AUDIO_FORMAT = "ba[acodec^=mp4a]/ba/b"


def build_mode_profile(
    settings: Settings,
    mode: str | AnalysisMode | None,
    verify_entities: bool | None = None,
) -> ModeProfile:
    resolved = AnalysisMode.parse(mode)
    requested_verification = (
        settings.entity_verify_enabled
        if verify_entities is None
        else bool(verify_entities)
    )

    if resolved is AnalysisMode.FAST:
        return ModeProfile(
            mode=resolved,
            download_format=FAST_AUDIO_FORMAT,
            subtitle_short_circuit=True,
            use_frames=False,
            use_multimodal=False,
            verify_entities=False,
            segment_seconds=0,
            summary_model=settings.fast_summary_model,
        )
    if resolved is AnalysisMode.STANDARD:
        return ModeProfile(
            mode=resolved,
            download_format=STANDARD_VIDEO_FORMAT,
            subtitle_short_circuit=False,
            use_frames=True,
            use_multimodal=True,
            verify_entities=requested_verification,
            segment_seconds=max(settings.segment_seconds, 180),
            summary_model=settings.qwen_model,
        )
    return ModeProfile(
        mode=resolved,
        download_format=settings.ytdlp_format,
        subtitle_short_circuit=False,
        use_frames=True,
        use_multimodal=True,
        verify_entities=requested_verification,
        segment_seconds=settings.segment_seconds,
        summary_model=settings.qwen_model,
    )


def settings_for_profile(settings: Settings, profile: ModeProfile) -> Settings:
    """Return task-local settings so one mode cannot mutate another task."""
    return replace(
        settings,
        qwen_model=profile.summary_model,
        entity_verify_enabled=profile.verify_entities,
        segment_seconds=(
            profile.segment_seconds
            if profile.segment_seconds > 0
            else settings.segment_seconds
        ),
        fast_summary_enabled=profile.mode is AnalysisMode.FAST,
    )
