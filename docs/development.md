# Development Guide | 开发者指南

> This guide covers development environment setup, debugging tips, and common
> development tasks for agentinsight-researcher.
>
> 本指南涵盖 agentinsight-researcher 的开发环境搭建、调试技巧和常见开发任务。

---

## Table of Contents | 目录

1. [Environment Setup | 环境搭建](#environment-setup--环境搭建)
2. [Debugging | 调试技巧](#debugging--调试技巧)
3. [Common Development Tasks | 常见开发任务](#common-development-tasks--常见开发任务)
4. [Code Style | 代码风格](#code-style--代码风格)
5. [Testing | 测试](#testing--测试)

---

## Environment Setup | 环境搭建

### Prerequisites | 前置要求

- Python ≥ 3.12
- Docker & Docker Compose
- Git

### Quick Start | 快速开始

```bash
# 1. Clone the repository
git clone https://github.com/AgentInsight/agentinsight-researcher.git
cd agentinsight-researcher

# 2. Create virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

# 3. Install dependencies (modern way with pyproject.toml)
pip install -e ".[dev]"

# 4. Install pre-commit hooks
pre-commit install

# 5. Configure environment
copy .env.template .env  # Windows
cp .env.template .env    # Linux/macOS
# Edit .env with your API keys

# 6. Start infrastructure services
docker compose -p agentinsight up -d

# 7. Run the development server
uvicorn server:app --host 0.0.0.0 --port 8066 --reload
```

### Alternative: Requirements-based Install | 基于依赖文件的安装

If you prefer the traditional `requirements.txt` approach:

```bash
pip install -r requirements-dev.txt
```

### Infrastructure Services | 基础设施服务

The project requires 7 Docker containers (see [deployment.md](deployment.md)
for details):

| Service | Port | Purpose |
|---------|------|---------|
| agent | 8066 | FastAPI application |
| postgres | 5432 | Checkpointer + business tables |
| redis | 6379 | Cache + rate limiting |
| qdrant | 6333 | Vector database |
| embeddings | 8088 | TEI embedding service (bge-base-zh-v1.5) |
| searxng | 8099 | Meta search engine |
| rerank (optional) | 8089 | Rerank service |

---

## Debugging | 调试技巧

### Log Levels | 日志级别

```bash
# Set log level via environment variable
LOG_LEVEL=DEBUG uvicorn server:app --port 8066 --reload
```

### Single Node Debugging | 单节点调试

To debug a specific LangGraph node in isolation:

```python
import asyncio
from src.graph.state import ResearcherState
from src.graph.nodes import plan_node

async def debug_plan():
    state = ResearcherState(query="AI 发展趋势")
    result = await plan_node(state)
    print(result)

asyncio.run(debug_plan())
```

### Database Inspection | 数据库检查

```bash
# Connect to PostgreSQL
docker exec -it agentinsight-postgres-1 psql -U $POSTGRES_USER -d agents

# Check business tables
\dt
SELECT * FROM research_sessions LIMIT 5;
SELECT * FROM research_reports LIMIT 5;

# Check LangGraph checkpoints
SELECT * FROM checkpoints LIMIT 5;
```

### Qdrant Inspection | Qdrant 检查

```bash
# Check collection info
curl http://localhost:6333/collections/agents

# Search vectors (with API key)
curl -H "api-key: $QDRANT_API_KEY" \
  http://localhost:6333/collections/agents/points/search \
  -d '{"vector": [0.1, ...], "limit": 5}'
```

### Redis Inspection | Redis 检查

```bash
# Connect to Redis
docker exec -it agentinsight-redis-1 redis-cli -a $REDIS_AUTH

# Check keys
KEYS agentinsight-researcher:*
TTL agentinsight-researcher:default:default:cache:*
```

---

## Common Development Tasks | 常见开发任务

### Add a New Search Engine | 新增搜索引擎

1. Create `src/skills/researcher/searchers/<engine_name>.py`:

```python
"""<EngineName> search engine."""
from __future__ import annotations
import httpx
from src.skills.researcher.searchers.base import BaseSearcher, SearchHit

class <EngineName>Searcher(BaseSearcher):
    async def search(self, query: str, max_results: int = 10) -> list[SearchHit]:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.example.com/search", params={...})
            # Parse response and return SearchHit list
            ...
```

2. Register in `src/skills/researcher/searchers/__init__.py`

3. Add configuration in `src/config/settings.py` if API key needed

4. Add unit test in `tests/unit/test_<engine_name>_searcher.py`

### Add a New Scraper | 新增抓取器

1. Create `src/skills/researcher/scrapers/<name>_scraper.py`:

```python
"""<Name> web scraper."""
from src.skills.researcher.scrapers.base import BaseScraper, ScrapeResult

class <Name>Scraper(BaseScraper):
    async def scrape(self, url: str) -> ScrapeResult:
        # Implement scraping logic
        ...
```

2. Register in `src/skills/researcher/scrapers/__init__.py`

3. Add to fallback chain if applicable

### Add a New Agent Node | 新增 Agent 节点

1. Define node function in `src/graph/nodes.py`:

```python
async def my_node(state: ResearcherState) -> dict:
    """Single responsibility, no side effects."""
    # Process state
    return {"delta_field": result}  # Return delta, not full state
```

2. Add to graph builder in `src/graph/builder.py`:

```python
graph.add_node("my_node", my_node)
graph.add_edge("previous_node", "my_node")
graph.add_conditional_edges("my_node", router_fn, mapping)
```

3. Add `max_iterations` counter if node is in a loop

### Add a New Prompt | 新增提示词

1. Add abstract method to `PromptFamily` in `src/skills/researcher/prompts.py`

2. Implement in `DefaultPromptFamily` and `EnglishPromptFamily`

3. Add prompt template files if large: `src/config/researcher/prompts/`

---

## Code Style | 代码风格

The project uses `ruff` for linting and formatting, `mypy --strict` for type
checking.

```bash
# Check code
ruff check .
ruff format --check .

# Fix auto-fixable issues
ruff check . --fix
ruff format .

# Type check
mypy src/ --strict
```

### Key Rules | 关键规则

- Line length: 100 characters
- Import order: stdlib → third-party → local (ruff handles automatically)
- Type hints: required on all functions (mypy --strict)
- Docstrings: required on public classes and functions
- No global mutable state: use state/deps pattern

---

## Testing | 测试

### Test Layers | 测试分层

| Layer | Directory | When to Run |
|-------|-----------|-------------|
| Unit | `tests/unit/` | Every commit |
| Functional | `tests/functional/` | After container stack is healthy |
| API | `tests/api/` | After container stack is healthy |
| Regression | `tests/regression/` | Before merging to main |
| E2E | `tests/e2e/` | Before release |

```bash
# Run unit tests (no external services needed)
pytest tests/unit/ -q

# Run with coverage
pytest tests/unit/ --cov=src --cov-report=term-missing

# Run all tests (requires container stack running)
pytest tests/ -q
```

### Writing Tests | 编写测试

- Tests must be independent and repeatable
- Use fixtures to clean up state between tests
- Test data isolation: use `namespace=test_*` / `user_id=test_*` / `session_id=test_*`
- Mock external services in unit tests; use real services in functional tests

---

## Pre-commit Hooks | 预提交钩子

The project includes `.pre-commit-config.yaml` with ruff, mypy, and common
file checks. After `pre-commit install`, hooks run automatically on `git commit`.

```bash
# Run hooks manually on all files
pre-commit run --all-files
```

---

## Need Help? | 需要帮助？

- Read [FAQ](faq.md) for common questions
- Check [architecture.md](architecture.md) for system design
- Check [deployment.md](deployment.md) for deployment issues
- Open an [Issue](https://github.com/AgentInsight/agentinsight-researcher/issues)
  for bugs or feature requests
