from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .config import Settings
from .models import TranscriptItem
from .utils import format_timestamp


LOGGER = logging.getLogger("video_assistant")


class DashScopeASR:
    """Transcribe a local 16kHz WAV with sentence timestamps."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def transcribe(self, audio_path: Path) -> list[TranscriptItem]:
        try:
            import dashscope
            from dashscope.audio.asr import Recognition
        except ImportError as exc:
            raise RuntimeError(
                "缺少 dashscope，请先执行：pip install -r requirements.txt"
            ) from exc

        dashscope.api_key = self.settings.dashscope_api_key
        if self.settings.asr_websocket_url:
            dashscope.base_websocket_api_url = self.settings.asr_websocket_url

        LOGGER.info("调用 %s 进行语音识别", self.settings.asr_model)
        recognition_options: dict[str, Any] = {
            "model": self.settings.asr_model,
            "format": "wav",
            "sample_rate": 16000,
            "callback": None,
        }
        if self.settings.asr_model.endswith("-v2"):
            recognition_options["language_hints"] = list(
                self.settings.language_hints
            )
        recognition = Recognition(
            **recognition_options,
        )
        result = recognition.call(str(audio_path.resolve()))
        if result.status_code != HTTPStatus.OK:
            request_id = getattr(result, "request_id", None)
            message = getattr(result, "message", "未知错误")
            raise RuntimeError(f"语音识别失败（request_id={request_id}）：{message}")

        raw_sentences: Any = result.get_sentence()
        if isinstance(raw_sentences, dict):
            raw_sentences = [raw_sentences]
        if not isinstance(raw_sentences, list):
            raise RuntimeError("语音识别返回了无法解析的句子数据。")

        transcript: list[TranscriptItem] = []
        for sentence in raw_sentences:
            if not isinstance(sentence, dict):
                continue
            text = str(sentence.get("text") or "").strip()
            if not text:
                continue
            start = float(sentence.get("begin_time") or 0) / 1000
            end = float(sentence.get("end_time") or sentence.get("begin_time") or 0) / 1000
            transcript.append(
                TranscriptItem(
                    start=round(start, 3),
                    end=round(max(start, end), 3),
                    text=text,
                    speaker=str(sentence.get("speaker_id") or "SPEAKER_00"),
                )
            )

        if not transcript:
            raise RuntimeError("语音识别成功返回，但没有识别出有效文本。")
        LOGGER.info("语音识别完成，共 %d 句", len(transcript))
        return transcript


class FastDashScopeASR:
    """Transcribe short audio chunks concurrently for fast summaries."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def transcribe(
        self,
        audio_chunks: list[Path],
        duration: float,
    ) -> list[TranscriptItem]:
        if not audio_chunks:
            raise ValueError("极速转写至少需要一个音频分片。")
        worker_count = min(self.settings.fast_asr_parallelism, len(audio_chunks))
        results: dict[int, str] = {}
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="fast-asr",
        ) as executor:
            futures = {
                executor.submit(self._transcribe_chunk, path): index
                for index, path in enumerate(audio_chunks)
            }
            for future in as_completed(futures):
                index = futures[future]
                results[index] = future.result()

        transcript: list[TranscriptItem] = []
        for index in range(len(audio_chunks)):
            text = results[index].strip()
            if not text:
                continue
            start = index * self.settings.fast_asr_chunk_seconds
            end = min(duration, start + self.settings.fast_asr_chunk_seconds)
            transcript.append(
                TranscriptItem(
                    start=float(start),
                    end=float(max(start, end)),
                    text=text,
                )
            )
        if not transcript:
            raise RuntimeError("极速语音识别成功返回，但没有识别出有效文本。")
        LOGGER.info(
            "极速语音识别完成，共 %d 个分片（并发 %d）",
            len(audio_chunks),
            worker_count,
        )
        return transcript

    def _transcribe_chunk(self, audio_path: Path) -> str:
        try:
            import dashscope
        except ImportError as exc:
            raise RuntimeError(
                "缺少 dashscope，请先执行：pip install -r requirements.txt"
            ) from exc

        dashscope.api_key = self.settings.dashscope_api_key
        compatible_suffix = "/compatible-mode/v1"
        if self.settings.qwen_base_url.endswith(compatible_suffix):
            dashscope.base_http_api_url = (
                self.settings.qwen_base_url[: -len(compatible_suffix)] + "/api/v1"
            )
        response = dashscope.MultiModalConversation.call(
            api_key=self.settings.dashscope_api_key,
            model=self.settings.fast_asr_model,
            messages=[
                {
                    "role": "user",
                    "content": [{"audio": audio_path.resolve().as_uri()}],
                }
            ],
            result_format="message",
            asr_options={"enable_itn": True},
        )
        status_code = _response_field(response, "status_code")
        if status_code is not None and int(status_code) != HTTPStatus.OK:
            request_id = _response_field(response, "request_id")
            message = _response_field(response, "message") or "未知错误"
            raise RuntimeError(
                f"极速语音识别失败（request_id={request_id}）：{message}"
            )
        text = _extract_multimodal_text(response)
        if not text:
            raise RuntimeError(f"极速语音识别未返回文本：{audio_path.name}")
        return text


def _response_field(response: Any, name: str) -> Any:
    if isinstance(response, dict):
        return response.get(name)
    return getattr(response, name, None)


def _extract_multimodal_text(response: Any) -> str:
    output = _response_field(response, "output") or {}
    choices = output.get("choices") if isinstance(output, dict) else getattr(output, "choices", None)
    if not choices:
        return ""
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)
    if message is None:
        return ""
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
        if text:
            parts.append(str(text).strip())
    return "\n".join(part for part in parts if part)


def transcript_to_srt(transcript: list[TranscriptItem]) -> str:
    blocks: list[str] = []
    for index, item in enumerate(transcript, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    (
                        f"{format_timestamp(item.start, include_millis=True)} --> "
                        f"{format_timestamp(item.end, include_millis=True)}"
                    ),
                    item.text,
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"
