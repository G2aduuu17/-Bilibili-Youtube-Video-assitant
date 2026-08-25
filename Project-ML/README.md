# Project-ML：Qwen 多模态视频分析助手

将 B 站/在线视频链接或本地视频自动转换为带时间戳、关键画面和视觉证据的结构化报告。

当前版本按模式选择独立执行路径：

```text
极速：平台字幕 → 直接总结
      无字幕 → 仅下载音频 → 180秒分片并行 ASR → Flash 总结

标准：最高 720p 视频 → ASR 与抽帧并行 → 180秒多模态分段 → 可选实体核验

深度：高质量视频 → ASR 与抽帧并行 → 细粒度多模态分段 → 可选实体核验
```

## 安全提醒

旧版本曾在公开代码中提交过 API Key。即使代码中的 Key 已删除，Git 历史仍然可能包含它们。请立即在对应平台撤销旧 Key、创建新 Key，并考虑清理 Git 历史。真实 Key 只能保存在本地 `.env` 或安全的密钥管理服务中。

## 主要能力

- 支持 B 站等 yt-dlp 可解析的视频链接
- 支持 MP4、MKV、MOV、WebM 等本地视频
- 自动抽取完整视频和音频，而不是只分析音频
- ASR 结果包含句子级开始/结束时间
- 定时抽帧保证全程覆盖，场景变化抽帧保留 PPT、代码和镜头切换
- 每个时间片段同时输入 ASR 与关键画面给 Qwen
- 默认按 60 秒细粒度分析，并在汇总时按主题合并为章节
- 输出前联网核验人名、艺名、歌名、机构名等易错专名
- 原始 ASR 与纠错版本分开保存，修正结果附带搜索来源
- 自动识别课程、会议、教程、访谈、评论等内容类型并切换分析重点
- 核心结论附带片段 ID、时间戳和语音/画面/屏幕文字证据
- 汇总结果包含中心信息、跨章节分析、不确定信息和最终综合结论
- 自动检查片段覆盖率、核心结论证据覆盖率和摘要完整性
- 所有中间结果落盘，失败后可从相同任务目录续跑
- 生成 `transcript.srt`、结构化 JSON 和 `report.md`

## 环境要求

- 建议 Python 3.10–3.12
- FFmpeg 和 FFprobe
- 阿里云百炼 API Key
- 能够访问待处理视频和百炼 API 的网络环境

FFmpeg 必须是系统可执行文件，不是名为 `ffmpeg` 的 Python 包。可以安装到系统 PATH，或通过 `.env` 中的 `FFMPEG_BIN`、`FFPROBE_BIN` 指定绝对路径。

## 安装

推荐使用独立 Conda 环境，避免和现有 Python、PyTorch 或 OpenCV 环境冲突：

```powershell
conda env create -f environment.yml
conda activate project-ml-video
Copy-Item .env.example .env
```

如果 PowerShell 的执行策略导致 `conda activate` 不可用，可以始终使用：

```powershell
conda run --no-capture-output -n project-ml-video python video_pipeline.py --doctor
```

更新已有环境：

```powershell
conda run --no-capture-output -n project-ml-video python -m pip install -r requirements.txt
```

编辑 `.env`，至少填写：

```dotenv
DASHSCOPE_API_KEY=你的新APIKey
```

默认使用：

- 多模态模型：`qwen3.7-plus`
- ASR 模型：`paraformer-realtime-v2`
- 单个分析片段：60 秒
- 单片段最多输入 8 张画面

使用百炼业务空间专属域名时，请同时修改 `.env` 中的 `DASHSCOPE_BASE_URL` 和 `DASHSCOPE_WEBSOCKET_URL`。

## 环境检查

```powershell
conda run --no-capture-output -n project-ml-video python video_pipeline.py --doctor
```

检查会验证 Python 依赖、API Key、FFmpeg 和 FFprobe，但不会调用模型或产生费用。

## 使用

分析 B 站视频：

```powershell
conda run --no-capture-output -n project-ml-video python video_pipeline.py "https://www.bilibili.com/video/BVxxxxxxxxxx"
```

选择分析模式：

```powershell
conda run --no-capture-output -n project-ml-video python video_pipeline.py "视频URL" --mode fast
conda run --no-capture-output -n project-ml-video python video_pipeline.py "视频URL" --mode standard
conda run --no-capture-output -n project-ml-video python video_pipeline.py "视频URL" --mode detailed
```

分析本地视频：

```powershell
conda run --no-capture-output -n project-ml-video python video_pipeline.py "D:\videos\course.mp4"
```

如需避免联网核验产生额外搜索调用：

```powershell
conda run --no-capture-output -n project-ml-video python video_pipeline.py "视频URL" --skip-web-verify
```

需要登录 Cookie 的视频：

```powershell
conda run --no-capture-output -n project-ml-video python video_pipeline.py "视频URL" --cookie-file "D:\secrets\cookies.txt"
```

改变模型和片段长度：

```powershell
conda run --no-capture-output -n project-ml-video python video_pipeline.py "视频URL" --model qwen3.6-flash --segment-seconds 240
```

### 失败后续跑

首次运行会打印任务目录。如果中途失败，修复网络或配置后使用同一个目录：

```powershell
conda run --no-capture-output -n project-ml-video python video_pipeline.py "原始视频URL" --run-dir "runs\BVxxxx_20260724_120000"
```

流程会复用已经完成的下载、音频、转写、抽帧和片段分析。

如需全部重跑：

```powershell
conda run --no-capture-output -n project-ml-video python video_pipeline.py "原始视频URL" --run-dir "任务目录" --force
```

## 输出目录

```text
runs/<任务名_时间>/
├── source/                  # 在线视频下载结果
├── audio/
│   └── audio_16k_mono.wav
├── frames/                  # 带时间信息的视觉证据帧
├── analysis/
│   ├── seg_0001.json
│   └── ...
├── metadata.json
├── probe.json
├── transcript.json
├── transcript.srt
├── transcript_corrected.json
├── transcript_corrected.srt
├── frames.json
├── segments.json
├── analysis.json
├── analysis_cache.json
├── analysis_corrected.json
├── entity_verification.json
├── summary.json
├── report.md
├── state.json
└── pipeline.log
```

`analysis/*.json` 保留原始多模态片段结果，`analysis_cache.json` 记录分段、模型和提示词版本指纹；调整分段长度后，续跑会自动重新分析受影响片段。`analysis_corrected.json` 是应用联网专名核验后的版本。`entity_verification.json` 保存每项修正的置信度和搜索来源，便于审计。`summary.json` 的 `quality` 字段记录片段覆盖率、结论证据覆盖率和完整性警告。

## 关键配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `QWEN_MODEL` | `qwen3.7-plus` | 多模态理解和汇总模型 |
| `ENTITY_VERIFY_ENABLED` | `true` | 是否在汇总前联网核验专名 |
| `ENTITY_VERIFY_MODEL` | `qwen-plus` | 执行联网核验的文本模型 |
| `ENTITY_VERIFY_SEARCH_STRATEGY` | `turbo` | 百炼联网搜索策略 |
| `ENTITY_VERIFY_MIN_CONFIDENCE` | `0.85` | 自动应用修正的最低置信度 |
| `ENTITY_VERIFY_MAX_ENTITIES` | `20` | 单个视频最多核验修正数 |
| `ENTITY_GLOSSARY_PATH` | `entity_glossary.json` | 项目人工确认专名词典，优先级高于联网搜索 |
| `YTDLP_FORMAT` | 优先 H.264 | 下载格式选择；默认兼顾 OpenCV 解码速度并保留回退 |
| `SEGMENT_SECONDS` | `60` | 单个分析片段时长；更细的片段可提升章节覆盖和最终综合质量 |
| `PERIODIC_FRAME_SECONDS` | `30` | 定时保底抽帧间隔 |
| `SCENE_THRESHOLD` | `0.22` | 场景变化阈值，越低越敏感 |
| `MAX_TOTAL_FRAMES` | `500` | 单视频最多保留的证据帧 |
| `MAX_IMAGES_PER_SEGMENT` | `8` | 单次 Qwen 请求最多画面数 |
| `IMAGE_MAX_WIDTH` | `1280` | 保存画面的最大宽度 |
| `MODEL_REQUEST_TIMEOUT_SECONDS` | `180` | 单次模型请求超时 |
| `FAST_SUMMARY_MODEL` | `qwen3.7-flash` | 极速模式专用低延迟总结模型 |
| `FAST_ASR_MODEL` | `qwen3-asr-flash` | 无字幕时的极速分片 ASR 模型 |
| `FAST_ASR_CHUNK_SECONDS` | `180` | 极速 ASR 单个音频分片时长 |
| `FAST_ASR_PARALLELISM` | `4` | 极速 ASR 最大并发分片数 |

完整配置见 `.env.example`。

对于已经人工确认的人名或作品名，可以编辑 `entity_glossary.json`。词典中的修正以置信度 `1.0` 应用，并覆盖可能不准确的搜索结果。

## 测试

核心数据处理测试不需要 API Key：

```powershell
conda run --no-capture-output -n project-ml-video python -m unittest discover -s tests -v
```

## 设计说明

多模态模型不直接替代 ASR。Paraformer 负责稳定的语音文本和时间戳；Qwen 负责把语音、PPT、字幕、代码界面、人物动作和镜头变化融合成统一语义。这使报告中的结论能够关联回具体时间和画面证据。

下载和分析网络内容时，请遵守网站服务条款、著作权及所在地法律法规。只处理你有权下载和分析的内容。
