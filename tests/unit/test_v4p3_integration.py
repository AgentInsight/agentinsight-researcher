"""单元测试: V4-P3 两层路由集成测试.

验证 src/skills/researcher/context_manager.py 的 get_similar_content 两层路由:
- Layer 1 Fast Path: total_chars < bm25_filter_char_threshold (8000) 且
  len(documents) <= max_results → 直接拼接
- Layer 2 BM25Filter: bm25_filter_enabled=True 且 total_chars >= 8000
  → 走 _bm25_filter

同时验证 L1 抓取降级链 (Trafilatura → BS+markdownify → Playwright) 与
L2 后处理压缩链的串联行为, 以及降级策略:
- BM25Filter 超时 → 关键词匹配降级
- EmbeddingsFilter 熔断 → 关键词匹配降级

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
所有外部依赖 (EmbeddingsClient/LLMClient) 全部 mock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.context_manager import ContextManager

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture()
def test_settings() -> Settings:
    """构造测试 Settings (跳过 .env 加载, 使用默认值)."""
    return Settings(_env_file=None)


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


@pytest.fixture()
def context_manager(
    test_settings: Settings,
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
        cm = ContextManager(test_settings)
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


# ========== TestV4P3LayerRouting: 两层路由边界测试 ==========


class TestV4P3LayerRouting:
    """验证 get_similar_content 两层路由边界条件."""

    async def test_layer1_fast_path_small_docs(
        self,
        context_manager: ContextManager,
    ) -> None:
        """Layer 1 Fast Path: 总字符 < 8000 且 文档数 <= max_results → fast_path=True.

        应直接拼接原文, 不调用 _bm25_filter / _embeddings_rerank.
        """
        docs = _make_docs(total_chars=4000, doc_count=3)
        with (
            patch.object(context_manager, "_bm25_filter", new=AsyncMock()) as mock_bm25,
            patch.object(context_manager, "_embeddings_rerank", new=AsyncMock()) as mock_emb,
        ):
            result = await context_manager.get_similar_content("test query", docs, max_results=10)
        mock_bm25.assert_not_called()
        mock_emb.assert_not_called()
        assert "doc-0" in result
        assert "doc-1" in result
        assert "doc-2" in result

    async def test_layer2_bm25_medium_docs(
        self,
        context_manager: ContextManager,
    ) -> None:
        """Layer 2 BM25Filter: 总字符 >= 8000 → 走 _bm25_filter.

        BM25 返回 2 chunks (<=30) → 跳过 _embeddings_rerank 精排.
        """
        docs = _make_docs(total_chars=20000, doc_count=20)
        bm25_return = ["bm25-chunk-1", "bm25-chunk-2"]
        with (
            patch.object(
                context_manager,
                "_bm25_filter",
                new=AsyncMock(return_value=bm25_return),
            ) as mock_bm25,
            patch.object(context_manager, "_embeddings_rerank", new=AsyncMock()) as mock_emb,
        ):
            result = await context_manager.get_similar_content("test query", docs, max_results=5)
        mock_bm25.assert_called_once()
        mock_emb.assert_not_called()
        assert "bm25-chunk-1" in result or "bm25-chunk-2" in result

    async def test_layer2_bm25_large_docs(
        self,
        context_manager: ContextManager,
    ) -> None:
        """V4-P3 两层路由: 大文档 (>=8K) 走 BM25Filter.

        现两层路由: >=8K 统一走 BM25Filter (含 >50K 超长上下文).
        BM25 返回 2 chunks (<=30) → 跳过 _embeddings_rerank 精排.
        """
        docs = _make_docs(total_chars=60000, doc_count=100)
        bm25_return = ["bm25-chunk-1", "bm25-chunk-2"]
        with (
            patch.object(
                context_manager,
                "_bm25_filter",
                new=AsyncMock(return_value=bm25_return),
            ) as mock_bm25,
            patch.object(
                context_manager,
                "_embeddings_rerank",
                new=AsyncMock(),
            ) as mock_emb,
        ):
            result = await context_manager.get_similar_content("test query", docs, max_results=5)
        mock_bm25.assert_called_once()
        mock_emb.assert_not_called()
        assert "bm25-chunk-1" in result or "bm25-chunk-2" in result

    async def test_empty_documents_returns_empty_string(
        self,
        context_manager: ContextManager,
    ) -> None:
        """空文档列表 documents=[] → 早期返回空字符串."""
        with (
            patch.object(context_manager, "_bm25_filter", new=AsyncMock()) as mock_bm25,
            patch.object(context_manager, "_embeddings_rerank", new=AsyncMock()) as mock_emb,
        ):
            result = await context_manager.get_similar_content("test query", [], max_results=10)
        assert result == ""
        mock_bm25.assert_not_called()
        mock_emb.assert_not_called()

    async def test_bm25_filter_disabled_falls_back_to_keyword(
        self,
        test_settings: Settings,
        mock_embeddings: MagicMock,
        mock_llm: MagicMock,
    ) -> None:
        """bm25_filter_enabled=False 时降级关键词匹配."""
        test_settings.bm25_filter_enabled = False

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
            cm = ContextManager(test_settings)
        cm._written_compressor = MagicMock()
        cm._written_compressor.should_keep = AsyncMock(return_value=True)
        cm._written_compressor.reset = MagicMock()

        docs = _make_docs(total_chars=20000, doc_count=20)
        with (
            patch.object(cm, "_bm25_filter", new=AsyncMock()) as mock_bm25,
            patch.object(
                ContextManager,
                "_keyword_fallback",
                wraps=ContextManager._keyword_fallback,
            ) as spy_kw,
        ):
            result = await cm.get_similar_content("test query 匹配关键词", docs, max_results=5)

        mock_bm25.assert_not_called()
        assert spy_kw.called, "bm25_filter_enabled=False 应走 _keyword_fallback"
        assert len(result) > 0


# ========== TestV4P3L1FallbackChain: L1 抓取降级链测试 ==========


class TestV4P3L1FallbackChain:
    """验证 scrape_with_fallback 的 Trafilatura → BS+markdownify → Playwright 降级链.

    全部 mock, 不实际网络请求.
    """

    @staticmethod
    def _make_scraper_class_mock(
        *,
        scrape_return: dict | None = None,
        scrape_side_effect: Exception | None = None,
    ) -> MagicMock:
        """构造 scraper 类 mock, 实例化后 scrape() 返回指定结果或抛异常."""

        class _MockInstance:
            def __init__(
                self, url: str = "", session: object | None = None, *args, **kwargs
            ) -> None:
                self.url = url
                self.session = session

            async def scrape(self) -> dict:
                if scrape_side_effect is not None:
                    raise scrape_side_effect
                return scrape_return or {}

        cls_mock = MagicMock()
        cls_mock.side_effect = _MockInstance
        return cls_mock

    async def test_fallback_chain_tf_to_bs_to_playwright(self) -> None:
        """Trafilatura 失败 → BS+markdownify 失败 → Playwright 兜底成功."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_mock = self._make_scraper_class_mock(
            scrape_side_effect=RuntimeError("trafilatura failed")
        )
        bsm_mock = self._make_scraper_class_mock(
            scrape_side_effect=RuntimeError("bs_markdownify failed")
        )
        pw_content = "x" * 500
        pw_mock = self._make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": pw_content,
                "title": "Playwright Title",
                "image_urls": [],
            }
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
        assert bsm_mock.called, "Trafilatura 失败后应降级到 BS+markdownify"
        assert pw_mock.called, "BS+markdownify 失败后应降级到 Playwright"
        assert result.get("content") == pw_content
        assert result.get("title") == "Playwright Title"

    async def test_fallback_chain_stops_at_first_success(self) -> None:
        """Trafilatura 成功返回足够内容 → 不触发 BS/Playwright 降级."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_content = "y" * 500
        tf_mock = self._make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": tf_content,
                "title": "Trafilatura Title",
                "image_urls": [],
            }
        )
        bsm_mock = self._make_scraper_class_mock(
            scrape_return={"url": "x", "content": "should-not-reach", "title": ""},
        )
        pw_mock = self._make_scraper_class_mock(
            scrape_return={"url": "x", "content": "should-not-reach", "title": ""},
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
        assert not bsm_mock.called, "Trafilatura 成功后不应触发 BS 降级"
        assert not pw_mock.called, "Trafilatura 成功后不应触发 Playwright 降级"
        assert result.get("content") == tf_content

    async def test_fallback_chain_lightweight_skips_playwright(self) -> None:
        """lightweight 模式: Trafilatura 失败 → 直接返回 (跳过 Playwright)."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_mock = self._make_scraper_class_mock(scrape_side_effect=RuntimeError("tf failed"))
        bsm_mock = self._make_scraper_class_mock(scrape_side_effect=RuntimeError("bsm failed"))
        pw_mock = self._make_scraper_class_mock(
            scrape_return={"url": "x", "content": "should-not-reach", "title": ""},
        )

        custom_settings = Settings(_env_file=None)
        custom_settings.scraper_mode = "lightweight"

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
        assert not bsm_mock.called, "lightweight 模式应在 Trafilatura 失败后跳过 BS 降级"
        assert not pw_mock.called, "lightweight 模式应跳过 Playwright 降级"
        assert not result.get("content")


# ========== TestV4P3DegradeStrategy: 降级策略测试 ==========


class TestV4P3DegradeStrategy:
    """验证 V4-P3 各层级的降级策略."""

    async def test_bm25_filter_timeout_degrades_to_keyword_match(
        self,
        context_manager: ContextManager,
    ) -> None:
        """BM25Filter 超时 → 降级到 _keyword_fallback_split."""
        docs = _make_docs(total_chars=20000, doc_count=20)

        with patch("src.rag.bm25_filter.BM25Filter") as mock_bm25_cls:
            mock_instance = MagicMock()
            mock_instance.filter = AsyncMock(side_effect=TimeoutError("bm25 timeout"))
            mock_bm25_cls.return_value = mock_instance

            with patch.object(
                ContextManager,
                "_keyword_fallback_split",
                wraps=ContextManager._keyword_fallback_split,
            ) as spy_kw:
                result = await context_manager.get_similar_content(
                    "test query 匹配关键词", docs, max_results=5
                )

        assert spy_kw.called, "BM25Filter 超时应降级到 _keyword_fallback_split"
        assert len(result) > 0
        mock_instance.filter.assert_awaited_once()

    async def test_embeddings_circuit_open_degrades_to_keyword_match(
        self,
        test_settings: Settings,
        mock_llm: MagicMock,
    ) -> None:
        """TEI 熔断器开启 → get_similar_content 直接走 _keyword_fallback."""
        mock_emb = MagicMock()
        mock_emb.is_circuit_open = MagicMock(return_value=True)

        with (
            patch(
                "src.skills.researcher.context_manager.get_embeddings_client",
                return_value=mock_emb,
            ),
            patch(
                "src.skills.researcher.context_manager.get_llm_client",
                return_value=mock_llm,
            ),
        ):
            cm = ContextManager(test_settings)
        cm._written_compressor = MagicMock()
        cm._written_compressor.should_keep = AsyncMock(return_value=True)
        cm._written_compressor.reset = MagicMock()

        docs = _make_docs(total_chars=60000, doc_count=100)

        with (
            patch.object(cm, "_bm25_filter", new=AsyncMock()) as mock_bm25,
            patch.object(
                ContextManager,
                "_keyword_fallback",
                wraps=ContextManager._keyword_fallback,
            ) as spy_kw,
        ):
            result = await cm.get_similar_content("test query 匹配关键词", docs, max_results=5)

        assert spy_kw.called, "TEI 熔断应直接走 _keyword_fallback"
        mock_bm25.assert_not_called()
        assert len(result) > 0

    async def test_bm25_filter_disabled_degrades_to_keyword_fallback(
        self,
        context_manager: ContextManager,
    ) -> None:
        """bm25_filter_enabled=False 时降级到 _keyword_fallback (不调远程 TEI).

        V4-P3 两层路由: 旧 `_embeddings_filter` 方法已删除, bm25_filter_enabled=False
        时直接走关键词匹配降级路径 (不依赖远程 TEI embed_texts).
        """
        docs = _make_docs(total_chars=60000, doc_count=100)
        context_manager.settings.bm25_filter_enabled = False

        with patch.object(
            context_manager,
            "_keyword_fallback",
            wraps=context_manager._keyword_fallback,
        ) as spy_kw:
            result = await context_manager.get_similar_content(
                "test query 匹配关键词", docs, max_results=5
            )

        assert spy_kw.called, "bm25_filter_enabled=False 应直接走 _keyword_fallback"
        assert len(result) > 0

    async def test_post_filter_compress_written_content_dedup(
        self,
        test_settings: Settings,
        mock_embeddings: MagicMock,
        mock_llm: MagicMock,
    ) -> None:
        """WrittenContentCompressor should_keep=False 过滤分支."""
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
            cm = ContextManager(test_settings)
        cm._written_compressor = MagicMock()
        cm._written_compressor.should_keep = AsyncMock(side_effect=[True, False, True])
        cm._written_compressor.reset = MagicMock()

        bm25_chunks = ["keep-chunk-1", "drop-chunk-2", "keep-chunk-3"]
        with patch.object(cm, "_bm25_filter", new=AsyncMock(return_value=bm25_chunks)):
            result = await cm.get_similar_content(
                "test query",
                _make_docs(total_chars=20000, doc_count=20),
                max_results=5,
            )

        assert "keep-chunk-1" in result
        assert "keep-chunk-3" in result
        assert "drop-chunk-2" not in result

    def test_truncate_by_words_word_limit(self) -> None:
        """_truncate_by_words Word Limit 截断 (MAX_CONTEXT_WORDS=25000)."""
        text1 = " ".join(f"word{i}" for i in range(15000))
        text2 = " ".join(f"item{i}" for i in range(15000))
        texts = [text1, text2]

        result = ContextManager._truncate_by_words(texts, max_words=25000)

        word_count = len(result.split())
        assert word_count <= 25000
        assert "word0" in result
        assert "word14999" in result
        assert "item0" in result
        assert "item9999" in result
        assert "item10000" not in result
