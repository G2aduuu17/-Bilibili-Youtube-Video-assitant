from __future__ import annotations

from typing import Any

from .utils import format_timestamp


def render_markdown_report(
    metadata: dict[str, Any],
    summary: dict[str, Any],
    segment_analyses: list[dict[str, Any]],
    entity_verification: dict[str, Any] | None = None,
) -> str:
    source_url = metadata.get("webpage_url") or metadata.get("source")
    content_type = str(summary.get("content_type") or "other")
    content_type_labels = {
        "course": "课程 / 知识讲解",
        "meeting": "会议",
        "tutorial": "教程 / 操作演示",
        "interview": "访谈",
        "commentary": "评论 / 观点",
        "news": "新闻 / 资讯",
        "entertainment": "娱乐 / 叙事",
        "other": "其他",
    }

    lines = [
        f"# {summary.get('title') or metadata.get('title') or '视频分析报告'}",
        "",
        "> 本报告由语音转写、视频画面和 Qwen 多模态分析联合生成。",
        "",
        "## 视频信息",
        "",
        f"- 来源：{source_url}",
        f"- 时长：{format_timestamp(float(metadata.get('duration_seconds') or 0))}",
        f"- 模型：{metadata.get('qwen_model', '')}",
        f"- 内容类型：{content_type_labels.get(content_type, '其他')}",
        "",
        "## 总览",
        "",
        str(summary.get("overview") or "暂无总览。"),
        "",
    ]

    central_thesis = str(summary.get("central_thesis") or "").strip()
    if central_thesis:
        lines.extend(["## 中心信息", "", central_thesis, ""])

    takeaways = summary.get("takeaways") or []
    if takeaways:
        lines.extend(["## 核心结论", ""])
        for index, item in enumerate(takeaways, start=1):
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
            else:
                text = str(item).strip()
            if not text:
                continue
            lines.extend([f"{index}. {text}", ""])

    insights = summary.get("content_type_insights") or []
    if insights:
        lines.extend(["## 类型化分析", ""])
        for item in insights:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "分析重点")
            content = str(item.get("content") or "")
            lines.extend([f"### {label}", "", content, ""])

    key_terms = summary.get("key_terms") or []
    if key_terms:
        lines.extend(
            [
                "## 关键术语",
                "",
                "| 术语 | 视频语境中的解释 |",
                "|---|---|",
            ]
        )
        for item in key_terms:
            if not isinstance(item, dict):
                continue
            term = str(item.get("term") or "").replace("|", "\\|")
            explanation = str(item.get("explanation") or "").replace("|", "\\|")
            if term and explanation:
                lines.append(f"| {term} | {explanation} |")
        lines.append("")

    lines.extend(["## 章节笔记", ""])
    for chapter_index, chapter in enumerate(summary.get("chapters") or [], start=1):
        start = float(chapter.get("start") or 0)
        end = float(chapter.get("end") or 0)
        lines.extend(
            [
                f"### {chapter_index}. {chapter.get('title') or '未命名章节'}",
                "",
                f"**时间：{format_timestamp(start)} - {format_timestamp(end)}**",
                "",
                str(chapter.get("summary") or ""),
                "",
            ]
        )

        key_points = chapter.get("key_points") or []
        if key_points:
            lines.append("主要内容：")
            lines.append("")
            lines.extend(f"- {item}" for item in key_points)
            lines.append("")

        keywords = chapter.get("keywords") or []
        if keywords:
            lines.extend([f"关键词：{', '.join(keywords)}", ""])

    cross_chapter_analysis = str(
        summary.get("cross_chapter_analysis") or ""
    ).strip()
    if cross_chapter_analysis:
        lines.extend(
            ["## 跨章节综合分析", "", cross_chapter_analysis, ""]
        )

    uncertainties = summary.get("uncertainties") or []
    if uncertainties:
        lines.extend(["## 不确定信息与边界", ""])
        lines.extend(f"- {item}" for item in uncertainties)
        lines.append("")

    final_synthesis = str(summary.get("final_synthesis") or "").strip()
    if final_synthesis:
        lines.extend(["## 最终综合结论", "", final_synthesis, ""])

    quality = summary.get("quality") or {}
    quality_warnings = quality.get("warnings") or []
    if quality_warnings:
        lines.extend(
            [
                "## 自动质量检查",
                "",
                "> 以下提示由结构化完整性检查产生，便于人工复核。",
                "",
            ]
        )
        lines.extend(f"- {warning}" for warning in quality_warnings)
        lines.append("")

    return "\n".join(lines)
