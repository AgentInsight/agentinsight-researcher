# agentinsight-researcher Dockerfile (联网模式)
# 多阶段构建 + 非 root + python:3.12-slim
# 联网模式: 构建时从 PyPI 下载 Python 依赖, apt-get 安装系统依赖
# 适用于开源社区贡献者快速起栈

# ========== Builder 阶段: 联网安装 Python 依赖 ==========
FROM python:3.12-slim AS builder

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 安装 cairo 系统库 (cairosvg 依赖, DOCX/PDF SVG 配图渲染)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libcairo2-dev \
    && rm -rf /var/lib/apt/lists/*

# 联网安装 Python 依赖 (从 PyPI 下载, 联网模式)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ========== Runtime 阶段 ==========
FROM python:3.12-slim AS runtime

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 联网安装运行时共享库 (WeasyPrint/lxml/psycopg 依赖) + Node.js 22 LTS (MCP 运行时)
# 联网模式: apt-get 从 NodeSource 仓库安装 Node.js (MCP 服务依赖 npx)
# 同时安装 uv (uvx 运行 PyPI MCP 服务, 如 mcp-server-fetch/mcp-server-git 等)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libxml2 \
    libxslt1.1 \
    libpq5 \
    fonts-dejavu-core \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    ca-certificates \
    curl \
    gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -sf /root/.local/bin/uv /usr/local/bin/uv \
    && ln -sf /root/.local/bin/uvx /usr/local/bin/uvx \
    && uv --version && uvx --version \
    && rm -rf /var/lib/apt/lists/*

# 联网安装 Playwright chromium (JS 渲染抓取, 方案 E)
# 联网模式: playwright install --with-deps 自动安装 chromium + 系统依赖
# 注意: 必须在切换非 root 用户前安装 (需要 root 权限安装系统依赖)
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN pip install playwright>=1.49 \
    && playwright install --with-deps chromium \
    && chmod -R a+rx /opt/pw-browsers

# 复制 Python site-packages (含已安装依赖)
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 创建非 root 用户
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent \
    && mkdir -p /app/data/sessions /tmp/uploads \
    && chown -R agent:agent /app /tmp/uploads

# 复制业务代码
COPY --chown=agent:agent . .

# 生产联网模式: FastEmbed 模型在运行时自动从 HuggingFace 下载
# 如需使用本地预下载模型, 在 docker-compose.yml 中挂载 bind mount:
#   - ./packages/models/bge-small-zh-v1.5-onnx:/app/packages/models/bge-small-zh-v1.5-onnx:ro

USER agent

# P0-2 修复: 验证 litellm 已正确安装 (构建时强校验, 避免运行时 ModuleNotFoundError)
# 重建镜像须加 --no-cache: docker compose -p agentinsight build --no-cache (确保 litellm 被重新安装)
# 注意: litellm 库不暴露 __version__ 属性, 用 importlib.metadata.version 读取
RUN python -c "import litellm; from importlib.metadata import version; print(f'litellm version: {version(\"litellm\")}')" || \
    (echo "ERROR: litellm not installed, rebuild required" && exit 1)

EXPOSE 8066

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import httpx; r=httpx.get('http://localhost:8066/health', timeout=5.0); assert r.status_code==200" || exit 1

CMD ["python", "server.py"]
