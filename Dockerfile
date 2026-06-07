# GalaxyOS Docker 镜像
# 用法:
#   docker build -t galaxyos .
#   docker run -v $(pwd)/config:/app/config galaxyos health
#
FROM python:3.12-slim

LABEL org.opencontainers.image.title="GalaxyOS"
LABEL org.opencontainers.image.description="OpenClaw 认知增强引擎"
LABEL org.opencontainers.image.version="6.1.0"

# 系统依赖
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安装项目
COPY . .
RUN pip install --no-cache-dir -e .

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import services; print('OK')" || exit 1

# 默认运行健康检查
ENTRYPOINT ["python", "-m", "services.xiaoyi_claw_api"]
CMD ["health"]
