from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from video_assistant.config import Settings
from video_assistant.pipeline import VideoAnalysisPipeline
from video_assistant.utils import create_run_dir, setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "自动获取视频、提取音频、执行 ASR、抽取视觉证据，并使用 Qwen "
            "生成多模态视频报告。"
        )
    )
    parser.add_argument("input", nargs="?", help="B站/在线视频 URL 或本地视频路径")
    parser.add_argument(
        "--run-dir",
        type=Path,
        help="复用指定任务目录并从缓存续跑；不指定时自动创建新目录",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="新任务输出根目录，默认由 VIDEO_ASSISTANT_OUTPUT_DIR 控制",
    )
    parser.add_argument("--model", help="覆盖 QWEN_MODEL")
    parser.add_argument(
        "--mode",
        choices=("fast", "standard", "detailed"),
        default="detailed",
        help="分析模式：极速文本摘要、标准报告或深度报告",
    )
    parser.add_argument(
        "--segment-seconds",
        type=int,
        help="多模态分析片段长度，默认 180 秒",
    )
    parser.add_argument(
        "--cookie-file",
        type=Path,
        help="yt-dlp Cookie 文件；登录后可访问的视频可能需要",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略已有缓存，重新执行各处理步骤",
    )
    parser.add_argument(
        "--skip-web-verify",
        action="store_true",
        help="跳过人名、歌名等专名的联网核验，减少搜索调用",
    )
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="只检查环境、依赖和配置，不处理视频",
    )
    return parser


def run_doctor(settings: Settings) -> int:
    print("Project-ML 多模态视频助手环境检查")
    print(f"- Python: {sys.version.split()[0]}")
    packages = {
        "openai": "openai",
        "dashscope": "dashscope",
        "opencv": "cv2",
        "yt-dlp": "yt_dlp",
        "python-dotenv": "dotenv",
    }
    missing = []
    for label, module in packages.items():
        available = importlib.util.find_spec(module) is not None
        print(f"- {label}: {'OK' if available else '缺失'}")
        if not available:
            missing.append(label)

    print(
        f"- DASHSCOPE_API_KEY: "
        f"{'已配置' if settings.dashscope_api_key else '未配置'}"
    )
    print(f"- Qwen model: {settings.qwen_model}")
    print(
        "- Entity web verification: "
        + (
            f"ON ({settings.entity_verify_model})"
            if settings.entity_verify_enabled
            else "OFF"
        )
    )
    print(f"- FFmpeg: {settings.ffmpeg_bin}")
    print(f"- FFprobe: {settings.ffprobe_bin}")

    problems = settings.validate(require_api_key=True)
    if missing:
        problems.append("缺少 Python 依赖：" + ", ".join(missing))
    if problems:
        print("\n需要处理：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\n环境检查通过。")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent
    settings = Settings.from_env(project_root)

    if args.output_dir:
        settings.output_root = args.output_dir.expanduser().resolve()
    if args.model:
        settings.qwen_model = args.model
    if args.segment_seconds:
        settings.segment_seconds = args.segment_seconds
    if args.skip_web_verify:
        settings.entity_verify_enabled = False

    if args.doctor:
        return run_doctor(settings)
    if not args.input:
        parser.error("必须提供视频 URL/本地路径，或使用 --doctor。")

    problems = settings.validate(require_api_key=True)
    if problems:
        for problem in problems:
            print(f"配置错误：{problem}", file=sys.stderr)
        print("请先运行 `python video_pipeline.py --doctor`。", file=sys.stderr)
        return 2

    if args.cookie_file and not args.cookie_file.exists():
        parser.error(f"Cookie 文件不存在：{args.cookie_file}")

    if args.run_dir:
        run_dir = args.run_dir.expanduser().resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        settings.output_root.mkdir(parents=True, exist_ok=True)
        run_dir = create_run_dir(settings.output_root, args.input)

    setup_logging(run_dir, verbose=args.verbose)
    pipeline = VideoAnalysisPipeline(
        settings=settings,
        run_dir=run_dir,
        force=args.force,
        cookie_file=args.cookie_file,
        mode=args.mode,
        verify_entities=not args.skip_web_verify,
    )
    try:
        report_path = pipeline.run(args.input)
    except KeyboardInterrupt:
        print("\n任务已由用户中断。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\n处理失败：{exc}", file=sys.stderr)
        print(f"任务目录：{run_dir}", file=sys.stderr)
        print(
            "修复问题后使用相同输入并增加 "
            f"`--run-dir \"{run_dir}\"` 即可续跑。",
            file=sys.stderr,
        )
        return 1

    print(f"\n报告已生成：{report_path}")
    print(f"任务目录：{run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
