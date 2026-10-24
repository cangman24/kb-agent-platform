# ---------------------------------------------------------------------------
# Dockerfile —— 运行时镜像
#
# ★ 设计取舍（与 domain-rag-qa 不同）：
#   本仓库**不打包嵌入模型**（内置知识库走 BM25，零 ML 依赖），
#   所以镜像只有 ~200MB，而不是 6GB。这是刻意的：Agent 的编排与管控
#   逻辑跟向量检索是两件事，不该被模型体积绑死。
#
#   ⚠️ 未验证声明：本 Dockerfile 尚未在本机构建过（无 Docker 环境）。
#      逻辑经过审阅，但首次构建可能需要调整。
#
# 构建与运行：
#     docker build -t kb-agent-platform .
#     docker run --rm -p 8000:8000 --env-file .env kb-agent-platform
# ---------------------------------------------------------------------------
FROM python:3.11-slim

LABEL org.opencontainers.image.title="kb-agent-platform" \
      org.opencontainers.image.description="受控知识库 Agent：白名单、熔断、Checkpoint 续跑、SSE 轨迹" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖层单独先装：改代码时不会让 pip 重装一遍
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir -e ".[llm,serve]" 2>/dev/null || \
    pip install --no-cache-dir fastapi "uvicorn[standard]" openai python-dotenv

# 源码与内置知识库
COPY core/ ./core/
COPY kb_agent/ ./kb_agent/
COPY data/ ./data/

# ★ 非 root 运行：容器里跑 Agent 更要收紧权限
RUN useradd --create-home --shell /bin/bash appuser && \
    mkdir -p data/checkpoints data/cache && \
    chown -R appuser:appuser /app
USER appuser

# 运行时目录挂卷：checkpoint 与 LLM 缓存不该随容器销毁
VOLUME ["/app/data/checkpoints", "/app/data/cache"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; \
        urllib.request.urlopen('http://127.0.0.1:8000/health').read()" || exit 1

CMD ["python", "-m", "kb_agent.server", "--host", "0.0.0.0", "--port", "8000"]
