from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class TranscriptItem:
    start: float
    end: float
    text: str
    speaker: str = "SPEAKER_00"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VideoFrame:
    timestamp: float
    path: str
    reason: str
    scene_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VideoSegment:
    segment_id: str
    start: float
    end: float
    transcript: list[TranscriptItem] = field(default_factory=list)
    frames: list[VideoFrame] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "start": self.start,
            "end": self.end,
            "transcript": [item.to_dict() for item in self.transcript],
            "frames": [frame.to_dict() for frame in self.frames],
        }


def build_segments(
    duration: float,
    segment_seconds: int,
    transcript: list[TranscriptItem],
    frames: list[VideoFrame],
) -> list[VideoSegment]:
    """Build fixed-duration segments and attach overlapping evidence."""
    if duration <= 0:
        raise ValueError("Video duration must be greater than zero.")
    if segment_seconds <= 0:
        raise ValueError("segment_seconds must be greater than zero.")

    segments: list[VideoSegment] = []
    start = 0.0
    index = 1
    while start < duration:
        end = min(start + segment_seconds, duration)
        segment_transcript = [
            item for item in transcript if item.end > start and item.start < end
        ]
        segment_frames = [
            frame for frame in frames if start <= frame.timestamp < end
        ]

        if not segment_frames and frames:
            midpoint = (start + end) / 2
            segment_frames = [min(frames, key=lambda frame: abs(frame.timestamp - midpoint))]

        segments.append(
            VideoSegment(
                segment_id=f"seg_{index:04d}",
                start=start,
                end=end,
                transcript=segment_transcript,
                frames=segment_frames,
            )
        )
        start = end
        index += 1
    return segments
