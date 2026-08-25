from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .config import Settings
from .models import TranscriptItem
from .utils import parse_json_payload


LOGGER = logging.getLogger("video_assistant")
ENTITY_VERIFIER_VERSION = "2"


def _plain_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    if hasattr(value, "items"):
        return {
            str(key): _plain_value(item)
            for key, item in value.items()
        }
    return value


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _bounded_context(
    metadata: dict[str, Any],
    transcript: list[TranscriptItem],
    analyses: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata_context = {
        key: metadata.get(key)
        for key in (
            "title",
            "uploader",
            "description",
            "tags",
            "categories",
            "webpage_url",
        )
        if metadata.get(key)
    }
    transcript_text = "\n".join(
        f"[{item.start:.2f}-{item.end:.2f}] {item.text}"
        for item in transcript
    )
    compact_analyses = [
        {
            key: item.get(key)
            for key in (
                "segment_id",
                "title",
                "summary",
                "key_points",
                "keywords",
                "on_screen_text",
                "named_entities",
                "transcript_corrections",
            )
            if item.get(key)
        }
        for item in analyses
    ]
    analyses_text = json.dumps(compact_analyses, ensure_ascii=False)
    return {
        "video_metadata": metadata_context,
        "transcript": transcript_text[:24_000],
        "multimodal_analysis": analyses_text[:36_000],
    }


def _normalize_search_results(output: dict[str, Any]) -> list[dict[str, Any]]:
    search_info = output.get("search_info")
    if not isinstance(search_info, dict):
        return []
    raw_results = search_info.get("search_results")
    if not isinstance(raw_results, list):
        return []

    results: list[dict[str, Any]] = []
    for position, item in enumerate(raw_results, start=1):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or url).strip()
        if not url:
            continue
        results.append(
            {
                "index": item.get("index", position),
                "title": title,
                "url": url,
                "site_name": str(
                    item.get("site_name") or item.get("hostname") or ""
                ).strip(),
            }
        )
    return results


def normalize_entity_verification(
    payload: dict[str, Any],
    search_results: list[dict[str, Any]],
    source_text: str,
    confidence_threshold: float,
    max_entities: int,
) -> dict[str, Any]:
    raw_corrections = payload.get("corrections")
    if not isinstance(raw_corrections, list):
        raw_corrections = []

    corrections_by_original: dict[str, dict[str, Any]] = {}
    for item in raw_corrections:
        if not isinstance(item, dict):
            continue
        original = str(item.get("original") or "").strip()
        canonical = str(
            item.get("canonical") or item.get("corrected") or ""
        ).strip()
        if (
            len(original) < 2
            or not canonical
            or original == canonical
            or original not in source_text
        ):
            continue
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))

        raw_indexes = item.get("source_indexes")
        if not isinstance(raw_indexes, list):
            raw_indexes = []
        source_indexes = [
            str(index).strip()
            for index in raw_indexes
            if str(index).strip()
        ]
        normalized = {
            "original": original,
            "canonical": canonical,
            "aliases": _string_list(item.get("aliases")),
            "entity_type": str(item.get("entity_type") or "other").strip(),
            "confidence": confidence,
            "accepted": confidence >= confidence_threshold,
            "reason": str(item.get("reason") or "").strip(),
            "source_indexes": source_indexes,
        }
        existing = corrections_by_original.get(original)
        if existing is None or confidence > float(existing["confidence"]):
            corrections_by_original[original] = normalized

    corrections = sorted(
        corrections_by_original.values(),
        key=lambda item: (-float(item["confidence"]), -len(str(item["original"]))),
    )[:max_entities]

    unresolved = payload.get("unresolved")
    if not isinstance(unresolved, list):
        unresolved = []
    cleaned_unresolved: list[dict[str, str]] = []
    for item in unresolved[:max_entities]:
        if isinstance(item, str):
            cleaned_unresolved.append({"text": item.strip(), "reason": ""})
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("entity") or "").strip()
            if text:
                cleaned_unresolved.append(
                    {
                        "text": text,
                        "reason": str(item.get("reason") or "").strip(),
                    }
                )

    return {
        "enabled": True,
        "verified_at": datetime.now().isoformat(),
        "confidence_threshold": confidence_threshold,
        "corrections": corrections,
        "unresolved": cleaned_unresolved,
        "search_results": search_results,
    }


def _replace_entity(text: str, original: str, canonical: str) -> str:
    if original.isascii() and any(character.isalnum() for character in original):
        # Python's \w includes Chinese characters, but an English name may
        # directly follow Chinese text without spaces (例如“电台On The Radar”).
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(original)}(?![A-Za-z0-9_])"
        return re.sub(pattern, canonical, text, flags=re.IGNORECASE)
    return text.replace(original, canonical)


def apply_entity_corrections(value: Any, verification: dict[str, Any]) -> Any:
    replacements = [
        (
            str(item.get("original") or ""),
            str(item.get("canonical") or ""),
        )
        for item in verification.get("corrections", [])
        if isinstance(item, dict)
        and item.get("accepted")
        and item.get("original")
        and item.get("canonical")
    ]
    replacements.sort(key=lambda pair: len(pair[0]), reverse=True)

    def apply(item: Any) -> Any:
        if isinstance(item, str):
            result = item
            for original, canonical in replacements:
                result = _replace_entity(result, original, canonical)
            return result
        if isinstance(item, list):
            return [apply(child) for child in item]
        if isinstance(item, dict):
            return {key: apply(child) for key, child in item.items()}
        return item

    return apply(value)


def merge_entity_glossary(
    verification: dict[str, Any],
    glossary_path: Path,
    source_text: str,
) -> dict[str, Any]:
    if not glossary_path.exists():
        return verification
    try:
        payload = json.loads(glossary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取实体词典：{glossary_path}：{exc}") from exc
    raw_corrections = payload.get("corrections")
    if not isinstance(raw_corrections, list):
        raise RuntimeError(f"实体词典缺少 corrections 数组：{glossary_path}")

    merged = json.loads(json.dumps(verification, ensure_ascii=False))
    by_original = {
        str(item.get("original")): item
        for item in merged.get("corrections", [])
        if isinstance(item, dict) and item.get("original")
    }
    for item in raw_corrections:
        if not isinstance(item, dict):
            continue
        original = str(item.get("original") or "").strip()
        canonical = str(item.get("canonical") or "").strip()
        if (
            len(original) < 2
            or not canonical
            or original == canonical
            or original not in source_text
        ):
            continue
        by_original[original] = {
            "original": original,
            "canonical": canonical,
            "aliases": _string_list(item.get("aliases")),
            "entity_type": str(item.get("entity_type") or "other").strip(),
            "confidence": 1.0,
            "accepted": True,
            "reason": str(item.get("reason") or "项目实体词典确认。").strip(),
            "source_indexes": [],
            "source_type": "project_glossary",
        }

    merged["corrections"] = sorted(
        by_original.values(),
        key=lambda item: (-float(item.get("confidence") or 0), -len(item["original"])),
    )
    merged["glossary_path"] = str(glossary_path)
    return merged


def correct_transcript(
    transcript: list[TranscriptItem],
    verification: dict[str, Any],
) -> list[TranscriptItem]:
    return [
        TranscriptItem(
            start=item.start,
            end=item.end,
            text=str(apply_entity_corrections(item.text, verification)),
            speaker=item.speaker,
        )
        for item in transcript
    ]


def correct_analyses(
    analyses: list[dict[str, Any]],
    verification: dict[str, Any],
) -> list[dict[str, Any]]:
    corrected = apply_entity_corrections(analyses, verification)
    accepted = [
        item
        for item in verification.get("corrections", [])
        if isinstance(item, dict) and item.get("accepted")
    ]
    originals = [
        str(item.get("original") or "")
        for item in accepted
        if item.get("original")
    ]

    # Segment-level corrections were produced before web verification. Drop
    # entries that discuss an entity now proven wrong, otherwise stale claims
    # such as a wrong identity can survive in analysis_corrected.json.
    for raw_item, corrected_item in zip(analyses, corrected):
        raw_corrections = raw_item.get("transcript_corrections")
        corrected_corrections = corrected_item.get("transcript_corrections")
        if isinstance(raw_corrections, list) and isinstance(
            corrected_corrections,
            list,
        ):
            corrected_item["transcript_corrections"] = [
                corrected_correction
                for raw_correction, corrected_correction in zip(
                    raw_corrections,
                    corrected_corrections,
                )
                if not any(
                    original in json.dumps(raw_correction, ensure_ascii=False)
                    for original in originals
                )
            ]
        corrected_item["entity_corrections"] = [
            {
                key: item.get(key)
                for key in (
                    "original",
                    "canonical",
                    "entity_type",
                    "confidence",
                    "source_type",
                )
                if item.get(key) is not None
            }
            for item in accepted
        ]
    return corrected


class EntityVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _repair_json(self, content: str) -> dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "缺少 openai SDK，无法修复实体核验JSON。"
            ) from exc

        client = OpenAI(
            api_key=self.settings.dashscope_api_key,
            base_url=self.settings.qwen_base_url,
            timeout=self.settings.request_timeout_seconds,
            max_retries=2,
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是JSON格式修复器。只修复语法，不增加、删除或改变事实，"
                    "只输出一个合法JSON对象。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请修复下面实体核验结果的JSON语法。必须保留 corrections、"
                    "unresolved、source_indexes 等现有字段和值：\n\n"
                    f"{content}"
                ),
            },
        ]
        common: dict[str, Any] = {
            "model": self.settings.entity_verify_model,
            "messages": messages,
            "temperature": 0,
            "extra_body": {"enable_thinking": False},
        }
        try:
            response = client.chat.completions.create(
                **common,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            error_text = str(exc).lower()
            if "response_format" not in error_text and "json_object" not in error_text:
                raise
            response = client.chat.completions.create(**common)
        repaired = response.choices[0].message.content
        if not isinstance(repaired, str) or not repaired.strip():
            raise RuntimeError("实体核验JSON修复返回了空内容。")
        return parse_json_payload(repaired)

    def verify(
        self,
        metadata: dict[str, Any],
        transcript: list[TranscriptItem],
        analyses: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            import dashscope
        except ImportError as exc:
            raise RuntimeError(
                "缺少 dashscope SDK，请先执行：pip install -r requirements.txt"
            ) from exc

        context = _bounded_context(metadata, transcript, analyses)
        source_text = json.dumps(context, ensure_ascii=False)
        prompt = (
            "你是中文视频专名事实核验员。请从输入中找出可能被ASR或模型写错的"
            "人名、艺名、歌名、影视/节目名、组织名和品牌名，并强制使用联网搜索"
            "核验。重点检查同音字、近音字、英文艺名错误关联。不得修改普通措辞，"
            "不得只凭读音猜测；证据不足时放入 unresolved。只有搜索证据明确支持时"
            "才输出 correction。original 必须逐字出现在输入中。对于中文人名旁边"
            "括号内或同一句里的英文名，必须单独核验二者是否为同一人；如果英文名"
            "错误，必须再输出一项独立 correction，例如把错误英文艺名修正为该人物"
            "实际使用的规范英文艺名。aliases 只能填写已被证据确认属于 canonical "
            "实体的别名，不能保留错误关联。\n\n"
            f"输入：{source_text}\n\n"
            "只返回合法JSON对象：\n"
            "{\n"
            '  "corrections": [\n'
            "    {\n"
            '      "original": "输入中的错误写法",\n'
            '      "canonical": "规范名称",\n'
            '      "aliases": ["英文名或别名"],\n'
            '      "entity_type": "person|song|work|organization|brand|other",\n'
            '      "confidence": 0.0,\n'
            '      "reason": "简洁说明证据为何支持该修正",\n'
            '      "source_indexes": [1, 2]\n'
            "    }\n"
            "  ],\n"
            '  "unresolved": [{"text": "待确认名称", "reason": "证据不足原因"}]\n'
            "}"
        )

        LOGGER.info(
            "联网核验视频专名（模型：%s）",
            self.settings.entity_verify_model,
        )
        response = dashscope.Generation.call(
            api_key=self.settings.dashscope_api_key,
            model=self.settings.entity_verify_model,
            messages=[
                {
                    "role": "system",
                    "content": "你只输出严格、可解析、基于搜索证据的JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            enable_search=True,
            search_options={
                "forced_search": True,
                "enable_source": True,
                "enable_citation": True,
                "search_strategy": self.settings.entity_verify_search_strategy,
            },
            result_format="message",
        )
        status_code = getattr(response, "status_code", None)
        if status_code not in (None, HTTPStatus.OK, int(HTTPStatus.OK)):
            code = getattr(response, "code", "")
            message = getattr(response, "message", "")
            raise RuntimeError(f"实体联网核验失败：{code} {message}".strip())

        output = _plain_value(getattr(response, "output", {}))
        if not isinstance(output, dict):
            raise RuntimeError("实体联网核验返回了无效 output。")
        choices = output.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("实体联网核验没有返回候选结果。")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise RuntimeError("实体联网核验返回了无效 message。")
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("实体联网核验返回了空内容。")

        try:
            payload = parse_json_payload(content)
        except (ValueError, json.JSONDecodeError):
            LOGGER.warning("实体核验返回的JSON格式不完整，正在进行无联网语法修复。")
            payload = self._repair_json(content)
        verification = normalize_entity_verification(
            payload=payload,
            search_results=_normalize_search_results(output),
            source_text=source_text,
            confidence_threshold=self.settings.entity_verify_min_confidence,
            max_entities=self.settings.entity_verify_max_entities,
        )
        verification["model"] = self.settings.entity_verify_model
        verification["verifier_version"] = ENTITY_VERIFIER_VERSION
        return verification
