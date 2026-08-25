# VideoDevour Online MVP

这是面向在线部署的第一版：React 前端负责任务创建、进度和报告展示；FastAPI
负责 API；Redis/RQ 调度后台任务；MongoDB 保存任务和报告元数据；`Project-ML`
继续承担实际的视频分析。

## 本地启动

1. 将 `.env.example` 复制为 `.env`，填入 `DASHSCOPE_API_KEY`。
2. 安装前后端依赖。
3. 执行 `docker compose up --build`。
4. 访问 `http://localhost:8080`。

默认只启动一个分析 Worker。视频分析会使用 FFmpeg、yt-dlp、阿里云百炼 ASR 和
Qwen，因此请确认服务器具备网络访问权限、足够的磁盘空间，并且只处理拥有授权的内容。

## 在线部署

生产环境应把前端容器映射到 80/443，并使用外层 Nginx、Caddy 或云负载均衡终止 HTTPS。
MongoDB、Redis 和 FastAPI 不应直接暴露公网端口。源视频和关键帧应在规模扩大后迁移到
OSS/S3；MongoDB 仅存放任务、报告和文件元数据。

