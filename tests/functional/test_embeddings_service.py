"""功能测试: 验证 Embeddings TEI 服务 (bge-large-zh-v1.5).

AGENTS.md 第 7 章硬约束:
- Embeddings: bge-large-zh-v1.5 (固定 1024 维)
- TEI 服务通过 API_KEY 环境变量开启鉴权, 客户端须携带 Authorization: Bearer <key>
- Embedding 调用统一走 rag/embeddings.py, 但本测试直连 TEI 验证服务可用性

执行方式 (宿主机, 容器栈已 healthy):
    set EMBEDDINGS_URL=http://127.0.0.1:8088
    pytest tests/functional/test_embeddings_service.py -v -m functional
"""

from __future__ import annotations

import os

import httpx
import pytest

# TEI 服务地址 (宿主机直连 127.0.0.1:8088)
EMBEDDINGS_URL = os.getenv("EMBEDDINGS_URL", "http://127.0.0.1:8088").rstrip("/")
EMBEDDINGS_API_KEY = os.getenv("EMBEDDINGS_API_KEY", "")

# bge-large-zh-v1.5 固定维度
EXPECTED_DIM = 1024

# TEI 服务首次加载模型较慢, 给足超时
TEI_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)


def _auth_headers() -> dict[str, str]:
    """构造 TEI 鉴权请求头 (API_KEY 配置时携带 Bearer)."""
    headers: dict[str, str] = {}
    if EMBEDDINGS_API_KEY:
        headers["Authorization"] = f"Bearer {EMBEDDINGS_API_KEY}"
    return headers


@pytest.mark.functional
def test_embed_single() -> None:
    """验证单条文本嵌入: POST /embed {"inputs":["测试"]} → 200 + 1024 维."""
    r = httpx.post(
        f"{EMBEDDINGS_URL}/embed",
        json={"inputs": ["测试"]},
        headers=_auth_headers(),
        timeout=TEI_TIMEOUT,
    )
    assert r.status_code == 200, f"/embed 非 200: {r.status_code} {r.text}"
    vectors = r.json()
    assert isinstance(vectors, list), f"返回非 list: {type(vectors)}"
    assert len(vectors) == 1, f"返回向量数非 1: {len(vectors)}"
    vec = vectors[0]
    assert isinstance(vec, list), f"向量非 list: {type(vec)}"
    assert len(vec) == EXPECTED_DIM, f"维度非 {EXPECTED_DIM}: {len(vec)}"


@pytest.mark.functional
def test_embed_batch() -> None:
    """验证批量文本嵌入: POST /embed 多条 inputs → 200 + 等长向量列表."""
    texts = ["人工智能研究报告", "Python 异步编程", "中文检索增强生成"]
    r = httpx.post(
        f"{EMBEDDINGS_URL}/embed",
        json={"inputs": texts},
        headers=_auth_headers(),
        timeout=TEI_TIMEOUT,
    )
    assert r.status_code == 200, f"/embed 批量非 200: {r.status_code} {r.text}"
    vectors = r.json()
    assert isinstance(vectors, list)
    assert len(vectors) == len(texts), f"返回向量数 {len(vectors)} ≠ 输入 {len(texts)}"
    for i, vec in enumerate(vectors):
        assert isinstance(vec, list), f"第 {i} 个向量非 list"
        assert len(vec) == EXPECTED_DIM, f"第 {i} 个向量维度非 {EXPECTED_DIM}: {len(vec)}"


@pytest.mark.functional
def test_embed_empty() -> None:
    """验证空 inputs 行为: POST /embed {"inputs":[]} → 400 或 [] (TEI 实现相关).

    TEI 对空 inputs 可能返回 400 (参数校验) 或 200 + 空列表.
    """
    r = httpx.post(
        f"{EMBEDDINGS_URL}/embed",
        json={"inputs": []},
        headers=_auth_headers(),
        timeout=TEI_TIMEOUT,
    )
    # 接受 400 (校验失败) 或 200 (返回空列表) 两种行为
    assert r.status_code in (200, 400, 422), f"空 inputs 状态码异常: {r.status_code} {r.text}"
    if r.status_code == 200:
        vectors = r.json()
        assert isinstance(vectors, list)
        assert len(vectors) == 0, f"空 inputs 应返回空列表, 实际: {vectors}"
