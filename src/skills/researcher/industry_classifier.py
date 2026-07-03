"""IndustryClassifier 行业分类器.

用户需求 4: GICS 68 行业识别.
1. Qdrant 检索 GICS 知识库 (namespace=agent_id, 共享知识库) 识别行业
2. 命中失败时 LLM 兜底识别
3. 加载对应行业 prompt_family
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier
from src.observability.tracing import trace_chain
from src.rag.qdrant_manager import QdrantManager

logger = logging.getLogger(__name__)


class IndustryClassifier:
    """行业分类器.

    用户需求 4: 识别用户研究请求所属行业, 加载对应行业专家提示词族.
    """

    settings: Settings
    _llm: LLMClient
    _qdrant: QdrantManager
    _prompts_cache: dict[str, dict[str, Any]]

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        qdrant: QdrantManager | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or LLMClient(self.settings)
        self._qdrant = qdrant or QdrantManager(self.settings)
        self._prompts_cache = {}

    async def classify(
        self,
        query: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """识别行业并加载 prompt_family.

        流程 (用户需求 4):
        1. Qdrant 检索 GICS 知识库
        2. 命中失败 → LLM 兜底
        3. 加载对应行业 prompt_family
        """
        async with trace_chain(
            name="industry-classifier",
            input={"query": query[:100]},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # 1. Qdrant 检索 GICS 知识库
            industry = await self._classify_via_qdrant(
                query,
                user_id=user_id,
                session_id=session_id,
            )

            # 2. Qdrant 未命中 → LLM 兜底
            if not industry:
                industry = await self._classify_via_llm(
                    query,
                    user_id=user_id,
                    session_id=session_id,
                )

            # 3. 加载 prompt_family
            prompt_family = self._load_prompt_family(industry.get("industry_code", ""))

            result = {
                "industry_code": industry.get("industry_code", "UNKNOWN"),
                "industry_name": industry.get("industry_name", "通用研究"),
                "industry_sector": industry.get("industry_sector", ""),
                "industry_group": industry.get("industry_group", ""),
                "industry_sub": industry.get("industry_sub", ""),
                "industry_prompt_family": prompt_family,
            }

            span.update(
                output={
                    "industry_code": result["industry_code"],
                    "industry_name": result["industry_name"],
                    "via_qdrant": bool(industry.get("via_qdrant")),
                }
            )
            return result

    async def _classify_via_qdrant(
        self,
        query: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """通过 Qdrant GICS 知识库识别行业."""
        try:
            from src.rag.embeddings import EmbeddingsClient

            embeddings = EmbeddingsClient(self.settings)
            query_vec = await embeddings.embed_query(
                query,
                user_id=user_id,
                session_id=session_id,
            )
            if not query_vec:
                return None

            # 检索 GICS 知识库 (共享 namespace = agent_id)
            namespaces = [self._qdrant.build_shared_namespace()]
            results = await self._qdrant.search(
                query_vector=query_vec,
                namespaces=namespaces,
                limit=3,
                score_threshold=0.5,  # 行业识别阈值较高
            )

            if results:
                top = results[0]
                metadata = top.get("metadata", {})
                return {
                    "industry_code": metadata.get("industry_code", ""),
                    "industry_name": metadata.get("industry_name", ""),
                    "industry_sector": metadata.get("industry_sector", ""),
                    "industry_group": metadata.get("industry_group", ""),
                    "industry_sub": metadata.get("industry_sub", ""),
                    "via_qdrant": True,
                    "score": top.get("score", 0),
                }
        except Exception as e:  # noqa: BLE001
            logger.warning("Qdrant 行业识别失败: %s", e)
        return None

    async def _classify_via_llm(
        self,
        query: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """LLM 兜底识别行业 (用户需求 4)."""
        try:
            prompt = """请识别以下研究请求所属的 GICS 行业.

GICS 行业分类体系包含 11 个行业部门 (Sector):
- Energy 能源
- Materials 原材料
- Industrials 工业
- Consumer Discretionary 非必需消费品
- Consumer Staples 必需消费品
- Health Care 医疗保健
- Financials 金融
- Information Technology 信息技术
- Communication Services 通信服务
- Utilities 公用事业
- Real Estate 房地产

每个部门下有行业集团 (Industry Group) → 行业 (Industry) → 子行业 (Sub-Industry).

研究请求: {query}

请返回 JSON 格式 (仅返回 JSON, 不要其他文字):
{{
  "industry_sector": "行业部门",
  "industry_group": "行业集团",
  "industry_name": "行业名称",
  "industry_code": "行业代码 (如 451020)",
  "industry_sub": "子行业"
}}"""

            messages = [{"role": "user", "content": prompt.format(query=query[:500])}]
            response = await self._llm.achat(
                messages,
                tier=LLMTier.FAST,
                temperature=0.0,
                user_id=user_id,
                session_id=session_id,
                span_name="industry-llm",
            )

            import json_repair

            result = json_repair.loads(response.content)
            if isinstance(result, dict) and result.get("industry_name"):
                result["via_qdrant"] = False
                return result
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 行业识别失败: %s", e)

        # 最终兜底
        return {
            "industry_code": "UNKNOWN",
            "industry_name": "通用研究",
            "via_qdrant": False,
        }

    def _load_prompt_family(self, industry_code: str) -> dict[str, Any]:
        """加载行业专家提示词族.

        用户需求 4: 68 套行业专家提示词.
        路径: config/researcher/industry_prompts/{industry}.yaml
        """
        if not industry_code or industry_code == "UNKNOWN":
            return self._get_default_prompt_family()

        # 缓存
        if industry_code in self._prompts_cache:
            return self._prompts_cache[industry_code]

        # 查找对应的 YAML 文件 (阶段 4 生成 68 个文件)
        prompts_dir = self._get_prompts_dir()
        if not prompts_dir.exists():
            return self._get_default_prompt_family()

        # 尝试按 industry_code 匹配
        yaml_files = list(prompts_dir.glob("*.yaml"))
        for yaml_file in yaml_files:
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and data.get("industry_code") == industry_code:
                    loaded: dict[str, Any] = data
                    self._prompts_cache[industry_code] = loaded
                    return loaded
            except Exception:  # noqa: BLE001
                continue

        # 未匹配, 返回默认
        return self._get_default_prompt_family()

    def _get_prompts_dir(self) -> Path:
        """获取行业提示词目录."""
        return (
            Path(__file__).parent.parent.parent.parent
            / "config"
            / "researcher"
            / "industry_prompts"
        )

    @staticmethod
    def _get_default_prompt_family() -> dict[str, Any]:
        """默认通用研究提示词族."""
        return {
            "industry_code": "UNKNOWN",
            "industry_name": "通用研究",
            "planner_prompt": "",
            "researcher_prompt": "",
            "reviewer_prompt": "",
            "writer_prompt": "",
            "key_dimensions": [
                "核心概念与背景",
                "现状与趋势",
                "关键挑战",
                "未来展望",
            ],
            "data_sources_preference": [],
        }
