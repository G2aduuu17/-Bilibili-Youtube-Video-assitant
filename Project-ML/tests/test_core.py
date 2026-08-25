from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_assistant.asr import transcript_to_srt
from video_assistant.entities import (
    apply_entity_corrections,
    correct_analyses,
    merge_entity_glossary,
    normalize_entity_verification,
)
from video_assistant.models import (
    TranscriptItem,
    VideoFrame,
    build_segments,
)
from video_assistant.qwen import _evenly_select_frames, _normalize_video_summary
from video_assistant.report import render_markdown_report
from video_assistant.utils import format_timestamp, parse_json_payload


class JsonParsingTests(unittest.TestCase):
    def test_parses_markdown_fence(self) -> None:
        value = parse_json_payload('```json\n{"title": "ok"}\n```')
        self.assertEqual(value["title"], "ok")

    def test_rejects_non_object(self) -> None:
        with self.assertRaises(ValueError):
            parse_json_payload("[1, 2, 3]")


class EntityCorrectionTests(unittest.TestCase):
    def test_accepts_supported_entity_and_replaces_nested_text(self) -> None:
        verification = normalize_entity_verification(
            payload={
                "corrections": [
                    {
                        "original": "蓝老",
                        "canonical": "揽佬",
                        "aliases": ["SKAI ISYOURGOD"],
                        "entity_type": "person",
                        "confidence": 0.98,
                        "reason": "搜索证据支持",
                        "source_indexes": [1],
                    }
                ]
            },
            search_results=[
                {"index": 1, "title": "source", "url": "https://example.com"}
            ],
            source_text="视频提到蓝老和他的歌曲。",
            confidence_threshold=0.85,
            max_entities=20,
        )

        corrected = apply_entity_corrections(
            {"summary": "蓝老演唱", "keywords": ["蓝老"]},
            verification,
        )
        self.assertTrue(verification["corrections"][0]["accepted"])
        self.assertEqual(corrected["summary"], "揽佬演唱")
        self.assertEqual(corrected["keywords"], ["揽佬"])
        self.assertEqual(
            apply_entity_corrections("电台on the Rader演唱", {
                "corrections": [
                    {
                        "original": "on the Rader",
                        "canonical": "On The Radar",
                        "accepted": True,
                    }
                ]
            }),
            "电台On The Radar演唱",
        )

    def test_does_not_apply_low_confidence_or_absent_entity(self) -> None:
        verification = normalize_entity_verification(
            payload={
                "corrections": [
                    {
                        "original": "蓝老",
                        "canonical": "揽佬",
                        "confidence": 0.5,
                    },
                    {
                        "original": "不存在",
                        "canonical": "不应加入",
                        "confidence": 1.0,
                    },
                ]
            },
            search_results=[],
            source_text="蓝老",
            confidence_threshold=0.85,
            max_entities=20,
        )
        self.assertEqual(
            apply_entity_corrections("蓝老", verification),
            "蓝老",
        )
        self.assertEqual(len(verification["corrections"]), 1)

    def test_removes_stale_segment_correction_after_web_verification(self) -> None:
        analyses = [
            {
                "summary": "蓝老（Lil Boi）演唱",
                "transcript_corrections": [
                    {
                        "original": "蓝老",
                        "corrected": "Lil Boi / 蓝老",
                        "reason": "错误地关联成韩国歌手",
                    }
                ],
            }
        ]
        verification = {
            "corrections": [
                {
                    "original": "蓝老",
                    "canonical": "揽佬",
                    "accepted": True,
                },
                {
                    "original": "Lil Boi",
                    "canonical": "SKAI ISYOURGOD",
                    "accepted": True,
                },
            ]
        }
        corrected = correct_analyses(analyses, verification)
        self.assertEqual(
            corrected[0]["summary"],
            "揽佬（SKAI ISYOURGOD）演唱",
        )
        self.assertEqual(corrected[0]["transcript_corrections"], [])

    def test_project_glossary_overrides_search_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "entities.json"
            path.write_text(
                (
                    '{"corrections": [{"original": "Lil Boi", '
                    '"canonical": "SKAI ISYOURGOD", '
                    '"entity_type": "person"}]}'
                ),
                encoding="utf-8",
            )
            merged = merge_entity_glossary(
                {
                    "corrections": [
                        {
                            "original": "Lil Boi",
                            "canonical": "错误搜索结果",
                            "confidence": 0.99,
                            "accepted": True,
                        }
                    ]
                },
                path,
                "视频错误关联为Lil Boi。",
            )
        correction = merged["corrections"][0]
        self.assertEqual(correction["canonical"], "SKAI ISYOURGOD")
        self.assertEqual(correction["source_type"], "project_glossary")
        self.assertEqual(correction["confidence"], 1.0)


class SegmentationTests(unittest.TestCase):
    def test_attaches_overlapping_transcript_and_frames(self) -> None:
        transcript = [
            TranscriptItem(start=0, end=8, text="intro"),
            TranscriptItem(start=9, end=12, text="crosses"),
            TranscriptItem(start=15, end=20, text="second"),
        ]
        frames = [
            VideoFrame(timestamp=2, path="frames/a.jpg", reason="periodic"),
            VideoFrame(timestamp=18, path="frames/b.jpg", reason="scene"),
        ]
        segments = build_segments(20, 10, transcript, frames)
        self.assertEqual(len(segments), 2)
        self.assertEqual(
            [item.text for item in segments[0].transcript],
            ["intro", "crosses"],
        )
        self.assertEqual(
            [item.text for item in segments[1].transcript],
            ["crosses", "second"],
        )
        self.assertEqual(segments[1].frames[0].path, "frames/b.jpg")

    def test_uses_nearest_frame_when_segment_has_none(self) -> None:
        frames = [VideoFrame(timestamp=1, path="frames/a.jpg", reason="periodic")]
        segments = build_segments(20, 10, [], frames)
        self.assertEqual(segments[1].frames[0].path, "frames/a.jpg")


class EvidenceSelectionTests(unittest.TestCase):
    def test_limits_images_and_keeps_time_order(self) -> None:
        frames = [
            VideoFrame(
                timestamp=float(index),
                path=f"frames/{index}.jpg",
                reason="scene",
                scene_score=0.9 if index == 5 else 0.1,
            )
            for index in range(10)
        ]
        selected = _evenly_select_frames(frames, 4)
        self.assertEqual(len(selected), 4)
        self.assertEqual(selected, sorted(selected, key=lambda item: item.timestamp))
        self.assertIn(frames[5], selected)


class SummaryNormalizationTests(unittest.TestCase):
    def test_drops_transcript_shaped_and_excess_takeaways(self) -> None:
        analyses = [
            {
                "segment_id": "seg_0001",
                "start": 0,
                "end": 10,
                "title": "part",
                "summary": "fallback summary",
            }
        ]
        valid = [
            {
                "text": f"第 {index} 条有效结论。",
                "evidence": [{"segment_id": "seg_0001", "timestamp": 0}],
            }
            for index in range(1, 8)
        ]
        value = _normalize_video_summary(
            {
                "takeaways": [
                    *valid,
                    {
                        "text": "[0.00-8.76] 这是一段不应出现在核心结论中的转写原文。",
                        "evidence": [],
                    },
                    {"text": "很长" * 61, "evidence": []},
                ]
            },
            "fallback",
            analyses,
        )
        self.assertEqual(len(value["takeaways"]), 6)
        self.assertTrue(
            all("[0.00-8.76]" not in item["text"] for item in value["takeaways"])
        )

    def test_preserves_segments_omitted_by_model(self) -> None:
        analyses = [
            {
                "segment_id": "seg_0001",
                "start": 0,
                "end": 10,
                "title": "one",
                "summary": "first",
            },
            {
                "segment_id": "seg_0002",
                "start": 10,
                "end": 20,
                "title": "two",
                "summary": "second",
            },
        ]
        value = _normalize_video_summary(
            {
                "chapters": [
                    {
                        "title": "chapter",
                        "segment_ids": ["seg_0001"],
                    }
                ]
            },
            "fallback",
            analyses,
        )
        used_ids = [
            segment_id
            for chapter in value["chapters"]
            for segment_id in chapter["segment_ids"]
        ]
        self.assertEqual(used_ids, ["seg_0001", "seg_0002"])

    def test_normalizes_evidence_and_adds_quality_metrics(self) -> None:
        analyses = [
            {
                "segment_id": f"seg_{index:04d}",
                "start": (index - 1) * 60,
                "end": index * 60,
                "title": f"part {index}",
                "summary": f"第 {index} 段的摘要内容",
                "key_points": [f"第 {index} 段要点"],
                "importance_score": 1 - index / 10,
                "visual_events": [
                    {
                        "timestamp": (index - 1) * 60 + 10,
                        "description": f"第 {index} 段画面证据",
                    }
                ],
            }
            for index in range(1, 4)
        ]
        value = _normalize_video_summary(
            {
                "content_type": "course",
                "overview": "这是一段足够详细的总体摘要。" * 20,
                "central_thesis": "中心信息",
                "takeaways": [
                    {
                        "text": "第一条结论",
                        "evidence": [
                            {
                                "segment_id": "seg_0001",
                                "timestamp": 999,
                                "evidence_type": "speech",
                                "description": "原话支持",
                            }
                        ],
                    }
                ],
                "final_synthesis": "这是完整的最终综合结论。" * 12,
                "chapters": [
                    {
                        "title": "前两段",
                        "segment_ids": ["seg_0001", "seg_0002"],
                    }
                ],
            },
            "fallback",
            analyses,
        )
        self.assertEqual(value["content_type"], "course")
        self.assertEqual(value["takeaways"][0]["evidence"][0]["timestamp"], 60)
        self.assertGreaterEqual(len(value["takeaways"]), 3)
        self.assertEqual(value["quality"]["segment_coverage"], 1.0)
        used_ids = {
            segment_id
            for chapter in value["chapters"]
            for segment_id in chapter["segment_ids"]
        }
        self.assertEqual(
            used_ids,
            {"seg_0001", "seg_0002", "seg_0003"},
        )


class ReportRenderingTests(unittest.TestCase):
    def test_hides_evidence_and_renders_final_synthesis(self) -> None:
        summary = {
            "title": "测试报告",
            "content_type": "tutorial",
            "overview": "总体摘要",
            "central_thesis": "先配置环境，再执行操作。",
            "takeaways": [
                {
                    "text": "必须先完成配置。",
                    "evidence": [
                        {
                            "segment_id": "seg_0001",
                            "timestamp": 12,
                            "evidence_type": "screen_text",
                            "description": "画面展示了配置项。",
                        }
                    ],
                }
            ],
            "chapters": [],
            "final_synthesis": "最终综合结论。",
            "quality": {"warnings": []},
        }
        content = render_markdown_report(
            {
                "title": "测试视频",
                "source": "local.mp4",
                "duration_seconds": 30,
                "qwen_model": "test-model",
            },
            summary,
            [],
        )
        self.assertIn("内容类型：教程 / 操作演示", content)
        self.assertNotIn("seg_0001", content)
        self.assertNotIn("证据片段", content)
        self.assertIn("## 最终综合结论", content)


class SubtitleTests(unittest.TestCase):
    def test_writes_srt_timestamps(self) -> None:
        content = transcript_to_srt(
            [TranscriptItem(start=1.25, end=2.5, text="你好")]
        )
        self.assertIn("00:00:01,250 --> 00:00:02,500", content)
        self.assertIn("你好", content)

    def test_timestamp_rounds_across_minute_boundary(self) -> None:
        self.assertEqual(
            format_timestamp(59.9996, include_millis=True),
            "00:01:00,000",
        )


if __name__ == "__main__":
    unittest.main()
