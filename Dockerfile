# --- 1단계: React 빌드 ---
FROM node:22-slim AS ui-build
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# vite.config.ts의 outDir(../app/static/ui)에 맞춰 컨테이너의 /app/static/ui 에 생성됨.

# --- 2단계: Python 런타임 ---
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY app ./app
# React 빌드 산출물 복사 (ui-build 단계에서 /app/static/ui 로 생성된 것)
COPY --from=ui-build /app/static/ui ./app/static/ui
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
