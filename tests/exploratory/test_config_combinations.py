"""探索性测试: L1+L2 方案配置组合 (Trafilatura + BM25).

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
本测试验证 L1+L2 方案在不同配置组合下的行为:
- L1 抓取降级链: Trafilatura → BS+markdownify → Playwright
- L2 上下文压缩: BM25Filter (rank-bm25 + jieba)

配置字段 (src/config/settings.py):
- bm25_filter_enabled: 默认 True (L2 启用)
- scraper_mode: 默认 "auto"

测试组合:
1. BM25 启用 (默认配置)
2. Trafilatura 成功不降级
3. Trafilatura 失败 → BS+markdownify 成功
4. Trafilatura + BM25 同时启用
5. BM25 禁用 (降级到 keyword_fallback)

所有外部依赖 (EmbeddingsClient/LLMClient/scraper 类) 全部 mock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.context_manager import ContextManager
from src.skills.researcher.scrapers import scrape_with_fallback

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture()
def mock_embeddings() -> MagicMock:
    """构造 mock EmbeddingsClient (is_circuit_open=False 默认).

    embed_texts 按 input 文本数返回等长向量列表, 兼容 WrittenContentCompressor
    的 strict=True zip 约束.
    """
    emb = MagicMock()
    emb.is_circuit_open = MagicMock(return_value=False)

    async def _embed_texts(texts, **kwargs):
        return [[0.1] * 1024 for _ in texts]

    emb.embed_texts = AsyncMock(side_effect=_embed_texts)
    return emb


@pytest.fixture()
def mock_llm() -> MagicMock:
    """构造 mock LLMClient."""
    llm = MagicMock()
    llm.achat = AsyncMock(return_value=MagicMock(content="mocked summary"))
    return llm


# ========== 辅助函数 ==========


def _make_unit_settings(**overrides: object) -> Settings:
    """构造测试 Settings (跳过 .env 加载)."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _make_context_manager(
    settings: Settings,
    mock_embeddings: MagicMock,
    mock_llm: MagicMock,
) -> ContextManager:
    """构造 ContextManager 实例 (注入 mock 依赖, 替换 _written_compressor)."""
    with (
        patch(
            "src.skills.researcher.context_manager.get_embeddings_client",
            return_value=mock_embeddings,
        ),
        patch(
            "src.skills.researcher.context_manager.get_llm_client",
            return_value=mock_llm,
        ),
    ):
        cm = ContextManager(settings)
    # 替换 _written_compressor 为 mock (避免触发真实 embedding 调用)
    cm._written_compressor = MagicMock()
    cm._written_compressor.should_keep = AsyncMock(return_value=True)
    cm._written_compressor.reset = MagicMock()
    return cm


def _make_docs(total_chars: int, doc_count: int) -> list[dict]:
    """构造 doc_count 个文档, 总字符数约等于 total_chars."""
    per_doc = max(1, total_chars // max(doc_count, 1))
    docs: list[dict] = []
    for i in range(doc_count):
        prefix = f"doc-{i} "
        content = prefix + ("x" * max(1, per_doc - len(prefix)))
        docs.append({"content": content, "url": f"https://example.com/{i}"})
    return docs


def _make_scraper_class_mock(
    *,
    scrape_return: dict | None = None,
    scrape_side_effect: Exception | None = None,
) -> MagicMock:
    """构造 scraper 类 mock, 实例化后 scrape() 返回指定结果或抛异常.

    scrape_return 与 scrape_side_effect 互斥; 同时给出时 side_effect 优先.
    """

    class _MockInstance:
        def __init__(self, url: str = "", session: object | None = None, *args, **kwargs) -> None:
            self.url = url
            self.session = session

        async def scrape(self) -> dict:
            if scrape_side_effect is not None:
                raise scrape_side_effect
            return scrape_return or {}

    cls_mock = MagicMock()
    cls_mock.side_effect = _MockInstance
    return cls_mock


# ========== 1. BM25 启用 (默认配置) ==========


async def test_bm25_enabled_default(
    mock_embeddings: MagicMock,
    mock_llm: MagicMock,
) -> None:
    """配置组合: BM25 启用 (默认) → L2 触发.

    L2 BM25Filter 输出 chunks → 原样拼接返回.
    验证: _bm25_filter 被调用, 结果含原始 chunks.
    """
    settings = _make_unit_settings(bm25_filter_enabled=True)
    cm = _make_context_manager(settings, mock_embeddings, mock_llm)

    bm25_chunks = ["chunk-A " + "a" * 800, "chunk-B " + "b" * 800]

    with patch.object(cm, "_bm25_filter", new=AsyncMock(return_value=bm25_chunks)):
        result = await cm.get_similar_content(
            "test query",
            _make_docs(total_chars=20000, doc_count=20),
            max_results=5,
        )

    assert "chunk-A" in result
    assert "chunk-B" in result


# ========== 2. Trafilatura 成功不降级 ==========


async def test_trafilatura_success_no_fallback() -> None:
    """配置组合: Trafilatura 成功 → 不触发 BS/Playwright 降级.

    Trafilatura 成功返回足够内容 → 不触发 BS/Playwright 降级.
    验证: TrafilaturaScraper 被调用, BSMarkdownifyScraper 未被调用,
    PlaywrightScraper 未被调用, 最终返回 Trafilatura 结果.
    """
    tf_content = "t" * 500
    tf_mock = _make_scraper_class_mock(
        scrape_return={
            "url": "https://example.com/page",
            "content": tf_content,
            "title": "Trafilatura Title",
            "image_urls": [],
        }
    )
    bsm_mock = _make_scraper_class_mock(
        scrape_return={"url": "x", "content": "should-not-reach", "title": ""}
    )
    pw_mock = _make_scraper_class_mock(
        scrape_return={"url": "x", "content": "should-not-reach", "title": ""}
    )

    custom_settings = Settings(_env_file=None)
    custom_settings.scraper_mode = "auto"

    with (
        patch(
            "src.skills.researcher.scrapers.get_settings",
            return_value=custom_settings,
        ),
        patch(
            "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
            tf_mock,
        ),
        patch(
            "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
            bsm_mock,
        ),
        patch(
            "src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper",
            pw_mock,
        ),
    ):
        result = await scrape_with_fallback(
            "https://example.com/page",
            enable_fallback=True,
            min_content_length=100,
        )

    assert tf_mock.called, "Trafilatura 应作为首路径被实例化"
    assert not bsm_mock.called, "Trafilatura 成功不应触发 BS 降级"
    assert not pw_mock.called, "Trafilatura 成功不应触发 Playwright 降级"
    assert result.get("content") == tf_content
    assert result.get("title") == "Trafilatura Title"


# ========== 3. Trafilatura 失败 → BS+markdownify 成功 ==========


async def test_trafilatura_fallback_to_bs_markdownify() -> None:
    """配置组合: Trafilatura 失败 → BS+markdownify 成功.

    Trafilatura 返回空内容 → 降级 BS+markdownify 成功.
    验证: Trafilatura 失败后, BS+markdownify 被调用, 最终返回其结果.
    """
    tf_mock = _make_scraper_class_mock(scrape_return={"url": "x", "content": "", "title": ""})
    bsm_content = "b" * 500
    bsm_mock = _make_scraper_class_mock(
        scrape_return={
            "url": "https://example.com/page",
            "content": bsm_content,
            "title": "BS Markdownify Title",
            "image_urls": [],
        }
    )
    pw_mock = _make_scraper_class_mock(
        scrape_return={"url": "x", "content": "should-not-reach", "title": ""}
    )

    custom_settings = Settings(_env_file=None)
    custom_settings.scraper_mode = "auto"

    with (
        patch(
            "src.skills.researcher.scrapers.get_settings",
            return_value=custom_settings,
        ),
        patch(
            "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
            tf_mock,
        ),
        patch(
            "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
            bsm_mock,
        ),
        patch(
            "src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper",
            pw_mock,
        ),
    ):
        result = await scrape_with_fallback(
            "https://example.com/page",
            enable_fallback=True,
            min_content_length=100,
        )

    assert tf_mock.called, "Trafilatura 应作为首路径被调用"
    assert bsm_mock.called, "Trafilatura 失败应降级 BS+markdownify"
    assert not pw_mock.called, "BS+markdownify 成功不应触发 Playwright 降级"
    assert result.get("content") == bsm_content
    assert result.get("title") == "BS Markdownify Title"


# ========== 4. Trafilatura + BM25 同时启用 ==========


async def test_trafilatura_bm25_enabled(
    mock_embeddings: MagicMock,
    mock_llm: MagicMock,
) -> None:
    """配置组合: Trafilatura + BM25 同时启用.

    L1 Trafilatura 成功返回 (不降级), L2 BM25Filter 被调用.
    验证: 两层均按预期工作, 各层 mock 被正确触发.
    """
    tf_content = "t" * 500
    tf_mock = _make_scraper_class_mock(
        scrape_return={
            "url": "https://example.com/page",
            "content": tf_content,
            "title": "Trafilatura Title",
            "image_urls": [],
        }
    )
    bsm_mock = _make_scraper_class_mock(
        scrape_return={"url": "x", "content": "should-not-reach", "title": ""}
    )

    l1_settings = Settings(_env_file=None)
    l1_settings.scraper_mode = "auto"

    with (
        patch(
            "src.skills.researcher.scrapers.get_settings",
            return_value=l1_settings,
        ),
        patch(
            "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
            tf_mock,
        ),
        patch(
            "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
            bsm_mock,
        ),
    ):
        l1_result = await scrape_with_fallback(
            "https://example.com/page",
            enable_fallback=True,
            min_content_length=100,
        )

    assert tf_mock.called, "L1 Trafilatura 应作为首路径被实例化"
    assert not bsm_mock.called, "L1 Trafilatura 成功后不应触发 BS 降级"
    assert l1_result.get("content") == tf_content

    l2_settings = _make_unit_settings(bm25_filter_enabled=True)
    cm = _make_context_manager(l2_settings, mock_embeddings, mock_llm)

    bm25_chunks = ["chunk-A " + "a" * 800, "chunk-B " + "b" * 800]

    with patch.object(cm, "_bm25_filter", new=AsyncMock(return_value=bm25_chunks)):
        l2_result = await cm.get_similar_content(
            "test query",
            _make_docs(total_chars=20000, doc_count=20),
            max_results=5,
        )

    assert "chunk-A" in l2_result
    assert "chunk-B" in l2_result


# ========== 5. BM25 禁用 (降级到 keyword_fallback) ==========


async def test_bm25_disabled_keyword_fallback(
    mock_embeddings: MagicMock,
    mock_llm: MagicMock,
) -> None:
    """配置组合: BM25 禁用 → 降级 keyword_fallback.

    BM25 禁用 → _keyword_fallback 被调用.
    验证: BM25Filter 不被调用, _keyword_fallback 被调用.
    """
    settings = _make_unit_settings(bm25_filter_enabled=False)
    cm = _make_context_manager(settings, mock_embeddings, mock_llm)

    with (
        patch.object(cm, "_bm25_filter", new=AsyncMock()) as mock_bm25,
        patch.object(
            ContextManager,
            "_keyword_fallback",
            wraps=ContextManager._keyword_fallback,
        ) as spy_kw,
    ):
        result = await cm.get_similar_content(
            "test query 匹配关键词",
            _make_docs(total_chars=20000, doc_count=20),
            max_results=5,
        )

    mock_bm25.assert_not_called()
    assert spy_kw.called, "BM25 禁用应走 _keyword_fallback"
    assert len(result) > 0
