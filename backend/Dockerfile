FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/Project-ML

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /tmp/backend-requirements.txt
COPY Project-ML/requirements.txt /tmp/project-ml-requirements.txt
RUN pip install --no-cache-dir -r /tmp/backend-requirements.txt \
    && pip install --no-cache-dir -r /tmp/project-ml-requirements.txt

COPY backend/app /app/app
COPY Project-ML /app/Project-ML

RUN mkdir -p /data/tasks

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

