from __future__ import annotations

import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from video_assistant.asr import FastDashScopeASR
from video_assistant.config import Settings
from video_assistant.media import (
    AcquiredMedia,
    acquire_media,
    acquire_video,
    load_downloaded_subtitles,
)
from video_assistant.modes import (
    AnalysisMode,
    FAST_AUDIO_FORMAT,
    STANDARD_VIDEO_FORMAT,
    build_mode_profile,
)
from video_assistant.models import TranscriptItem, VideoFrame, VideoSegment
from video_assistant.pipeline import VideoAnalysisPipeline


def make_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        output_root=root / "runs",
        dashscope_api_key="test-key",
        qwen_model="test-model",
        qwen_base_url="https://example.invalid/v1",
        asr_model="test-asr-v2",
        asr_websocket_url="wss://example.invalid",
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
        ytdlp_format="bv*[vcodec^=avc1]+ba/b[vcodec^=avc1]/bv*+ba/b",
        entity_verify_enabled=False,
        entity_verify_model="qwen-plus",
        entity_verify_search_strategy="turbo",
        entity_verify_min_confidence=0.85,
        entity_verify_max_entities=20,
        entity_glossary_path=root / "entity_glossary.json",
        segment_seconds=10,
        max_parallel_segment_analyses=2,
        scan_interval_seconds=1,
        periodic_frame_seconds=5,
        min_scene_gap_seconds=2,
        scene_threshold=0.2,
        max_frames=20,
        max_images_per_segment=4,
        image_max_width=640,
        request_timeout_seconds=30,
        download_attempts=3,
        download_retries_per_attempt=2,
        download_socket_timeout_seconds=60,
        fast_summary_enabled=False,
        fast_summary_model="fast-test-model",
        fast_summary_max_transcript_chars=24_000,
        fast_asr_model="fast-asr-test-model",
        fast_asr_chunk_seconds=180,
        fast_asr_parallelism=4,
        language_hints=("zh", "en"),
    )


class FakeASR:
    def __init__(self, settings: Settings):
        self.settings = settings

    def transcribe(self, audio_path: Path) -> list[TranscriptItem]:
        return [
            TranscriptItem(start=0, end=8, text="第一段"),
            TranscriptItem(start=12, end=18, text="第二段"),
        ]


class FakeQwen:
    def __init__(self, settings: Settings):
        self.settings = settings

    def analyze_segment(self, segment, run_dir: Path):
        return {
            "segment_id": segment.segment_id,
            "start": segment.start,
            "end": segment.end,
            "title": segment.segment_id,
            "summary": "mock summary",
            "key_points": ["point"],
            "keywords": ["mock"],
            "visual_events": [],
            "on_screen_text": [],
            "transcript_corrections": [],
            "importance_score": 0.5,
            "representative_frame": segment.frames[0].path,
            "evidence_frames": [segment.frames[0].to_dict()],
        }

    def summarize_video(self, title: str, segment_analyses):
        return {
            "title": title,
            "overview": "mock overview",
            "takeaways": ["takeaway"],
            "chapters": [
                {
                    "title": item["title"],
                    "start": item["start"],
                    "end": item["end"],
                    "summary": item["summary"],
                    "key_points": item["key_points"],
                    "keywords": item["keywords"],
                    "segment_ids": [item["segment_id"]],
                }
                for item in segment_analyses
            ],
        }


class FastASRTests(unittest.TestCase):
    def test_extracts_text_from_dashscope_multimodal_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "chunk.wav"
            audio.write_bytes(b"wav")
            settings = make_settings(root)

            class FakeConversation:
                @staticmethod
                def call(**_kwargs):
                    return {
                        "status_code": 200,
                        "output": {
                            "choices": [
                                {"message": {"content": [{"text": "识别文本"}]}}
                            ]
                        },
                    }

            fake_dashscope = types.SimpleNamespace(
                api_key=None,
                base_http_api_url=None,
                MultiModalConversation=FakeConversation,
            )
            with patch.dict(sys.modules, {"dashscope": fake_dashscope}):
                text = FastDashScopeASR(settings)._transcribe_chunk(audio)

        self.assertEqual(text, "识别文本")

class MediaAcquisitionTests(unittest.TestCase):
    def test_fast_mode_stops_after_platform_subtitle_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            settings = make_settings(root)
            profile = build_mode_profile(settings, AnalysisMode.FAST)
            calls = 0

            class FakeYoutubeDL:
                def __init__(self, options):
                    self.options = options

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def extract_info(self, _source, download):
                    nonlocal calls
                    calls += 1
                    self.assert_download(download)
                    subtitle = run_dir / "source" / "source.zh-Hans.vtt"
                    subtitle.parent.mkdir(parents=True, exist_ok=True)
                    subtitle.write_text(
                        "WEBVTT\n\n00:00:00.000 --> 00:00:10.000\n字幕内容\n",
                        encoding="utf-8",
                    )
                    return {"title": "Subtitle video", "duration": 10, "id": "BVtest"}

                @staticmethod
                def assert_download(value):
                    if value is not True:
                        raise AssertionError("yt-dlp should process subtitle output")

            fake_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
            with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                acquired = acquire_media(
                    "https://example.invalid/video",
                    run_dir,
                    settings,
                    profile,
                )

        self.assertEqual(calls, 1)
        self.assertEqual(acquired.kind, "subtitle_only")
        self.assertIsNone(acquired.path)

    def test_fast_mode_downloads_audio_only_when_subtitles_are_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            settings = make_settings(root)
            profile = build_mode_profile(settings, AnalysisMode.FAST)
            options_seen: list[dict] = []

            class FakeYoutubeDL:
                def __init__(self, options):
                    self.options = options
                    options_seen.append(options)

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def extract_info(self, _source, download):
                    if not self.options.get("skip_download"):
                        audio = run_dir / "source" / "source.m4a"
                        audio.parent.mkdir(parents=True, exist_ok=True)
                        audio.write_bytes(b"audio")
                    return {"title": "Audio video", "duration": 10, "id": "BVtest"}

            fake_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
            with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                acquired = acquire_media(
                    "https://example.invalid/video",
                    run_dir,
                    settings,
                    profile,
                )

        self.assertEqual(len(options_seen), 2)
        self.assertTrue(options_seen[0]["skip_download"])
        self.assertEqual(options_seen[1]["format"], FAST_AUDIO_FORMAT)
        self.assertFalse(options_seen[1]["writesubtitles"])
        self.assertEqual(acquired.kind, "audio")
        self.assertEqual(acquired.path.suffix, ".m4a")

    def test_passes_configured_ffmpeg_directory_to_ytdlp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            ffmpeg = root / "tools" / "ffmpeg.exe"
            ffmpeg.parent.mkdir(parents=True)
            ffmpeg.write_bytes(b"fake")
            settings = make_settings(root)
            settings.ffmpeg_bin = str(ffmpeg)
            captured_options = {}

            class FakeYoutubeDL:
                def __init__(self, options):
                    captured_options.update(options)

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def extract_info(self, _source, download):
                    self_path = Path(captured_options["outtmpl"].replace("%(ext)s", "mp4"))
                    self_path.parent.mkdir(parents=True, exist_ok=True)
                    self_path.write_bytes(b"video")
                    return {"title": "Mock download", "id": "BVtest"}

            fake_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
            with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                video_path, _ = acquire_video(
                    "https://example.invalid/video",
                    run_dir,
                    settings,
                )

            self.assertEqual(
                captured_options["ffmpeg_location"],
                str(ffmpeg.resolve().parent),
            )
            self.assertEqual(captured_options["format"], settings.ytdlp_format)
            self.assertFalse(captured_options["overwrites"])
            self.assertTrue(captured_options["continuedl"])
            self.assertEqual(captured_options["socket_timeout"], 60)
            self.assertTrue(captured_options["writesubtitles"])
            self.assertTrue(video_path.exists())

    def test_prefers_chinese_vtt_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "source.en.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nEnglish\n",
                encoding="utf-8",
            )
            (directory / "source.zh-Hans.vtt").write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\n<c>中文字幕</c>\n",
                encoding="utf-8",
            )
            transcript = load_downloaded_subtitles(directory)

        self.assertEqual(len(transcript), 1)
        self.assertEqual(transcript[0].text, "中文字幕")
        self.assertEqual(transcript[0].start, 1.0)
        self.assertEqual(transcript[0].end, 3.5)

    def test_refreshes_download_url_after_transient_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            settings = make_settings(root)
            calls = 0

            class FakeYoutubeDL:
                def __init__(self, _options):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def extract_info(self, _source, download):
                    nonlocal calls
                    calls += 1
                    if calls < 3:
                        raise RuntimeError("CDN read timed out")
                    output = run_dir / "source" / "source.mp4"
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b"video")
                    return {"title": "Recovered download", "id": "BVtest"}

            fake_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
            with (
                patch.dict(sys.modules, {"yt_dlp": fake_module}),
                patch("video_assistant.media.time.sleep") as sleep,
            ):
                video_path, metadata = acquire_video(
                    "https://example.invalid/video",
                    run_dir,
                    settings,
                )

            self.assertEqual(calls, 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(metadata["title"], "Recovered download")
            self.assertTrue(video_path.exists())


class ConfigurationTests(unittest.TestCase):
    def test_mode_profiles_keep_fast_standard_and_detailed_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = make_settings(Path(temporary))
            fast = build_mode_profile(settings, "fast", verify_entities=True)
            standard = build_mode_profile(settings, "standard", verify_entities=True)
            detailed = build_mode_profile(settings, "detailed", verify_entities=False)

        self.assertTrue(fast.text_only)
        self.assertFalse(fast.verify_entities)
        self.assertEqual(fast.download_format, FAST_AUDIO_FORMAT)
        self.assertEqual(standard.download_format, STANDARD_VIDEO_FORMAT)
        self.assertEqual(standard.segment_seconds, 180)
        self.assertTrue(standard.verify_entities)
        self.assertEqual(detailed.download_format, settings.ytdlp_format)
        self.assertFalse(detailed.verify_entities)

    def test_pipeline_mode_settings_do_not_mutate_shared_base_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = make_settings(root)
            original_model = settings.qwen_model
            original_segment_seconds = settings.segment_seconds
            fast = VideoAnalysisPipeline(settings, root / "fast", mode="fast")
            standard = VideoAnalysisPipeline(settings, root / "standard", mode="standard")

        self.assertEqual(fast.settings.qwen_model, settings.fast_summary_model)
        self.assertEqual(standard.settings.segment_seconds, 180)
        self.assertEqual(settings.qwen_model, original_model)
        self.assertEqual(settings.segment_seconds, original_segment_seconds)
        self.assertFalse(settings.fast_summary_enabled)

    def test_default_segment_length_is_sixty_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict("os.environ", {}, clear=True):
                settings = Settings.from_env(Path(temporary))
        self.assertEqual(settings.segment_seconds, 60)
        self.assertEqual(settings.max_parallel_segment_analyses, 2)


class AnalysisCacheTests(unittest.TestCase):
    def test_analyzes_segments_concurrently_and_preserves_time_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            settings = make_settings(root)
            started_together = threading.Event()
            release = threading.Event()
            lock = threading.Lock()
            active = 0
            maximum_active = 0

            class ConcurrentQwen:
                def __init__(self, _settings):
                    pass

                def analyze_segment(self, segment, _run_dir):
                    nonlocal active, maximum_active
                    with lock:
                        active += 1
                        maximum_active = max(maximum_active, active)
                        if active >= 2:
                            started_together.set()
                    release.wait(timeout=1)
                    with lock:
                        active -= 1
                    return {
                        "segment_id": segment.segment_id,
                        "start": segment.start,
                        "end": segment.end,
                        "title": segment.segment_id,
                        "summary": segment.segment_id,
                    }

            segments = [
                VideoSegment(f"seg_{index:04d}", index * 10, (index + 1) * 10)
                for index in range(3)
            ]
            pipeline = VideoAnalysisPipeline(settings, run_dir)
            with patch(
                "video_assistant.pipeline.QwenMultimodalClient",
                ConcurrentQwen,
            ):
                def unblock_when_parallel() -> None:
                    started_together.wait(timeout=1)
                    release.set()

                unblocker = threading.Thread(target=unblock_when_parallel)
                unblocker.start()
                analyses = pipeline._analyze_segments(segments)
                unblocker.join(timeout=1)

        self.assertGreaterEqual(maximum_active, 2)
        self.assertLessEqual(maximum_active, 2)
        self.assertEqual(
            [item["segment_id"] for item in analyses],
            ["seg_0000", "seg_0001", "seg_0002"],
        )

    def test_segment_change_invalidates_cached_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            run_dir.mkdir()
            frame_dir = run_dir / "frames"
            frame_dir.mkdir()
            (frame_dir / "one.jpg").write_bytes(b"jpg")
            settings = make_settings(root)
            calls: list[tuple[float, float]] = []

            class CountingQwen:
                def __init__(self, _settings):
                    pass

                def analyze_segment(self, segment, _run_dir):
                    calls.append((segment.start, segment.end))
                    return {
                        "segment_id": segment.segment_id,
                        "start": segment.start,
                        "end": segment.end,
                        "title": "segment",
                        "summary": "summary",
                    }

            first = VideoSegment(
                "seg_0001",
                0,
                10,
                frames=[VideoFrame(2, "frames/one.jpg", "periodic")],
            )
            changed = VideoSegment(
                "seg_0001",
                0,
                20,
                frames=[VideoFrame(2, "frames/one.jpg", "periodic")],
            )
            pipeline = VideoAnalysisPipeline(settings, run_dir)
            with patch(
                "video_assistant.pipeline.QwenMultimodalClient",
                CountingQwen,
            ):
                pipeline._analyze_segments([first])
                pipeline._analyze_segments([first])
                pipeline._analyze_segments([changed])

        self.assertEqual(calls, [(0, 10), (0, 20)])


class PipelineOrchestrationTests(unittest.TestCase):
    def test_fast_mode_uses_parallel_chunk_asr_without_video_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            audio = root / "source.m4a"
            audio.write_bytes(b"audio")
            settings = make_settings(root)
            chunks = [root / "chunk_000.wav", root / "chunk_001.wav"]
            for chunk in chunks:
                chunk.write_bytes(b"wav")

            class FakeFastASR:
                def __init__(self, configured_settings):
                    self.settings = configured_settings

                def transcribe(self, received_chunks, duration):
                    self.assert_inputs(received_chunks, duration)
                    return [TranscriptItem(start=0, end=20, text="并行转写内容")]

                @staticmethod
                def assert_inputs(received_chunks, duration):
                    if received_chunks != chunks or duration != 20:
                        raise AssertionError("unexpected fast ASR inputs")

            with (
                patch(
                    "video_assistant.pipeline.acquire_media",
                    return_value=AcquiredMedia(
                        audio,
                        "audio",
                        {"title": "Fast audio", "source": "mock"},
                    ),
                ),
                patch(
                    "video_assistant.pipeline.probe_video",
                    return_value={"duration_seconds": 20, "format": {}, "streams": []},
                ),
                patch(
                    "video_assistant.pipeline.load_downloaded_subtitles",
                    return_value=[],
                ),
                patch(
                    "video_assistant.pipeline.extract_audio_chunks",
                    return_value=chunks,
                ) as extract_chunks,
                patch("video_assistant.pipeline.FastDashScopeASR", FakeFastASR),
                patch("video_assistant.pipeline.DashScopeASR") as legacy_asr,
                patch("video_assistant.pipeline.sample_video_frames") as sample_frames,
                patch("video_assistant.pipeline.QwenMultimodalClient", FakeQwen),
            ):
                report = VideoAnalysisPipeline(
                    settings,
                    run_dir,
                    mode="fast",
                    verify_entities=True,
                ).run("https://example.invalid/video")

            self.assertTrue(report.exists())
            extract_chunks.assert_called_once()
            legacy_asr.assert_not_called()
            sample_frames.assert_not_called()
            metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["analysis_mode"], "fast")
            self.assertEqual(metadata["media_kind"], "audio")
            self.assertEqual(metadata["qwen_model"], settings.fast_summary_model)

    def test_fast_mode_uses_subtitles_without_asr_or_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            video = root / "source.mp4"
            video.write_bytes(b"fake")
            settings = make_settings(root)
            settings.fast_summary_enabled = True
            settings.qwen_model = settings.fast_summary_model
            (run_dir / "source").mkdir(parents=True)
            subtitles = [TranscriptItem(start=0, end=10, text="平台字幕内容")]

            with (
                patch(
                    "video_assistant.pipeline.acquire_media",
                    return_value=AcquiredMedia(
                        video,
                        "video",
                        {"title": "Mock video", "source": str(video)},
                    ),
                ),
                patch(
                    "video_assistant.pipeline.probe_video",
                    return_value={"duration_seconds": 10, "format": {}, "streams": []},
                ),
                patch(
                    "video_assistant.pipeline.load_downloaded_subtitles",
                    return_value=subtitles,
                ),
                patch("video_assistant.pipeline.extract_audio") as extract_audio,
                patch("video_assistant.pipeline.DashScopeASR") as asr,
                patch("video_assistant.pipeline.sample_video_frames") as sample_frames,
                patch("video_assistant.pipeline.QwenMultimodalClient", FakeQwen),
            ):
                report = VideoAnalysisPipeline(settings, run_dir).run(str(video))

            self.assertTrue(report.exists())
            extract_audio.assert_not_called()
            asr.assert_not_called()
            sample_frames.assert_not_called()
            metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["transcript_source"], "platform_subtitle")
            analyses = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(len(analyses), 1)
            self.assertIn("平台字幕内容", analyses[0]["summary"])

    def test_runs_all_steps_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            video = root / "source.mp4"
            video.write_bytes(b"fake")
            settings = make_settings(root)
            run_dir.mkdir(parents=True)
            (run_dir / "state.json").write_text(
                json.dumps(
                    {
                        "source_input": str(video),
                        "status": "failed",
                        "error": "old failure",
                        "failed_at": "2026-01-01T00:00:00",
                        "steps": {},
                    }
                ),
                encoding="utf-8",
            )

            def fake_extract(video_path: Path, audio_path: Path, settings: Settings):
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(b"wav")
                return audio_path

            def fake_frames(video_path: Path, run_dir: Path, duration: float, settings):
                frame_dir = run_dir / "frames"
                frame_dir.mkdir(parents=True, exist_ok=True)
                first = frame_dir / "one.jpg"
                second = frame_dir / "two.jpg"
                first.write_bytes(b"jpg")
                second.write_bytes(b"jpg")
                return [
                    VideoFrame(2, "frames/one.jpg", "periodic"),
                    VideoFrame(12, "frames/two.jpg", "periodic"),
                ]

            with (
                patch(
                    "video_assistant.pipeline.acquire_media",
                    return_value=AcquiredMedia(
                        video,
                        "video",
                        {"title": "Mock video", "source": str(video)},
                    ),
                ),
                patch(
                    "video_assistant.pipeline.probe_video",
                    return_value={"duration_seconds": 20, "format": {}, "streams": []},
                ),
                patch(
                    "video_assistant.pipeline.extract_audio",
                    side_effect=fake_extract,
                ),
                patch("video_assistant.pipeline.DashScopeASR", FakeASR),
                patch(
                    "video_assistant.pipeline.sample_video_frames",
                    side_effect=fake_frames,
                ),
                patch("video_assistant.pipeline.QwenMultimodalClient", FakeQwen),
            ):
                report = VideoAnalysisPipeline(settings, run_dir).run(str(video))

            self.assertTrue(report.exists())
            self.assertIn("Mock video", report.read_text(encoding="utf-8"))
            self.assertTrue((run_dir / "transcript_corrected.json").exists())
            self.assertTrue((run_dir / "analysis_corrected.json").exists())
            self.assertTrue((run_dir / "entity_verification.json").exists())
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertNotIn("failed_at", state)
            self.assertEqual(len(json.loads((run_dir / "analysis.json").read_text())), 2)


if __name__ == "__main__":
    unittest.main()
