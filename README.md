# Bilibili / YouTube Video Assistant

An online-ready AI video understanding system that turns Bilibili, YouTube, other `yt-dlp`-compatible URLs, or local video files into structured reports. It combines subtitle extraction, speech recognition, multimodal analysis, optional entity verification, and Markdown report generation in one asynchronous workflow.

*一个面向本地与云端部署的 AI 视频理解系统。它可以将 Bilibili、YouTube、其他 `yt-dlp` 可解析的视频链接或本地视频转换为结构化报告，并在同一条异步流水线中完成字幕获取、语音识别、多模态分析、可选专名核验和 Markdown 报告生成。*

> This project is an early, working release. Only process content that you are authorized to access and analyze, and comply with the platform terms and applicable laws.
>
> *本项目目前是可运行的早期版本。请只处理你有权访问和分析的内容，并遵守平台条款及所在地法律法规。*

## Highlights / 核心能力

- Accept video URLs and local uploads, with a common task API and report format.
  *支持视频链接和本地上传，并统一任务接口与报告格式。*
- Prefer existing platform subtitles and automatically fall back to ASR when subtitles are unavailable.
  *优先使用平台字幕；字幕不可用时自动回退到语音识别。*
- Keep Fast, Standard, and Detailed modes isolated at the configuration and execution-path levels.
  *在配置和执行链路层面隔离极速、标准和深度三种模式。*
- Run ASR and frame extraction concurrently, and transcribe audio chunks in parallel in Fast mode.
  *ASR 与抽帧并行执行，极速模式还支持音频分片并发识别。*
- Generate readable Markdown reports without exposing internal evidence IDs in the final conclusions.
  *生成可读的 Markdown 报告，最终结论不直接罗列内部证据片段 ID。*
- Use Redis/RQ for background jobs and MongoDB for task, report, and segment persistence.
  *使用 Redis/RQ 处理后台任务，并通过 MongoDB 持久化任务、报告和片段数据。*
- Run the full web application with Docker Compose.
  *通过 Docker Compose 启动完整 Web 系统。*

## Analysis Modes / 分析模式

| Mode | English description | 中文说明 |
| --- | --- | --- |
| **Fast** | Use platform subtitles directly. If no subtitle is available, download audio only, split it into 180-second chunks, run parallel ASR, and summarize with a low-latency Flash model. | 优先直接总结平台字幕；无字幕时仅下载音频，按 180 秒切片并行 ASR，再使用低延迟 Flash 模型总结。 |
| **Standard** | Download video at up to 720p, run ASR and frame extraction concurrently, and analyze larger multimodal segments. | 下载最高 720p 视频，并行执行 ASR 与抽帧，再分析较大的多模态片段。 |
| **Detailed** | Preserve higher-quality video, use finer multimodal segmentation, and optionally verify names and entities online. | 保留更高质量视频，使用更细粒度的多模态分段，并可选联网核验专名和实体。 |

The engineering target for Fast mode is to complete many captioned videos of 10–15 minutes within two minutes. This is a performance target rather than a guaranteed SLA because platform delivery speed, subtitle availability, network quality, and model-service latency remain external variables.

*极速模式的工程目标是让许多带字幕的 10～15 分钟视频在两分钟内完成。由于平台下载速度、字幕可用性、网络质量和模型服务延迟均属于外部变量，因此该目标不是硬性 SLA。*

## Architecture / 系统架构

```mermaid
flowchart LR
    U["Browser / 浏览器"] --> N["Nginx + React"]
    N --> A["FastAPI"]
    A --> M["MongoDB"]
    A --> R["Redis / RQ"]
    R --> W["Analysis Worker"]
    W --> P["Project-ML Pipeline"]
    P --> Y["yt-dlp / FFmpeg"]
    P --> Q["DashScope ASR + Qwen"]
    P --> F["./data/tasks"]
    W --> M
```

The frontend submits tasks through Nginx to FastAPI. The API stores task state in MongoDB and enqueues work in Redis. An RQ worker runs the Project-ML pipeline, writes intermediate artifacts to the local task directory, and persists the final structured report back to MongoDB.

*前端通过 Nginx 向 FastAPI 提交任务。API 将任务状态保存到 MongoDB，并把作业加入 Redis 队列。RQ Worker 执行 Project-ML 流水线，把中间产物写入本地任务目录，并将最终结构化报告保存回 MongoDB。*

| Component | Responsibility / 职责 |
| --- | --- |
| React + Nginx | Task creation, result display, and reverse proxy / 创建任务、展示结果和反向代理 |
| FastAPI | Task, report, and asset APIs / 任务、报告和资源接口 |
| Redis + RQ | Asynchronous job scheduling / 异步任务调度 |
| Worker | Download, ASR, multimodal analysis, and reporting / 下载、ASR、多模态分析和报告生成 |
| MongoDB | Task, report, and segment persistence / 任务、报告和片段持久化 |
| `./data/tasks` | Source files and pipeline artifacts on the host / 宿主机中的源文件和流水线产物 |

## Technology Stack / 技术栈

- **Frontend:** React, Vite, React Markdown, Nginx
- **Backend:** FastAPI, Pydantic, MongoDB, Redis, RQ
- **Media:** yt-dlp, FFmpeg, FFprobe, OpenCV
- **AI:** DashScope ASR, Qwen text and vision models
- **Deployment:** Docker Compose

*前端使用 React/Vite；后端使用 FastAPI、MongoDB、Redis 和 RQ；媒体处理依赖 yt-dlp、FFmpeg 与 OpenCV；模型服务使用 DashScope ASR 和 Qwen；整体通过 Docker Compose 部署。*

## Quick Start / 快速开始

### Requirements / 环境要求

- Docker Desktop with the Linux Containers / WSL 2 backend enabled.
  *启用 Linux Containers / WSL 2 后端的 Docker Desktop。*
- A DashScope API key.
  *阿里云百炼 DashScope API Key。*
- Network access to the target video platform and DashScope.
  *能够访问目标视频平台和 DashScope 的网络。*
- At least 4 CPU cores, 8 GB of memory, and sufficient disk space are recommended.
  *建议至少 4 核 CPU、8 GB 内存以及充足磁盘空间。*

### 1. Clone / 获取代码

```powershell
git clone https://github.com/G2aduuu17/-Bilibili-Youtube-Video-assitant.git
cd .\-Bilibili-Youtube-Video-assitant
```

### 2. Configure / 配置环境变量

```powershell
Copy-Item .env.example .env
notepad .env
```

Set at least the following values. Keep the MongoDB password in `MONGO_INITDB_ROOT_PASSWORD` and `MONGODB_URL` consistent.

*至少设置以下字段，并保证 `MONGO_INITDB_ROOT_PASSWORD` 与 `MONGODB_URL` 中的 MongoDB 密码一致。*

```dotenv
APP_SECRET=replace-with-a-long-random-value
MONGO_INITDB_ROOT_PASSWORD=replace-with-a-strong-password
MONGODB_URL=mongodb://video_devour:the-same-password@mongo:27017/video_devour?authSource=admin
DASHSCOPE_API_KEY=your-dashscope-api-key
```

Never commit `.env`. The repository only includes `.env.example` with placeholder values.

*绝对不要提交 `.env`。仓库只包含使用占位符的 `.env.example`。*

### 3. Start / 启动系统

```powershell
docker compose up -d --build
docker compose ps
```

- Web application: <http://localhost:8080>
- Health endpoint: <http://localhost:8080/api/v1/health>

*Web 页面位于 `http://localhost:8080`，健康检查位于 `http://localhost:8080/api/v1/health`。*

The first build downloads base images and dependencies, so it is slower than subsequent starts. For normal daily startup, use:

*首次构建需要下载基础镜像和依赖，因此会比后续启动更慢。日常启动只需执行：*

```powershell
docker compose up -d
```

To stop the application without deleting persistent data:

*停止应用但保留持久化数据：*

```powershell
docker compose down
```

Do not run `docker compose down -v` unless you intentionally want to delete the MongoDB and Redis volumes.

*除非明确希望删除 MongoDB 和 Redis volumes，否则不要执行 `docker compose down -v`。*

## Usage / 使用方法

Open the web application, choose a local upload or video URL, select an analysis mode, and submit the task. Fast mode disables entity verification because its purpose is low-latency summarization.

*打开 Web 页面，选择本地上传或视频链接，再选择分析模式并提交任务。极速模式会关闭实体核验，以降低总结延迟。*

You can also create a Fast task directly through the API:

*也可以直接通过 API 创建极速任务：*

```powershell
$body = @{
  url = "https://www.bilibili.com/video/BVxxxxxxxx"
  report_mode = "fast"
  verify_entities = $false
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://localhost:8080/api/v1/tasks/url" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

## YouTube Support and Platform Migration / YouTube 支持与平台迁移

### Current YouTube compatibility / 当前 YouTube 兼容性

The current acquisition layer uses `yt-dlp`, so public YouTube URLs can already be submitted through the same URL form and `/api/v1/tasks/url` endpoint. No report-generation code needs to change: once media metadata, subtitles, audio, or video have been acquired, every platform enters the same normalized analysis pipeline.

*当前获取层使用 `yt-dlp`，因此公开 YouTube 链接已经可以通过同一个链接表单和 `/api/v1/tasks/url` 接口提交。报告生成代码无需改变：只要获取到媒体元数据、字幕、音频或视频，不同平台都会进入同一条标准化分析流水线。*

Example:

*示例：*

```powershell
$body = @{
  url = "https://www.youtube.com/watch?v=VIDEO_ID"
  report_mode = "fast"
  verify_entities = $false
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://localhost:8080/api/v1/tasks/url" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

For Fast mode, the preferred YouTube path is: available subtitles → direct summary. If usable subtitles cannot be obtained, the pipeline falls back to audio-only download → parallel ASR → summary. Standard and Detailed modes additionally acquire video frames for multimodal analysis.

*对于极速模式，推荐的 YouTube 路径是“存在字幕 → 直接总结”。如果无法获得可用字幕，则回退到“仅下载音频 → 并行 ASR → 总结”。标准和深度模式还会获取视频画面进行多模态分析。*

`yt-dlp` compatibility is best-effort and can change when a platform changes its player, authentication, or delivery protocol. Keep `yt-dlp` updated, but do not treat extractor compatibility as an official platform API contract. See the [yt-dlp supported-sites documentation](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).

*`yt-dlp` 的兼容性属于尽力支持，平台修改播放器、认证方式或分发协议后可能发生变化。应定期更新 `yt-dlp`，但不能把解析器兼容性视为平台官方 API 契约。可参考 [yt-dlp 支持站点文档](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)。*

### Recommended production integration / 推荐的生产级接入方式

For a production YouTube integration, keep media acquisition separate from report generation. Add a platform adapter layer instead of adding YouTube-specific branches throughout the pipeline:

*生产级接入 YouTube 时，应继续把媒体获取与报告生成分离。建议增加平台适配层，而不是在整条流水线中到处添加 YouTube 特殊判断：*

```text
VideoSourceAdapter
├── match(url)
├── fetch_metadata()
├── fetch_subtitles()
├── acquire_audio()
└── acquire_video()

Adapters
├── YouTubeAdapter
├── BilibiliAdapter
└── GenericYtDlpAdapter (fallback)
```

Each adapter should return one normalized source object containing `platform`, `source_id`, `canonical_url`, `title`, `duration`, `subtitle_tracks`, `media_kind`, and local artifact paths. The ASR, frame extraction, segmentation, model analysis, and report layers should consume only this normalized object.

*每个适配器应返回统一的来源对象，其中包含 `platform`、`source_id`、`canonical_url`、`title`、`duration`、`subtitle_tracks`、`media_kind` 和本地产物路径。ASR、抽帧、分段、模型分析和报告层只依赖这个统一对象。*

Use the official YouTube Data API for stable metadata and channel/video identity. Public-data requests can use an API key, while access to private user data requires OAuth 2.0. Follow the official [YouTube Data API getting-started guide](https://developers.google.com/youtube/v3/getting-started) and [OAuth authentication guide](https://developers.google.com/youtube/v3/guides/authentication).

*生产环境应使用官方 YouTube Data API 获取稳定的元数据、频道和视频身份。公开数据请求可以使用 API Key，访问用户私有数据则需要 OAuth 2.0。请参考官方 [YouTube Data API 入门文档](https://developers.google.com/youtube/v3/getting-started) 和 [OAuth 认证文档](https://developers.google.com/youtube/v3/guides/authentication)。*

The official captions endpoints require authorization and do not provide unrestricted transcript download for arbitrary videos. Use them only when the authenticated user has the necessary permission; otherwise use accessible public subtitle tracks or fall back to ASR. See the official [`captions.download` documentation](https://developers.google.com/youtube/v3/docs/captions/download).

*官方字幕接口需要授权，并不能无限制下载任意视频的字幕。只有当已认证用户拥有相应权限时才应调用；否则应使用可访问的公开字幕轨道，或回退到 ASR。请参考官方 [`captions.download` 文档](https://developers.google.com/youtube/v3/docs/captions/download)。*

YouTube Data API requests consume project quota. Cache stable metadata by `platform + source_id`, avoid repeated polling, and monitor quota usage before increasing worker concurrency. Use the official [quota-cost documentation](https://developers.google.com/youtube/v3/determine_quota_cost) rather than hard-coding assumptions about request cost.

*YouTube Data API 请求会消耗项目配额。应按 `platform + source_id` 缓存稳定元数据、避免重复轮询，并在提高 Worker 并发前监控配额使用情况。请求成本应以官方 [配额成本文档](https://developers.google.com/youtube/v3/determine_quota_cost) 为准，不要在代码中长期硬编码假设。*

Do not commit browser cookies, OAuth refresh tokens, client secrets, or API keys. Store them in a cloud secret manager, encrypt refresh tokens at rest, and separate credentials by environment.

*不要提交浏览器 Cookie、OAuth refresh token、client secret 或 API Key。生产环境应使用云端密钥管理服务，静态加密 refresh token，并按环境隔离凭据。*

### Adding another platform / 接入其他平台

To add Vimeo, a podcast host, or another supported video platform, implement one adapter and keep the downstream pipeline unchanged:

*要接入 Vimeo、播客平台或其他视频平台，只需实现一个适配器，下游分析流水线保持不变：*

1. Detect and canonicalize the source URL; derive a stable platform-specific source ID.
   *识别并规范化来源 URL，生成稳定的平台来源 ID。*
2. Define the authentication strategy: anonymous, API key, OAuth, or user-provided credentials.
   *定义认证策略：匿名、API Key、OAuth 或用户提供的凭据。*
3. Retrieve metadata and subtitle tracks, then map them into the normalized source schema.
   *获取元数据和字幕轨道，并映射到统一来源结构。*
4. Implement audio-only and video acquisition with bounded retries, timeouts, and format fallbacks.
   *实现音频与视频获取，并提供有上限的重试、超时和格式回退。*
5. Add fixtures for URL matching, subtitle selection, metadata normalization, and download failure handling.
   *为 URL 匹配、字幕选择、元数据标准化和下载失败处理补充测试。*
6. Add platform-specific metrics for acquisition latency, subtitle hit rate, failure rate, and API quota usage.
   *补充平台级指标，包括获取延迟、字幕命中率、失败率和 API 配额使用量。*

This adapter boundary also makes it possible to replace `yt-dlp` for one platform without changing MongoDB, queues, ASR, model prompts, or report rendering.

*该适配器边界还允许只替换某个平台的 `yt-dlp` 获取方式，而无需修改 MongoDB、任务队列、ASR、模型提示词或报告渲染。*

## API Overview / API 概览

| Method | Path | Description / 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/health` | API, MongoDB, and Redis health / API、MongoDB 与 Redis 健康检查 |
| `POST` | `/api/v1/tasks/url` | Submit a video URL / 提交视频链接 |
| `POST` | `/api/v1/tasks/upload` | Upload a local video / 上传本地视频 |
| `GET` | `/api/v1/tasks` | List recent tasks / 获取最近任务 |
| `GET` | `/api/v1/tasks/{task_id}` | Get task state / 获取任务状态 |
| `POST` | `/api/v1/tasks/{task_id}/cancel` | Request task cancellation / 请求取消任务 |
| `GET` | `/api/v1/tasks/{task_id}/report` | Get the generated report / 获取生成的报告 |

## Data and Persistence / 数据与持久化

Task artifacts are written to the host through the Compose bind mount:

*任务产物通过 Compose bind mount 写入宿主机：*

```text
data/tasks/<task-id>/
├── source/
├── audio/
├── metadata.json
├── transcript.json
├── analysis.json
├── summary.json
├── report.md
└── state.json
```

`data/` is excluded from Git because it may contain source video, audio, subtitles, frames, and generated reports. MongoDB data is stored in the Docker named volume `mongo_data`, not directly in the project folder.

*`data/` 可能包含源视频、音频、字幕、画面和报告，因此已从 Git 排除。MongoDB 数据保存在 Docker named volume `mongo_data` 中，而不是直接保存在项目目录。*

## Performance Configuration / 性能配置

| Variable | Default | Purpose / 作用 |
| --- | ---: | --- |
| `FAST_SUMMARY_MODEL` | `qwen3.7-flash` | Low-latency summary model / 低延迟总结模型 |
| `FAST_ASR_MODEL` | `qwen3-asr-flash` | Fast-mode ASR fallback / 极速模式 ASR 回退模型 |
| `FAST_ASR_CHUNK_SECONDS` | `180` | Fast ASR chunk duration / 极速 ASR 分片长度 |
| `FAST_ASR_PARALLELISM` | `4` | Maximum concurrent ASR chunks / ASR 分片最大并发数 |
| `MAX_PARALLEL_SEGMENT_ANALYSES` | `2` | Standard/Detailed segment concurrency / 标准和深度片段并发数 |
| `DOWNLOAD_ATTEMPTS` | `3` | Top-level download attempts / 下载总尝试次数 |

See [`.env.example`](.env.example) for the complete configuration.

*完整配置请查看 [`.env.example`](.env.example)。*

## Operations / 常用运维

```powershell
# List all containers, including exited containers / 查看所有容器
docker compose ps -a

# Follow Worker logs; Ctrl+C stops log following only / 跟踪 Worker 日志
docker compose logs -f --since=10m worker

# Inspect frontend and API errors / 查看前端和 API 错误
docker compose logs --tail=200 frontend api

# Rebuild backend services after pipeline changes / 修改流水线后重建后端
docker compose up -d --build --force-recreate api worker
```

## Testing / 测试

The pipeline unit tests do not call paid model APIs:

*流水线单元测试不会调用付费模型 API：*

```powershell
Set-Location Project-ML
python -m unittest discover -s tests -v
```

Validate the Docker deployment with:

*通过以下命令验证 Docker 部署：*

```powershell
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://localhost:8080/api/v1/health
```

For platform migration, keep a small authorized test matrix containing one captioned video, one video without captions, and one inaccessible/private video per platform. Measure end-to-end latency, acquisition time, subtitle hit rate, ASR fallback rate, and task success rate.

*进行平台迁移测试时，每个平台至少准备一个有字幕视频、一个无字幕视频和一个无权限/私有视频。记录端到端耗时、获取耗时、字幕命中率、ASR 回退率和任务成功率。*

## Project Layout / 项目结构

```text
.
├── backend/                 # FastAPI, MongoDB repository, RQ Worker
├── frontend/                # React/Vite frontend and Nginx config
├── Project-ML/              # Core video-analysis pipeline
├── data/tasks/              # Local task artifacts; excluded from Git
├── compose.yaml             # Full local deployment
├── .env.example             # Environment template
├── README.md
└── SECURITY.md
```

## Production and Security / 生产与安全

The current release does not yet provide login, per-user authorization, quotas, or tenant isolation. Do not expose it directly to the public internet. Before production deployment, add authentication, HTTPS, rate limiting, task ownership checks, resource quotas, observability, and private-network isolation for MongoDB, Redis, and FastAPI. At larger scale, move source media and report assets to an OSS/S3-compatible object store.

*当前版本尚未提供登录、用户级授权、配额和租户隔离，请勿直接暴露到公网。生产部署前应增加身份认证、HTTPS、限流、任务所有权检查、资源配额和可观测性，并将 MongoDB、Redis 与 FastAPI 隔离在私有网络中。规模扩大后，应把源媒体和报告附件迁移到 OSS/S3 兼容对象存储。*

See [`SECURITY.md`](SECURITY.md) for credential and privacy guidance.

*密钥与隐私说明请查看 [`SECURITY.md`](SECURITY.md)。*

