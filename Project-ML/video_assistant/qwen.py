from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

from .config import Settings
from .models import VideoFrame, VideoSegment
from .utils import format_timestamp, parse_json_payload


LOGGER = logging.getLogger("video_assistant")

SEGMENT_ANALYSIS_VERSION = "2"
SUMMARY_SCHEMA_VERSION = "2"
MAX_TAKEAWAYS = 6
MAX_TAKEAWAY_CHARACTERS = 120
TRANSCRIPT_TIMESTAMP_PATTERN = re.compile(
    r"\[\s*\d{1,3}(?:[.:]\d{1,2})?\s*[-–~]\s*\d{1,3}(?:[.:]\d{1,2})?\s*\]"
)

CONTENT_TYPE_GUIDANCE = {
    "course": "课程/知识讲解：突出核心概念、论证关系、例子、易错点和学习建议。",
    "meeting": "会议：突出议题、决定、分歧、行动项、负责人和截止时间；未明确的信息不要猜测。",
    "tutorial": "教程/操作演示：突出前置条件、步骤、关键参数、风险和验收结果。",
    "interview": "访谈：区分不同说话者的观点、论据、共识与分歧。",
    "commentary": "评论/观点：区分事实陈述、作者主张、论据、反例和争议。",
    "news": "新闻/资讯：突出事件、时间线、主体、影响，并区分已证实信息与推测。",
    "entertainment": "娱乐/叙事：突出情节、关键时刻、人物关系、笑点或文化背景。",
    "other": "其他：根据内容本身选择最有解释力的组织方式。",
}


def _data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _evenly_select_frames(
    frames: list[VideoFrame],
    limit: int,
) -> list[VideoFrame]:
    if len(frames) <= limit:
        return list(frames)
    if limit == 1:
        return [max(frames, key=lambda frame: frame.scene_score)]

    # Preserve temporal coverage, then replace the weakest interior choice with
    # the strongest scene change when it is otherwise absent.
    indexes = {
        round(position * (len(frames) - 1) / (limit - 1))
        for position in range(limit)
    }
    selected = [frames[index] for index in sorted(indexes)]
    strongest = max(frames, key=lambda frame: frame.scene_score)
    if strongest not in selected and len(selected) > 2:
        weakest_index = min(
            range(1, len(selected) - 1),
            key=lambda index: selected[index].scene_score,
        )
        selected[weakest_index] = strongest
        selected.sort(key=lambda frame: frame.timestamp)
    return selected


def _transcript_text(segment: VideoSegment) -> str:
    if not segment.transcript:
        return "（该时间段没有识别到语音）"
    return "\n".join(
        (
            f"[{format_timestamp(item.start)} - {format_timestamp(item.end)}] "
            f"{item.speaker}: {item.text}"
        )
        for item in segment.transcript
    )


def _normalize_segment_analysis(
    payload: dict[str, Any],
    segment: VideoSegment,
    selected_frames: list[VideoFrame],
) -> dict[str, Any]:
    def string_list(name: str) -> list[str]:
        value = payload.get(name, [])
        if isinstance(value, str):
            return [value]
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    visual_events = payload.get("visual_events", [])
    if not isinstance(visual_events, list):
        visual_events = []
    cleaned_events: list[dict[str, Any]] = []
    for event in visual_events:
        if not isinstance(event, dict):
            continue
        cleaned_events.append(
            {
                "timestamp": float(event.get("timestamp") or segment.start),
                "description": str(event.get("description") or "").strip(),
                "evidence_frame": str(event.get("evidence_frame") or "").strip(),
            }
        )

    corrections = payload.get("transcript_corrections", [])
    if not isinstance(corrections, list):
        corrections = []
    cleaned_corrections = [
        {
            "original": str(item.get("original") or "").strip(),
            "corrected": str(item.get("corrected") or "").strip(),
            "reason": str(item.get("reason") or "").strip(),
        }
        for item in corrections
        if isinstance(item, dict)
    ]

    named_entities = payload.get("named_entities", [])
    if not isinstance(named_entities, list):
        named_entities = []
    cleaned_entities: list[dict[str, Any]] = []
    for item in named_entities:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        cleaned_entities.append(
            {
                "text": text,
                "entity_type": str(
                    item.get("entity_type") or item.get("type") or "other"
                ).strip(),
                "context": str(item.get("context") or "").strip(),
                "confidence": min(1.0, max(0.0, confidence)),
            }
        )

    try:
        importance_score = float(payload.get("importance_score", 0.5))
    except (TypeError, ValueError):
        importance_score = 0.5

    notable_quotes = payload.get("notable_quotes", [])
    if not isinstance(notable_quotes, list):
        notable_quotes = []
    cleaned_quotes: list[dict[str, Any]] = []
    for item in notable_quotes:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            timestamp = float(item.get("timestamp", segment.start))
        except (TypeError, ValueError):
            timestamp = segment.start
        cleaned_quotes.append(
            {
                "timestamp": min(segment.end, max(segment.start, timestamp)),
                "speaker": str(item.get("speaker") or "SPEAKER_00").strip(),
                "text": text,
                "context": str(item.get("context") or "").strip(),
            }
        )

    representative = str(payload.get("representative_frame") or "").strip()
    available_paths = {frame.path for frame in selected_frames}
    if representative not in available_paths:
        representative = selected_frames[0].path if selected_frames else ""

    return {
        "segment_id": segment.segment_id,
        "start": segment.start,
        "end": segment.end,
        "title": str(payload.get("title") or f"片段 {segment.segment_id}").strip(),
        "summary": str(payload.get("summary") or "").strip(),
        "key_points": string_list("key_points"),
        "keywords": string_list("keywords"),
        "visual_events": cleaned_events,
        "on_screen_text": string_list("on_screen_text"),
        "named_entities": cleaned_entities,
        "notable_quotes": cleaned_quotes,
        "transcript_corrections": cleaned_corrections,
        "importance_score": min(1.0, max(0.0, importance_score)),
        "representative_frame": representative,
        "evidence_frames": [frame.to_dict() for frame in selected_frames],
    }


class QwenMultimodalClient:
    def __init__(self, settings: Settings):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "缺少 openai SDK，请先执行：pip install -r requirements.txt"
            ) from exc

        self.settings = settings
        self.client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.qwen_base_url,
            timeout=settings.request_timeout_seconds,
            max_retries=2,
        )

    def _chat_json(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        common: dict[str, Any] = {
            "model": self.settings.qwen_model,
            "messages": messages,
            "temperature": 0.1,
            "extra_body": {"enable_thinking": False},
        }
        try:
            response = self.client.chat.completions.create(
                **common,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            message = str(exc).lower()
            if "response_format" not in message and "json_object" not in message:
                raise
            LOGGER.warning("当前模型端点不接受 response_format，改用提示词 JSON 约束。")
            response = self.client.chat.completions.create(**common)

        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Qwen 返回了空内容。")
        return parse_json_payload(content)

    def analyze_segment(self, segment: VideoSegment, run_dir: Path) -> dict[str, Any]:
        selected_frames = _evenly_select_frames(
            segment.frames,
            self.settings.max_images_per_segment,
        )
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "你是一名严谨的视频内容分析师。请联合分析语音转写和按时间排列的"
                    "视频画面。不得补充视频之外的事实；无法确认的信息要明确标注不确定。\n\n"
                    f"片段ID：{segment.segment_id}\n"
                    f"时间范围：{format_timestamp(segment.start)} - "
                    f"{format_timestamp(segment.end)}\n\n"
                    "语音转写：\n"
                    f"{_transcript_text(segment)}\n\n"
                    "接下来依次提供视频画面，每张画面前有时间戳和文件ID。"
                ),
            }
        ]

        for index, frame in enumerate(selected_frames, start=1):
            absolute_path = run_dir / frame.path
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"画面 {index}：timestamp={frame.timestamp:.3f}s，"
                        f"frame_id={frame.path}"
                    ),
                }
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _data_url(absolute_path)},
                }
            )

        content.append(
            {
                "type": "text",
                "text": (
                    "只返回一个合法 JSON 对象，不要使用 Markdown 代码块。结构必须为：\n"
                    "{\n"
                    '  "title": "片段标题",\n'
                    '  "summary": "结合语音和画面的客观摘要",\n'
                    '  "key_points": ["要点"],\n'
                    '  "keywords": ["关键词"],\n'
                    '  "visual_events": [\n'
                    '    {"timestamp": 0.0, "description": "画面事件", '
                    '"evidence_frame": "frame_id"}\n'
                    "  ],\n"
                    '  "on_screen_text": ["画面中的关键字幕、PPT或代码文字"],\n'
                    '  "notable_quotes": [\n'
                    '    {"timestamp": 0.0, "speaker": "说话者", '
                    '"text": "对理解内容有价值的原话", "context": "原话作用"}\n'
                    "  ],\n"
                    '  "named_entities": [\n'
                    '    {"text": "人名/歌名/机构名等专名", '
                    '"entity_type": "person|song|work|organization|brand|other", '
                    '"context": "出现上下文", "confidence": 0.0}\n'
                    "  ],\n"
                    '  "transcript_corrections": [\n'
                    '    {"original": "ASR原文", "corrected": "修正", '
                    '"reason": "画面或上下文证据"}\n'
                    "  ],\n"
                    '  "importance_score": 0.0,\n'
                    '  "representative_frame": "必须从提供的frame_id中选择"\n'
                    "}"
                ),
            }
        )

        LOGGER.info(
            "Qwen 分析 %s（%d 张画面，%d 句转写）",
            segment.segment_id,
            len(selected_frames),
            len(segment.transcript),
        )
        payload = self._chat_json([{"role": "user", "content": content}])
        return _normalize_segment_analysis(payload, segment, selected_frames)

    def summarize_video(
        self,
        title: str,
        segment_analyses: list[dict[str, Any]],
    ) -> dict[str, Any]:
        is_fast_summary = any(
            item.get("analysis_mode") == "fast" for item in segment_analyses
        )
        compact_segments = [
            {
                "segment_id": item.get("segment_id"),
                "start": item.get("start"),
                "end": item.get("end"),
                "title": item.get("title"),
                "summary": item.get("summary"),
                "key_points": item.get("key_points"),
                "keywords": item.get("keywords"),
                "visual_events": item.get("visual_events"),
                "on_screen_text": item.get("on_screen_text"),
                "named_entities": item.get("named_entities"),
                "notable_quotes": item.get("notable_quotes"),
                "importance_score": item.get("importance_score"),
                "representative_frame": item.get("representative_frame"),
                "analysis_mode": item.get("analysis_mode"),
            }
            for item in segment_analyses
        ]
        type_guidance = "\n".join(
            f"- {name}: {guidance}"
            for name, guidance in CONTENT_TYPE_GUIDANCE.items()
        )
        fast_summary_instruction = (
            "当前为极速摘要：输入可能是一整段转写。只提炼结论，不得把转写内容原样"
            "放入任何字段。\n\n"
            if is_fast_summary
            else ""
        )
        prompt = (
            "你是一名严谨的视频研究报告编辑。下面是按时间排序的多模态片段分析。"
            "先判断内容类型，再按对应模板进行深度综合。相邻且主题一致的片段可以"
            "合并成章节，但不得遗漏任何片段。所有事实、观点和结论必须由输入片段"
            "支持，不得补充视频之外的信息。章节时间必须连续、递增，不要虚构片段ID。"
            "不要为了达到字数而重复内容；信息不足时应写入 uncertainties。\n\n"
            "内容类型指引：\n"
            f"{type_guidance}\n\n"
            f"视频标题：{title}\n"
            f"片段数据：{json.dumps(compact_segments, ensure_ascii=False)}\n\n"
            f"{fast_summary_instruction}"
            "质量要求：\n"
            "1. overview 应形成完整的总体叙述；短视频约 250-400 字，长视频可扩展到 "
            "400-700 字。\n"
            "2. central_thesis 用一段话概括视频最核心的信息或主张。\n"
            "3. takeaways 给出 3-6 条。每条必须是一句独立、清晰的结论，最多 120 个"
            "汉字；不得复制字幕、转写原文、时间戳或大段引语。每条都必须附带至少一个"
            "evidence，并使用真实 segment_id 和该片段范围内的 timestamp。\n"
            "4. 每章 summary 通常为 150-300 字，并解释信息之间的关系，不要只罗列。\n"
            "5. cross_chapter_analysis 应总结跨章节的因果、演变、对照、共识或分歧；"
            "如果视频不具备这些关系，要如实说明。\n"
            "6. final_synthesis 应给出完整收束，回答视频最终说明了什么、依据是什么，"
            "以及有哪些边界或未决问题。\n"
            "7. content_type_insights 根据内容类型填写。例如课程写核心概念/易错点，"
            "会议写决定/行动项，教程写步骤/风险，评论写主张/论据/争议。\n\n"
            "只返回合法 JSON 对象：\n"
            "{\n"
            '  "title": "报告标题",\n'
            '  "content_type": "course|meeting|tutorial|interview|commentary|news|'
            'entertainment|other",\n'
            '  "overview": "详细的视频总体摘要",\n'
            '  "central_thesis": "中心信息或核心主张",\n'
            '  "takeaways": [\n'
            "    {\n"
            '      "text": "核心结论",\n'
            '      "evidence": [\n'
            '        {"segment_id": "seg_0001", "timestamp": 0.0, '
            '"evidence_type": "speech|visual|screen_text|segment", '
            '"description": "支持该结论的具体证据"}\n'
            "      ]\n"
            "    }\n"
            "  ],\n"
            '  "content_type_insights": [\n'
            '    {"label": "类型相关栏目", "content": "综合分析", '
            '"evidence_segment_ids": ["seg_0001"]}\n'
            "  ],\n"
            '  "key_terms": [\n'
            '    {"term": "关键术语", "explanation": "视频语境中的解释"}\n'
            "  ],\n"
            '  "cross_chapter_analysis": "跨章节综合分析",\n'
            '  "uncertainties": ["未确认、存在歧义或视频未回答的问题"],\n'
            '  "final_synthesis": "最终综合结论",\n'
            '  "chapters": [\n'
            "    {\n"
            '      "title": "章节标题", "start": 0.0, "end": 0.0,\n'
            '      "summary": "章节摘要", "key_points": ["要点"],\n'
            '      "keywords": ["关键词"], "segment_ids": ["seg_0001"],\n'
            '      "evidence": [\n'
            '        {"segment_id": "seg_0001", "timestamp": 0.0, '
            '"evidence_type": "speech|visual|screen_text|segment", '
            '"description": "章节证据"}\n'
            "      ]\n"
            "    }\n"
            "  ]\n"
            "}"
        )
        LOGGER.info("Qwen 汇总整段视频，共 %d 个片段", len(segment_analyses))
        payload = self._chat_json(
            [
                {
                    "role": "system",
                    "content": "你只输出严格、可解析、基于证据的 JSON。",
                },
                {"role": "user", "content": prompt},
            ]
        )
        summary = _normalize_video_summary(payload, title, segment_analyses)
        for warning in summary.get("quality", {}).get("warnings", []):
            LOGGER.warning("视频汇总质量检查：%s", warning)
        return summary


def _normalize_video_summary(
    payload: dict[str, Any],
    fallback_title: str,
    segment_analyses: list[dict[str, Any]],
) -> dict[str, Any]:
    segment_map = {
        str(item.get("segment_id")): item
        for item in segment_analyses
        if item.get("segment_id")
    }

    chapters_value = payload.get("chapters", [])
    chapters: list[dict[str, Any]] = []
    if isinstance(chapters_value, list):
        for chapter in chapters_value:
            if not isinstance(chapter, dict):
                continue
            segment_ids = chapter.get("segment_ids", [])
            if not isinstance(segment_ids, list):
                segment_ids = []
            segment_ids = [
                str(segment_id)
                for segment_id in segment_ids
                if str(segment_id) in segment_map
            ]
            if not segment_ids:
                continue
            members = [segment_map[segment_id] for segment_id in segment_ids]
            chapters.append(
                {
                    "title": str(chapter.get("title") or members[0].get("title") or ""),
                    "start": min(float(item.get("start") or 0) for item in members),
                    "end": max(float(item.get("end") or 0) for item in members),
                    "summary": str(chapter.get("summary") or "").strip(),
                    "key_points": _as_string_list(chapter.get("key_points")),
                    "keywords": _as_string_list(chapter.get("keywords")),
                    "segment_ids": segment_ids,
                    "evidence": _normalize_evidence(
                        chapter.get("evidence"),
                        segment_map,
                    ),
                }
            )

    if not chapters:
        chapters = [
            {
                "title": str(item.get("title") or item.get("segment_id")),
                "start": float(item.get("start") or 0),
                "end": float(item.get("end") or 0),
                "summary": str(item.get("summary") or ""),
                "key_points": _as_string_list(item.get("key_points")),
                "keywords": _as_string_list(item.get("keywords")),
                "segment_ids": [str(item.get("segment_id"))],
                "evidence": _fallback_segment_evidence(item),
            }
            for item in segment_analyses
        ]
    else:
        used_segment_ids = {
            segment_id
            for chapter in chapters
            for segment_id in chapter["segment_ids"]
        }
        for item in segment_analyses:
            segment_id = str(item.get("segment_id") or "")
            if not segment_id or segment_id in used_segment_ids:
                continue
            chapters.append(
                {
                    "title": str(item.get("title") or segment_id),
                    "start": float(item.get("start") or 0),
                    "end": float(item.get("end") or 0),
                    "summary": str(item.get("summary") or ""),
                    "key_points": _as_string_list(item.get("key_points")),
                    "keywords": _as_string_list(item.get("keywords")),
                    "segment_ids": [segment_id],
                    "evidence": _fallback_segment_evidence(item),
                }
            )

    chapters.sort(key=lambda item: item["start"])
    for chapter in chapters:
        if not chapter["evidence"]:
            members = [
                segment_map[segment_id]
                for segment_id in chapter["segment_ids"]
                if segment_id in segment_map
            ]
            chapter["evidence"] = [
                evidence
                for member in members
                for evidence in _fallback_segment_evidence(member)
            ][:4]

    takeaways = _normalize_takeaways(payload.get("takeaways"), segment_map)
    _fill_takeaway_fallbacks(takeaways, segment_analyses, minimum=3)

    valid_content_types = set(CONTENT_TYPE_GUIDANCE)
    content_type = str(payload.get("content_type") or "other").strip().lower()
    if content_type not in valid_content_types:
        content_type = "other"

    overview = str(payload.get("overview") or "").strip()
    if not overview:
        overview = "\n".join(
            str(item.get("summary") or "").strip()
            for item in segment_analyses
            if str(item.get("summary") or "").strip()
        )
    central_thesis = str(payload.get("central_thesis") or "").strip()
    if not central_thesis:
        central_thesis = takeaways[0]["text"] if takeaways else overview
    final_synthesis = str(payload.get("final_synthesis") or "").strip()
    if not final_synthesis:
        final_synthesis = overview

    result = {
        "title": str(payload.get("title") or fallback_title).strip(),
        "content_type": content_type,
        "overview": overview,
        "central_thesis": central_thesis,
        "takeaways": takeaways,
        "content_type_insights": _normalize_content_type_insights(
            payload.get("content_type_insights"),
            segment_map,
        ),
        "key_terms": _normalize_key_terms(payload.get("key_terms")),
        "cross_chapter_analysis": str(
            payload.get("cross_chapter_analysis") or ""
        ).strip(),
        "uncertainties": _as_string_list(payload.get("uncertainties")),
        "final_synthesis": final_synthesis,
        "chapters": chapters,
    }
    result["quality"] = _summary_quality(result, segment_map)
    return result


def _normalize_evidence(
    value: Any,
    segment_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    evidence: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        segment_id = str(item.get("segment_id") or "")
        segment = segment_map.get(segment_id)
        if segment is None:
            continue
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or start)
        try:
            timestamp = float(item.get("timestamp", start))
        except (TypeError, ValueError):
            timestamp = start
        evidence_type = str(item.get("evidence_type") or "segment").strip()
        if evidence_type not in {"speech", "visual", "screen_text", "segment"}:
            evidence_type = "segment"
        description = str(item.get("description") or "").strip()
        if not description:
            description = str(segment.get("summary") or segment.get("title") or "").strip()
        evidence.append(
            {
                "segment_id": segment_id,
                "timestamp": min(end, max(start, timestamp)),
                "evidence_type": evidence_type,
                "description": description,
            }
        )
    return evidence


def _fallback_segment_evidence(item: dict[str, Any]) -> list[dict[str, Any]]:
    segment_id = str(item.get("segment_id") or "")
    if not segment_id:
        return []
    start = float(item.get("start") or 0)
    events = [
        event
        for event in item.get("visual_events") or []
        if isinstance(event, dict) and event.get("description")
    ]
    if events:
        event = events[0]
        return [
            {
                "segment_id": segment_id,
                "timestamp": float(event.get("timestamp") or start),
                "evidence_type": "visual",
                "description": str(event.get("description") or "").strip(),
            }
        ]
    description = str(item.get("summary") or item.get("title") or "").strip()
    if not description:
        return []
    return [
        {
            "segment_id": segment_id,
            "timestamp": start,
            "evidence_type": "segment",
            "description": description,
        }
    ]


def _normalize_takeaways(
    value: Any,
    segment_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        value = [value] if isinstance(value, str) else []
    takeaways: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            evidence: list[dict[str, Any]] = []
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("conclusion") or "").strip()
            evidence = _normalize_evidence(item.get("evidence"), segment_map)
        else:
            continue
        text = _clean_takeaway_text(text)
        if not text or text in seen:
            continue
        seen.add(text)
        takeaways.append({"text": text, "evidence": evidence})
        if len(takeaways) >= MAX_TAKEAWAYS:
            break
    return takeaways


def _clean_takeaway_text(value: str) -> str:
    """Keep concise conclusions, not transcript-shaped model output."""
    text = value.strip()
    if not text or len(text) > MAX_TAKEAWAY_CHARACTERS:
        return ""
    if text.count("\n") > 1 or TRANSCRIPT_TIMESTAMP_PATTERN.search(text):
        return ""
    return " ".join(text.split())


def _fill_takeaway_fallbacks(
    takeaways: list[dict[str, Any]],
    segment_analyses: list[dict[str, Any]],
    minimum: int,
) -> None:
    if len(takeaways) >= minimum or len(takeaways) >= MAX_TAKEAWAYS:
        return
    seen = {str(item.get("text") or "") for item in takeaways}
    ordered = sorted(
        segment_analyses,
        key=lambda item: float(item.get("importance_score") or 0),
        reverse=True,
    )
    for segment in ordered:
        candidates = [
            *_as_string_list(segment.get("key_points")),
            str(segment.get("summary") or "").strip(),
        ]
        for candidate in candidates:
            candidate = _clean_takeaway_text(candidate)
            if not candidate or candidate in seen:
                continue
            takeaways.append(
                {
                    "text": candidate,
                    "evidence": _fallback_segment_evidence(segment),
                }
            )
            seen.add(candidate)
            if len(takeaways) >= minimum or len(takeaways) >= MAX_TAKEAWAYS:
                return


def _normalize_content_type_insights(
    value: Any,
    segment_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    insights: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        content = str(item.get("content") or "").strip()
        segment_ids = [
            str(segment_id)
            for segment_id in item.get("evidence_segment_ids") or []
            if str(segment_id) in segment_map
        ]
        if label and content:
            insights.append(
                {
                    "label": label,
                    "content": content,
                    "evidence_segment_ids": segment_ids,
                }
            )
    return insights


def _normalize_key_terms(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    terms: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or "").strip()
        explanation = str(item.get("explanation") or "").strip()
        if term and explanation:
            terms.append({"term": term, "explanation": explanation})
    return terms


def _summary_quality(
    summary: dict[str, Any],
    segment_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    covered_ids = {
        segment_id
        for chapter in summary.get("chapters") or []
        for segment_id in chapter.get("segment_ids") or []
    }
    takeaways = summary.get("takeaways") or []
    evidenced_takeaways = sum(
        1 for item in takeaways if isinstance(item, dict) and item.get("evidence")
    )
    warnings: list[str] = []
    overview_length = len(str(summary.get("overview") or ""))
    final_length = len(str(summary.get("final_synthesis") or ""))
    if overview_length < 180:
        warnings.append("总体摘要少于 180 字，可能不够完整。")
    if len(takeaways) < 3:
        warnings.append("核心结论少于 3 条。")
    if takeaways and evidenced_takeaways < len(takeaways):
        warnings.append("部分核心结论缺少可追溯证据。")
    if covered_ids != set(segment_map):
        warnings.append("章节没有覆盖全部分析片段。")
    short_chapters = [
        chapter
        for chapter in summary.get("chapters") or []
        if len(str(chapter.get("summary") or "")) < 80
    ]
    if short_chapters:
        warnings.append(f"有 {len(short_chapters)} 个章节摘要少于 80 字。")
    if (
        summary.get("content_type") != "other"
        and not summary.get("content_type_insights")
    ):
        warnings.append("缺少与内容类型匹配的专项分析。")
    if len(summary.get("chapters") or []) > 1 and not str(
        summary.get("cross_chapter_analysis") or ""
    ).strip():
        warnings.append("多章节报告缺少跨章节综合分析。")
    if final_length < 100:
        warnings.append("最终综合结论少于 100 字，可能缺少充分收束。")
    return {
        "segment_coverage": (
            len(covered_ids) / len(segment_map) if segment_map else 1.0
        ),
        "takeaway_evidence_coverage": (
            evidenced_takeaways / len(takeaways) if takeaways else 0.0
        ),
        "overview_characters": overview_length,
        "final_synthesis_characters": final_length,
        "warnings": warnings,
    }


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
