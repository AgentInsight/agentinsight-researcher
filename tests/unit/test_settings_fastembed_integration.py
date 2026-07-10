"""单元测试: settings + fastembed_client 配置集成测试.

验证 src/config/settings.py 的 ONNX 线程配置传递到 src/rag/fastembed_client.py 的 _ensure_model:
- fastembed_onnx_intra_threads 配置传递到 ONNX Runtime (TextEmbedding threads 参数)
- fastembed_onnx_inter_threads 配置传递到 OMP_NUM_THREADS 环境变量
- 配置值 0 (自动) 时的回退逻辑: intra → cpu_count, inter → cpu_count // 2

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
所有外部依赖 (FastEmbed TextEmbedding / anyio.Path / os.cpu_count) 全部 mock.
AGENTS.md 第 1 章: 配置经 config/ + 环境变量, 业务代码不硬编码.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.rag.fastembed_client import FastEmbedClient

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture(autouse=True)
def _clear_fastembed_singleton() -> None:
    """每个用例前后清空 FastEmbed 全局单例 + 进程内缓存."""
    from src.rag import fastembed_client as fe_mod

    fe_mod._FASTEMBED_CACHE.clear()
    fe_mod._client = None
    yield
    fe_mod._FASTEMBED_CACHE.clear()
    fe_mod._client = None


def _make_settings(
    *,
    intra_threads: int = 0,
    inter_threads: int = 0,
) -> Settings:
    """构造带 ONNX 线程配置的 Settings."""
    return Settings(
        _env_file=None,
        fastembed_onnx_intra_threads=intra_threads,
        fastembed_onnx_inter_threads=inter_threads,
    )


def _patch_model_loading() -> tuple[MagicMock, MagicMock]:
    """返回 (mock_te_cls, mock_path) 用于 patch fastembed.TextEmbedding + anyio.Path.

    mock_te_cls 捕获 TextEmbedding 构造参数 (含 threads); mock_path.exists 返回 False
    (本地模型路径不存在, 走自动下载分支, 不影响 threads 参数传递).
    """
    mock_model = MagicMock()
    mock_model.embed = MagicMock(side_effect=lambda batch: [[0.1] * 512 for _ in batch])
    mock_te_cls = MagicMock(return_value=mock_model)

    mock_path = MagicMock()
    mock_path.exists = AsyncMock(return_value=False)
    return mock_te_cls, mock_path


async def _trigger_ensure_model(client: FastEmbedClient) -> None:
    """触发 _ensure_model 加载 (通过 embed_texts 单条文本)."""
    await client.embed_texts(["触发加载"])


# ========== TestIntraThreadsConfig: intra_op_num_threads 配置传递 ==========


class TestIntraThreadsConfig:
    """验证 fastembed_onnx_intra_threads 传递到 ONNX Runtime (TextEmbedding threads 参数)."""

    async def test_explicit_intra_threads_passed_to_onnx(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """intra_threads=4 → TextEmbedding 构造参数 threads=4."""
        monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
        settings = _make_settings(intra_threads=4, inter_threads=2)
        mock_te_cls, mock_path = _patch_model_loading()
        client = FastEmbedClient(settings)

        with (
            patch("fastembed.TextEmbedding", mock_te_cls),
            patch("anyio.Path", return_value=mock_path),
        ):
            await _trigger_ensure_model(client)

        te_kwargs = mock_te_cls.call_args.kwargs
        assert te_kwargs["threads"] == 4, (
            f"intra_threads=4 应传递到 TextEmbedding threads, 实际: {te_kwargs.get('threads')}"
        )

    async def test_zero_intra_threads_falls_back_to_cpu_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """intra_threads=0 → 回退到 os.cpu_count()."""
        monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
        settings = _make_settings(intra_threads=0, inter_threads=2)
        mock_te_cls, mock_path = _patch_model_loading()
        client = FastEmbedClient(settings)

        with (
            patch("fastembed.TextEmbedding", mock_te_cls),
            patch("anyio.Path", return_value=mock_path),
            patch("os.cpu_count", return_value=8),
        ):
            await _trigger_ensure_model(client)

        te_kwargs = mock_te_cls.call_args.kwargs
        assert te_kwargs["threads"] == 8, (
            f"intra_threads=0 + cpu_count=8 → threads 应为 8, 实际: {te_kwargs.get('threads')}"
        )

    async def test_zero_intra_threads_with_none_cpu_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """intra_threads=0 且 cpu_count=None → 回退到默认 4."""
        monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
        settings = _make_settings(intra_threads=0, inter_threads=2)
        mock_te_cls, mock_path = _patch_model_loading()
        client = FastEmbedClient(settings)

        with (
            patch("fastembed.TextEmbedding", mock_te_cls),
            patch("anyio.Path", return_value=mock_path),
            patch("os.cpu_count", return_value=None),
        ):
            await _trigger_ensure_model(client)

        te_kwargs = mock_te_cls.call_args.kwargs
        assert te_kwargs["threads"] == 4, (
            f"cpu_count=None → threads 应回退到 4, 实际: {te_kwargs.get('threads')}"
        )


# ========== TestInterThreadsConfig: inter_op_num_threads 配置传递 ==========


class TestInterThreadsConfig:
    """验证 fastembed_onnx_inter_threads 传递到 OMP_NUM_THREADS 环境变量."""

    async def test_explicit_inter_threads_sets_omp_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """inter_threads=2 → OMP_NUM_THREADS='2'."""
        monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
        settings = _make_settings(intra_threads=4, inter_threads=2)
        mock_te_cls, mock_path = _patch_model_loading()
        client = FastEmbedClient(settings)

        with (
            patch("fastembed.TextEmbedding", mock_te_cls),
            patch("anyio.Path", return_value=mock_path),
        ):
            await _trigger_ensure_model(client)

        assert os.environ.get("OMP_NUM_THREADS") == "2", (
            f"inter_threads=2 → OMP_NUM_THREADS 应为 '2', 实际: {os.environ.get('OMP_NUM_THREADS')}"
        )

    async def test_zero_inter_threads_falls_back_to_cpu_half(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """inter_threads=0 → 回退到 cpu_count // 2."""
        monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
        settings = _make_settings(intra_threads=4, inter_threads=0)
        mock_te_cls, mock_path = _patch_model_loading()
        client = FastEmbedClient(settings)

        with (
            patch("fastembed.TextEmbedding", mock_te_cls),
            patch("anyio.Path", return_value=mock_path),
            patch("os.cpu_count", return_value=8),
        ):
            await _trigger_ensure_model(client)

        assert os.environ.get("OMP_NUM_THREADS") == "4", (
            f"inter_threads=0 + cpu_count=8 → OMP_NUM_THREADS 应为 '4' (8//2), "
            f"实际: {os.environ.get('OMP_NUM_THREADS')}"
        )

    async def test_zero_inter_threads_odd_cpu_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """inter_threads=0 + cpu_count=7 → OMP_NUM_THREADS='3' (7//2=3)."""
        monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
        settings = _make_settings(intra_threads=4, inter_threads=0)
        mock_te_cls, mock_path = _patch_model_loading()
        client = FastEmbedClient(settings)

        with (
            patch("fastembed.TextEmbedding", mock_te_cls),
            patch("anyio.Path", return_value=mock_path),
            patch("os.cpu_count", return_value=7),
        ):
            await _trigger_ensure_model(client)

        assert os.environ.get("OMP_NUM_THREADS") == "3", (
            f"cpu_count=7 → OMP_NUM_THREADS 应为 '3' (7//2), "
            f"实际: {os.environ.get('OMP_NUM_THREADS')}"
        )

    async def test_zero_inter_threads_minimum_one(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """inter_threads=0 + cpu_count=1 → OMP_NUM_THREADS='1' (max(1, 1//2)=1)."""
        monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
        settings = _make_settings(intra_threads=4, inter_threads=0)
        mock_te_cls, mock_path = _patch_model_loading()
        client = FastEmbedClient(settings)

        with (
            patch("fastembed.TextEmbedding", mock_te_cls),
            patch("anyio.Path", return_value=mock_path),
            patch("os.cpu_count", return_value=1),
        ):
            await _trigger_ensure_model(client)

        assert os.environ.get("OMP_NUM_THREADS") == "1", (
            f"cpu_count=1 → OMP_NUM_THREADS 应为 '1' (max(1, 0)), "
            f"实际: {os.environ.get('OMP_NUM_THREADS')}"
        )

    async def test_omp_env_not_overridden_if_already_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OMP_NUM_THREADS 已设置时, setdefault 不覆盖 (保留原值)."""
        monkeypatch.setenv("OMP_NUM_THREADS", "16")
        settings = _make_settings(intra_threads=4, inter_threads=2)
        mock_te_cls, mock_path = _patch_model_loading()
        client = FastEmbedClient(settings)

        with (
            patch("fastembed.TextEmbedding", mock_te_cls),
            patch("anyio.Path", return_value=mock_path),
        ):
            await _trigger_ensure_model(client)

        # setdefault 不覆盖已有值
        assert os.environ.get("OMP_NUM_THREADS") == "16", (
            "OMP_NUM_THREADS 已存在时 setdefault 不应覆盖"
        )


# ========== TestConfigIntegration: 配置端到端传递验证 ==========


class TestConfigIntegration:
    """验证 settings → FastEmbedClient → ONNX Runtime 的端到端配置传递."""

    async def test_both_threads_explicit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """intra=6, inter=3 → threads=6, OMP_NUM_THREADS='3'."""
        monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
        settings = _make_settings(intra_threads=6, inter_threads=3)
        mock_te_cls, mock_path = _patch_model_loading()
        client = FastEmbedClient(settings)

        with (
            patch("fastembed.TextEmbedding", mock_te_cls),
            patch("anyio.Path", return_value=mock_path),
        ):
            await _trigger_ensure_model(client)

        te_kwargs = mock_te_cls.call_args.kwargs
        assert te_kwargs["threads"] == 6
        assert os.environ.get("OMP_NUM_THREADS") == "3"

    async def test_both_threads_auto(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """intra=0, inter=0, cpu_count=12 → threads=12, OMP_NUM_THREADS='6'."""
        monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
        settings = _make_settings(intra_threads=0, inter_threads=0)
        mock_te_cls, mock_path = _patch_model_loading()
        client = FastEmbedClient(settings)

        with (
            patch("fastembed.TextEmbedding", mock_te_cls),
            patch("anyio.Path", return_value=mock_path),
            patch("os.cpu_count", return_value=12),
        ):
            await _trigger_ensure_model(client)

        te_kwargs = mock_te_cls.call_args.kwargs
        assert te_kwargs["threads"] == 12, (
            f"intra=0 + cpu_count=12 → threads=12, 实际: {te_kwargs.get('threads')}"
        )
        assert os.environ.get("OMP_NUM_THREADS") == "6", (
            f"inter=0 + cpu_count=12 → OMP='6' (12//2), 实际: {os.environ.get('OMP_NUM_THREADS')}"
        )

    async def test_model_name_and_max_length_passed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TextEmbedding 同时接收 model_name + max_length 配置."""
        monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
        settings = _make_settings(intra_threads=4, inter_threads=2)
        mock_te_cls, mock_path = _patch_model_loading()
        client = FastEmbedClient(settings)

        with (
            patch("fastembed.TextEmbedding", mock_te_cls),
            patch("anyio.Path", return_value=mock_path),
        ):
            await _trigger_ensure_model(client)

        te_kwargs = mock_te_cls.call_args.kwargs
        assert te_kwargs["model_name"] == settings.fastembed_model_name
        assert te_kwargs["max_length"] == settings.fastembed_max_length
        assert te_kwargs["threads"] == 4
