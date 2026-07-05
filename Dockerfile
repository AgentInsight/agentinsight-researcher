# agentinsight-researcher Dockerfile (联网模式)
# 严格遵循 AGENTS.md 第 12 章: 多阶段构建 + 非 root + python:3.12-slim
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
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libxml2 \
    libxslt1.1 \
    libpq5 \
    fonts-dejavu-core \
    ca-certificates \
    curl \
    gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
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

# 创建非 root 用户 (AGENTS.md 第 12 章)
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent \
    && mkdir -p /app/data/sessions /tmp/uploads \
    && chown -R agent:agent /app /tmp/uploads

# 复制业务代码
COPY --chown=agent:agent . .

USER agent

EXPOSE 8066

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import httpx; r=httpx.get('http://localhost:8066/health', timeout=5.0); assert r.status_code==200" || exit 1

CMD ["python", "server.py"]
