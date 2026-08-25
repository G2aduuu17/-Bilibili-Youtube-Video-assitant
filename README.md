# Bilibili / YouTube Video Assistant

一个可本地运行或部署到云服务器的 AI 视频理解系统。它接收 Bilibili、YouTube 等 `yt-dlp` 可解析的视频链接或本地视频，完成字幕获取、语音识别、多模态分析和结构化报告生成。

> 项目处于可运行的早期版本。请只处理你有权访问、下载和分析的内容，并遵守内容平台条款及所在地法律法规。

## 主要能力

- 支持视频链接与本地文件上传
- 字幕优先，缺少字幕时自动回退到语音识别
- 极速、标准、深度三条相互隔离的分析链路
- ASR 与抽帧并行，同一视频的音频分片可并发识别
- 多模态片段分析与可选的专名联网核验
- 后台任务队列、失败状态记录与任务取消
- MongoDB 保存任务、报告和片段数据
- Markdown 报告在线展示和下载
- Docker Compose 一键启动完整系统

## 三种分析模式

| 模式 | 执行路径 | 适用场景 |
| --- | --- | --- |
| 极速 | 平台字幕直接总结；无字幕时仅下载音频，按 180 秒切片并发 ASR，再使用 Flash 模型总结 | 快速了解 10～15 分钟视频 |
| 标准 | 最高 720p 视频，ASR 与抽帧并行，按较大时间段进行多模态分析 | 日常报告，平衡速度与信息量 |
| 深度 | 高质量视频、细粒度多模态片段和可选实体核验 | 需要画面依据和完整分析的内容 |

极速模式的工程目标是让有字幕的 10～15 分钟视频尽量在 2 分钟内完成，但实际耗时仍受平台下载速度、视频字幕、DashScope 服务和网络质量影响，这不是硬性 SLA。

## 系统架构

```mermaid
flowchart LR
    U["浏览器"] --> N["Nginx + React"]
    N --> A["FastAPI"]
    A --> M["MongoDB"]
    A --> R["Redis / RQ"]
    R --> W["分析 Worker"]
    W --> P["Project-ML Pipeline"]
    P --> Y["yt-dlp / FFmpeg"]
    P --> Q["DashScope ASR + Qwen"]
    P --> F["./data/tasks"]
    W --> M
```

| 组件 | 作用 |
| --- | --- |
| React + Nginx | 创建任务、查看状态和展示报告，同时反向代理 API |
| FastAPI | 提供任务、报告和文件接口 |
| Redis + RQ | 异步调度耗时的视频分析任务 |
| Worker | 执行下载、ASR、多模态分析和报告生成 |
| MongoDB | 保存任务状态、报告正文和片段结构化数据 |
| `./data/tasks` | 保存源文件和流水线中间产物，映射到宿主机便于管理 |

## 快速开始

### 环境要求

- Docker Desktop，使用 Linux Containers / WSL 2 backend
- 阿里云百炼 DashScope API Key
- 能访问目标视频站点和 DashScope 的网络
- 建议至少 4 核 CPU、8 GB 内存和充足磁盘空间

### 1. 获取代码

```powershell
git clone https://github.com/G2aduuu17/-Bilibili-Youtube-Video-assitant.git
cd .\-Bilibili-Youtube-Video-assitant
```

### 2. 配置环境变量

```powershell
Copy-Item .env.example .env
notepad .env
```

至少设置以下字段：

```dotenv
APP_SECRET=替换为随机长字符串
MONGO_INITDB_ROOT_PASSWORD=替换为强密码
MONGODB_URL=mongodb://video_devour:同一个Mongo密码@mongo:27017/video_devour?authSource=admin
DASHSCOPE_API_KEY=你的DashScope_API_Key
```

不要提交 `.env`。仓库只提供不含真实密钥的 `.env.example`。

### 3. 启动系统

```powershell
docker compose up -d --build
docker compose ps
```

打开：

- Web 界面：<http://localhost:8080>
- 健康检查：<http://localhost:8080/api/v1/health>

首次构建需要下载 Docker 镜像和依赖，时间会明显长于后续启动。平时重新打开项目只需要：

```powershell
docker compose up -d
```

停止系统：

```powershell
docker compose down
```

`docker compose down` 不会删除 `./data/tasks`、MongoDB volume 或 Redis volume。不要使用 `docker compose down -v`，除非明确要删除数据库和队列数据。

## 常用运维命令

```powershell
# 查看所有容器，包括已退出的容器
docker compose ps -a

# 跟踪 Worker 日志，按 Ctrl+C 退出日志查看
docker compose logs -f --since=10m worker

# 查看前端和 API 最近的错误
docker compose logs --tail=200 frontend api

# 只重新构建后端分析服务
docker compose up -d --build --force-recreate api worker
```

## API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/health` | MongoDB、Redis 与 API 健康检查 |
| `POST` | `/api/v1/tasks/url` | 提交视频链接 |
| `POST` | `/api/v1/tasks/upload` | 上传本地视频 |
| `GET` | `/api/v1/tasks` | 获取最近任务 |
| `GET` | `/api/v1/tasks/{task_id}` | 获取任务状态 |
| `POST` | `/api/v1/tasks/{task_id}/cancel` | 请求取消任务 |
| `GET` | `/api/v1/tasks/{task_id}/report` | 获取生成的报告 |

链接任务示例：

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

## 数据目录

每个任务的本地文件保存在：

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

`data/` 已从 Git 排除。MongoDB 数据保存在 Docker named volume `mongo_data` 中，不会出现在项目文件夹里。

## 关键性能配置

| 环境变量 | 默认值 | 说明 |
| --- | ---: | --- |
| `FAST_SUMMARY_MODEL` | `qwen3.7-flash` | 极速总结模型 |
| `FAST_ASR_MODEL` | `qwen3-asr-flash` | 极速模式无字幕时的 ASR 模型 |
| `FAST_ASR_CHUNK_SECONDS` | `180` | 极速 ASR 分片长度 |
| `FAST_ASR_PARALLELISM` | `4` | 极速 ASR 最大并发数 |
| `MAX_PARALLEL_SEGMENT_ANALYSES` | `2` | 标准和深度模式片段并发数 |
| `DOWNLOAD_ATTEMPTS` | `3` | 视频下载尝试次数 |

完整配置请查看 [`.env.example`](.env.example)。

## 测试

流水线单元测试不调用付费模型：

```powershell
Set-Location Project-ML
python -m unittest discover -s tests -v
```

测试 Docker 部署：

```powershell
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://localhost:8080/api/v1/health
```

## 项目结构

```text
.
├── backend/                 # FastAPI、MongoDB repository 和 RQ Worker
├── frontend/                # React/Vite 前端与 Nginx 配置
├── Project-ML/              # 视频分析核心流水线
├── data/tasks/              # 本地任务文件，不提交 Git
├── compose.yaml             # 完整本地部署
├── .env.example             # 环境变量模板
└── README.md
```

## 生产部署提醒

当前版本没有用户登录、配额和租户隔离，不应原样暴露到公网。正式部署前至少需要增加身份认证、HTTPS、请求限流、任务配额、日志与监控，并将 MongoDB、Redis 和 FastAPI 保持在私有网络中。规模扩大后，建议把源视频和报告附件迁移到 OSS/S3 兼容对象存储。

安全问题与密钥处理说明见 [`SECURITY.md`](SECURITY.md)。
