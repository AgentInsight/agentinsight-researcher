"""单元测试: DocumentLoader 文档加载模块.

验证 Document 数据类、LocalDocumentLoader 本地文件加载 (TXT/MD/CSV/HTML)、
工厂函数 get_document_loader 路由逻辑、AzureBlobLoader source 解析.

单元测试在构建期执行, 不依赖外部服务; 临时文件使用 pytest tmp_path 自动清理.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import Settings
from src.skills.researcher.document_loader import (
    AzureBlobLoader,
    Document,
    DocumentLoader,
    LocalDocumentLoader,
    OnlineDocumentLoader,
    get_document_loader,
)

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造无 env 文件的 Settings (避免读取 .env 配置)."""
    return Settings(_env_file=None)


# ========== Document 数据类 ==========


def test_document_creation_default_metadata() -> None:
    """测试 Document 可创建, metadata 默认为空 dict."""
    doc = Document(page_content="测试内容")
    assert doc.page_content == "测试内容"
    assert doc.metadata == {}


def test_document_creation_with_metadata() -> None:
    """测试 Document 可带 metadata 创建."""
    doc = Document(page_content="内容", metadata={"source": "test.txt"})
    assert doc.metadata["source"] == "test.txt"


# ========== DocumentLoader 基类 (抽象) ==========


def test_document_loader_is_abstract() -> None:
    """测试 DocumentLoader 是抽象基类, 不能直接实例化."""
    with pytest.raises(TypeError):
        DocumentLoader()  # type: ignore[abstract]


# ========== LocalDocumentLoader 实例化 ==========


def test_local_document_loader_can_instantiate(settings: Settings) -> None:
    """测试 LocalDocumentLoader 可实例化."""
    loader = LocalDocumentLoader(settings)
    assert loader is not None
    assert loader.settings is settings


# ========== _get_extension 静态方法 ==========


def test_get_extension_lowercases() -> None:
    """测试扩展名小写化."""
    assert LocalDocumentLoader._get_extension("file.TXT") == "txt"
    assert LocalDocumentLoader._get_extension("file.MD") == "md"
    assert LocalDocumentLoader._get_extension("file.PDF") == "pdf"


def test_get_extension_no_extension() -> None:
    """测试无扩展名文件返回空串."""
    assert LocalDocumentLoader._get_extension("noextension") == ""


def test_get_extension_multiple_dots() -> None:
    """测试多级扩展名取最后一段."""
    assert LocalDocumentLoader._get_extension("archive.tar.gz") == "gz"


# ========== load() 文本类文件 (TXT/MD/CSV/HTML) ==========


@pytest.mark.asyncio
async def test_load_txt_file(tmp_path: Path, settings: Settings) -> None:
    """测试 TXT 文件加载."""
    file_path = tmp_path / "test.txt"
    file_path.write_text("这是一段测试文本内容", encoding="utf-8")
    loader = LocalDocumentLoader(settings)
    docs = await loader.load(str(file_path))
    assert len(docs) == 1
    assert docs[0].page_content == "这是一段测试文本内容"
    assert docs[0].metadata["file_type"] == "txt"
    assert docs[0].metadata["file_name"] == "test.txt"


@pytest.mark.asyncio
async def test_load_md_file(tmp_path: Path, settings: Settings) -> None:
    """测试 MD 文件加载."""
    file_path = tmp_path / "test.md"
    file_path.write_text("# 标题\n\n正文内容", encoding="utf-8")
    loader = LocalDocumentLoader(settings)
    docs = await loader.load(str(file_path))
    assert len(docs) == 1
    assert "# 标题" in docs[0].page_content
    assert docs[0].metadata["file_type"] == "md"


@pytest.mark.asyncio
async def test_load_csv_file(tmp_path: Path, settings: Settings) -> None:
    """测试 CSV 文件加载."""
    file_path = tmp_path / "test.csv"
    file_path.write_text("name,age\n张三,25\n李四,30", encoding="utf-8")
    loader = LocalDocumentLoader(settings)
    docs = await loader.load(str(file_path))
    assert len(docs) == 1
    assert "张三" in docs[0].page_content
    assert docs[0].metadata["file_type"] == "csv"


@pytest.mark.asyncio
async def test_load_html_file(tmp_path: Path, settings: Settings) -> None:
    """测试 HTML 文件加载 (BeautifulSoup 提取文本, 降级纯文本亦可)."""
    file_path = tmp_path / "test.html"
    file_path.write_text(
        "<html><body><h1>标题</h1><p>段落内容</p></body></html>",
        encoding="utf-8",
    )
    loader = LocalDocumentLoader(settings)
    docs = await loader.load(str(file_path))
    assert len(docs) == 1
    assert "标题" in docs[0].page_content
    assert "段落内容" in docs[0].page_content
    assert docs[0].metadata["file_type"] == "html"


# ========== load() 异常与边界 ==========


@pytest.mark.asyncio
async def test_load_file_not_exists_returns_empty(settings: Settings) -> None:
    """测试文件不存在时返回空列表 (不抛异常)."""
    loader = LocalDocumentLoader(settings)
    docs = await loader.load("/nonexistent/path/file.txt")
    assert docs == []


@pytest.mark.asyncio
async def test_load_empty_file_returns_empty(
    tmp_path: Path, settings: Settings
) -> None:
    """测试空文件返回空列表 (content 为空)."""
    file_path = tmp_path / "empty.txt"
    file_path.write_text("", encoding="utf-8")
    loader = LocalDocumentLoader(settings)
    docs = await loader.load(str(file_path))
    assert docs == []


@pytest.mark.asyncio
async def test_load_url_source_returns_empty(settings: Settings) -> None:
    """测试 LocalDocumentLoader 收到 URL 时返回空 (不处理 URL)."""
    loader = LocalDocumentLoader(settings)
    docs = await loader.load("https://example.com/page")
    assert docs == []


@pytest.mark.asyncio
async def test_load_empty_source_returns_empty(settings: Settings) -> None:
    """测试空 source 返回空列表."""
    loader = LocalDocumentLoader(settings)
    docs = await loader.load("")
    assert docs == []


@pytest.mark.asyncio
async def test_load_unsupported_extension_falls_back_to_text(
    tmp_path: Path, settings: Settings
) -> None:
    """测试未知扩展名降级按文本读取."""
    file_path = tmp_path / "data.xyz"
    file_path.write_text("未知格式但可读文本", encoding="utf-8")
    loader = LocalDocumentLoader(settings)
    docs = await loader.load(str(file_path))
    assert len(docs) == 1
    assert docs[0].page_content == "未知格式但可读文本"


# ========== 工厂函数 get_document_loader ==========


def test_get_document_loader_url_returns_online(settings: Settings) -> None:
    """测试工厂函数: http:// URL → OnlineDocumentLoader."""
    loader = get_document_loader("https://example.com/page", settings)
    assert isinstance(loader, OnlineDocumentLoader)


def test_get_document_loader_azure_scheme_returns_blob(settings: Settings) -> None:
    """测试工厂函数: azure:// → AzureBlobLoader."""
    loader = get_document_loader("azure://container/blob", settings)
    assert isinstance(loader, AzureBlobLoader)


def test_get_document_loader_azure_url_returns_blob(settings: Settings) -> None:
    """测试工厂函数: Azure Blob URL → AzureBlobLoader."""
    loader = get_document_loader(
        "https://account.blob.core.windows.net/container/blob", settings
    )
    assert isinstance(loader, AzureBlobLoader)


def test_get_document_loader_local_path_returns_local(settings: Settings) -> None:
    """测试工厂函数: 本地路径 → LocalDocumentLoader."""
    loader = get_document_loader("/path/to/file.txt", settings)
    assert isinstance(loader, LocalDocumentLoader)


# ========== AzureBlobLoader._parse_source ==========


def test_azure_blob_parse_source_azure_scheme() -> None:
    """测试 AzureBlobLoader 解析 azure:// 协议."""
    container, blob = AzureBlobLoader._parse_source("azure://mycontainer/myblob.txt")
    assert container == "mycontainer"
    assert blob == "myblob.txt"


def test_azure_blob_parse_source_https_url() -> None:
    """测试 AzureBlobLoader 解析 https Azure Blob URL."""
    container, blob = AzureBlobLoader._parse_source(
        "https://account.blob.core.windows.net/mycontainer/myblob.txt"
    )
    assert container == "mycontainer"
    assert blob == "myblob.txt"


def test_azure_blob_parse_source_invalid_raises() -> None:
    """测试 AzureBlobLoader 无效 source 抛 ValueError."""
    with pytest.raises(ValueError):
        AzureBlobLoader._parse_source("ftp://invalid/source")


def test_azure_blob_parse_source_missing_blob_raises() -> None:
    """测试 AzureBlobLoader source 缺少 blob 部分抛 ValueError."""
    with pytest.raises(ValueError):
        AzureBlobLoader._parse_source("azure://containeronly")


@pytest.mark.asyncio
async def test_azure_blob_loader_without_config_returns_empty(
    settings: Settings,
) -> None:
    """测试 AzureBlobLoader 无有效配置 (未装 SDK 或无连接串) 时返回空列表."""
    loader = AzureBlobLoader(settings)
    docs = await loader.load("azure://container/blob")
    assert docs == []
