# Third-Party Licenses
# 第三方许可证清单

This file lists the licenses of third-party dependencies used by
agentinsight-researcher.

本文件列出 agentinsight-researcher 所使用的第三方依赖及其许可证。

> 许可证信息来源于 PyPI 分类器, 仅供参考。
> License information sourced from PyPI classifiers, for reference only.
> 对于标注 "See PyPI" 的包, 请以 PyPI 页面为准。
> For packages marked "See PyPI", please refer to the PyPI page.

## Runtime Dependencies
## 运行时依赖

| Package | Version | License |
|---------|---------|---------|
| langgraph | 1.2.8 | MIT |
| langgraph-checkpoint | 4.1.1 | MIT |
| langgraph-checkpoint-postgres | 3.1.0 | MIT |
| langchain-core | 1.4.8 | MIT |
| langchain-mcp-adapters | 0.3.0 | MIT |
| langchain-protocol | 0.0.18 | MIT |
| litellm | 1.91.0 | MIT |
| openai | 2.44.0 | Apache-2.0 |
| mcp | 1.28.1 | MIT |
| fastapi | 0.139.0 | MIT |
| uvicorn[standard] | 0.50.2 | BSD-3-Clause |
| sse-starlette | 3.4.5 | BSD-3-Clause |
| starlette | 1.3.1 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| aiofiles | 25.1.0 | Apache-2.0 OR MIT |
| pydantic | 2.13.4 | MIT |
| pydantic-settings | 2.14.2 | MIT |
| psycopg[binary] | 3.3.4 | LGPL-3.0-or-later |
| psycopg-pool | 3.3.1 | LGPL-3.0-or-later |
| redis | 8.0.1 | MIT |
| asyncpg | 0.31.0 | Apache-2.0 |
| qdrant-client | 1.18.0 | Apache-2.0 |
| fastembed | 0.8.0 | MIT |
| onnxruntime | 1.27.0 | MIT |
| rank-bm25 | 0.2.2 | MIT |
| jieba | 0.42.1 | MIT |
| agentinsight-sdk | 0.1.5 | MIT |
| opentelemetry-sdk | 1.43.0 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-http | 1.43.0 | Apache-2.0 |
| ddgs | 9.14.4 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| beautifulsoup4 | 4.15.0 | MIT |
| lxml | 6.1.1 | BSD-3-Clause |
| playwright | 1.61.0 | Apache-2.0 |
| pypdf | 5.6.0 | BSD-3-Clause |
| trafilatura | 2.1.0 | Apache-2.0 |
| markdownify | 1.2.3 | MIT |
| curl_cffi | >=0.7 | MIT |
| python-docx | 1.2.0 | MIT |
| openpyxl | 3.1.5 | MIT |
| python-pptx | 1.0.2 | MIT (BSD-2-Clause) |
| markitdown | 0.1.6 | MIT |
| jinja2 | 3.1.6 | BSD-3-Clause |
| weasyprint | 69.0 | BSD-3-Clause |
| mistune | 3.3.2 | BSD-3-Clause |
| tiktoken | 0.13.0 | MIT |
| json-repair | 0.61.2 | MIT |
| tenacity | 9.1.4 | Apache-2.0 OR MIT |
| orjson | 3.11.9 | Apache-2.0 OR MIT |
| pyyaml | 6.0.3 | MIT |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| cachetools | 7.1.4 | MIT |
| numpy | 2.5.1 | BSD-3-Clause |

## Development Dependencies
## 开发依赖

| Package | Version | License |
|---------|---------|---------|
| ragas | 0.2.0 | Apache-2.0 |
| deepeval | 2.0.0 | Apache-2.0 |
| pytest | 8.3.0 | MIT |
| pytest-asyncio | 0.24.0 | MIT |
| pytest-cov | 6.0.0 | MIT |
| ruff | 0.8.0 | MIT |
| mypy | 1.13.0 | MIT |
| pip-tools | 7.4.1 | BSD-3-Clause |

## License Compatibility Notes
## 许可证兼容性说明

- **MIT / Apache-2.0 / BSD**: Permissive licenses, fully compatible with the
  project's MIT license. 宽松许可证, 与项目 MIT 许可证完全兼容。
- **LGPL-3.0-or-later** (psycopg / psycopg-pool): Used as a system library
  via pip import, does not impose copyleft on the project. 作为系统库通过
  pip 导入使用, 不对项目施加 copyleft 约束。
- For packages with dual licenses (e.g. "Apache-2.0 OR MIT"), either license
  may be chosen. 双许可证包可选择其中任一。

> Generated from requirements.txt and requirements-dev.txt.
> License information sourced from PyPI classifiers.
