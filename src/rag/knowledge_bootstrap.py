"""GICS 行业知识库 Bootstrap.

AGENTS.md 第 7 章硬约束:
- Qdrant 单一集合 agents, payload namespace 隔离
- 共享知识库: namespace = agent_id (不含 user_id, 所有用户共享)
- 点 id 用 uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}") 幂等生成

Agent 容器启动时读取 config/researcher/industry_prompts/*.yaml (68 套 GICS 行业),
为每个行业构建描述文本, 经 EmbeddingsClient 嵌入后写入 Qdrant (upsert 语义):
- 相同 namespace+content_hash 生成相同 point_id, 天然支持更新 (覆盖旧数据)
- 共享 namespace = agent_id, 所有用户可检索 (IndustryClassifier 使用)
- 失败不阻断启动, 仅告警
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import yaml

from src.config.settings import Settings, get_settings
from src.rag.qdrant_manager import QdrantManager

logger = logging.getLogger(__name__)

# 行业提示词目录: 项目根/config/researcher/industry_prompts/
# Agent 容器内: /app/config/researcher/industry_prompts/ (Dockerfile COPY . . 已包含)
PROMPTS_DIR = Path(__file__).parent.parent.parent / "config" / "researcher" / "industry_prompts"


def _load_industry_yamls(yaml_files: list[Path]) -> list[dict[str, Any]]:
    """同步批量读取行业 YAML 文件 (在 asyncio.to_thread 中执行, 避免阻塞事件循环).

    ruff ASYNC230: async 函数禁止用阻塞 open, 故抽取为同步函数.
    """
    results: list[dict[str, Any]] = []
    for yaml_file in yaml_files:
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data or not data.get("industry_code"):
                continue
            results.append({**data, "_source_file": yaml_file.name})
        except Exception as e:  # noqa: BLE001
            logger.warning("解析行业 YAML 失败 %s: %s", yaml_file.name, e)
            continue
    return results


def _build_industry_description(yaml_data: dict[str, Any]) -> str:
    """构建行业描述文本 (用于 embedding).

    将 YAML 中的关键字段拼接为一段语义丰富的中文描述,
    使 IndustryClassifier 的向量检索能根据用户研究请求命中正确行业.
    """
    parts: list[str] = []
    if yaml_data.get("industry_name"):
        parts.append(f"行业名称: {yaml_data['industry_name']}")
    if yaml_data.get("industry_sector"):
        parts.append(f"行业部门: {yaml_data['industry_sector']}")
    if yaml_data.get("industry_group"):
        parts.append(f"行业集团: {yaml_data['industry_group']}")
    if yaml_data.get("industry_sub"):
        parts.append(f"子行业: {yaml_data['industry_sub']}")
    if yaml_data.get("industry_code"):
        parts.append(f"行业代码: {yaml_data['industry_code']}")

    key_dims = yaml_data.get("key_dimensions")
    if isinstance(key_dims, list) and key_dims:
        parts.append("关键研究维度: " + ", ".join(str(d) for d in key_dims))

    data_sources = yaml_data.get("data_sources_preference")
    if isinstance(data_sources, list) and data_sources:
        parts.append("优先数据来源: " + ", ".join(str(s) for s in data_sources))

    return "\n".join(parts)


async def bootstrap_industry_knowledge(settings: Settings | None = None) -> bool:
    """初始化 GICS 行业知识库 (Agent 启动时触发).

    读取 config/researcher/industry_prompts/*.yaml, 为每个行业构建描述文本,
    经 EmbeddingsClient 嵌入后写入 Qdrant (共享 namespace = agent_id, upsert 语义).

    幂等性:
    - point_id = uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}")
    - 相同 namespace+content_hash 生成相同 point_id, upsert 覆盖旧数据
    - 行业描述内容变更 → content_hash 变更 → 新 point_id (旧数据残留, 可接受)

    Returns:
        True 成功, False 失败 (不阻断启动).
    """
    settings = settings or get_settings()

    if not PROMPTS_DIR.exists():
        logger.warning("行业提示词目录不存在: %s, 跳过 GICS 知识库 bootstrap", PROMPTS_DIR)
        return False

    yaml_files = sorted(PROMPTS_DIR.glob("*.yaml"))
    if not yaml_files:
        logger.warning("行业提示词目录为空: %s, 跳过 GICS 知识库 bootstrap", PROMPTS_DIR)
        return False

    qdrant = QdrantManager(settings)

    try:
        # 1. 确保集合存在
        await qdrant.ensure_collection()

        # 2. 批量读取行业 YAML (同步 IO, 用 asyncio.to_thread 避免阻塞事件循环)
        industry_list = await asyncio.to_thread(_load_industry_yamls, yaml_files)

        # 3. 构建行业描述点
        points: list[dict[str, Any]] = []
        for data in industry_list:
            content = _build_industry_description(data)
            if not content:
                continue

            points.append(
                {
                    "content": content,
                    "metadata": {
                        "industry_code": data.get("industry_code", ""),
                        "industry_name": data.get("industry_name", ""),
                        "industry_sector": data.get("industry_sector", ""),
                        "industry_group": data.get("industry_group", ""),
                        "industry_sub": data.get("industry_sub", ""),
                        "source_file": data.get("_source_file", ""),
                        "type": "gics_industry",
                    },
                }
            )

        if not points:
            logger.warning("未解析到有效行业数据, 跳过 GICS 知识库 bootstrap")
            return False

        # 4. 写入 Qdrant (共享 namespace = agent_id, upsert 语义)
        #    upsert_points 内部调用 EmbeddingsClient 嵌入 + uuid5 幂等 point_id
        #    共享知识库不含 user_id (AGENTS.md 第 7 章)
        await qdrant.upsert_points(
            namespace=qdrant.build_shared_namespace(),
            points=points,
        )
        logger.info(
            "GICS 行业知识库 bootstrap 完成 (%d 个行业, namespace=%s)",
            len(points),
            qdrant.build_shared_namespace(),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(
            "GICS 知识库 bootstrap 失败 (不阻断启动, 仅告警): type=%s msg=%s",
            type(e).__name__,
            e,
        )
        return False
    finally:
        await qdrant.close()


__all__ = ["bootstrap_industry_knowledge"]
