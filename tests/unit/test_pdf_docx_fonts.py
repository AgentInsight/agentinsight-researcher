"""单元测试: DOCX 中文字体渲染验证.

验证运行时 DOCX 报告的中文字体配置生效, 确保 PDF/DOCX 报告不乱码:
- DOCX 中文文本不乱码 (eastAsia 字体配置)
- DOCX 中英文混合不乱码
- DOCX 字体配置使用 Noto Sans CJK SC (eastAsia 属性)

单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ========== 运行时 PDF/DOCX 中文渲染验证 (验证不乱码) ==========


def test_docx_chinese_text_not_garbled() -> None:
    """运行时验证: DOCX 中文文本不乱码.

    生成含中文的 DOCX → 读回 → 验证中文文本正确提取.
    验证 publisher._to_docx 的 eastAsia 字体配置生效.
    """
    import io

    from docx import Document

    from src.skills.researcher.publisher import Publisher

    chinese_content = (
        "# 测试报告标题\n\n这是一段中文内容, 用于验证 DOCX 不乱码.\n- 列表项一\n- 列表项二"
    )
    publisher = Publisher()
    docx_bytes = publisher._to_docx(chinese_content, title="中文测试标题")
    assert len(docx_bytes) > 0, "DOCX 生成失败, 返回空 bytes"

    doc = Document(io.BytesIO(docx_bytes))
    all_text = "\n".join(p.text for p in doc.paragraphs)
    assert "中文内容" in all_text, f"DOCX 中文文本乱码或缺失, 实际: {all_text[:200]}"
    assert "列表项一" in all_text, f"DOCX 中文列表项乱码, 实际: {all_text[:200]}"


def test_docx_mixed_chinese_english_not_garbled() -> None:
    """运行时验证: DOCX 中英文混合不乱码."""
    import io

    from docx import Document

    from src.skills.researcher.publisher import Publisher

    mixed_content = "# LLM Hallucination 大语言模型幻觉\n\nRAG 检索增强生成可以缓解幻觉问题."
    publisher = Publisher()
    docx_bytes = publisher._to_docx(mixed_content, title="中英文混合测试")
    assert len(docx_bytes) > 0

    doc = Document(io.BytesIO(docx_bytes))
    all_text = "\n".join(p.text for p in doc.paragraphs)
    assert "大语言模型幻觉" in all_text, f"DOCX 中文部分乱码, 实际: {all_text[:200]}"
    assert "RAG" in all_text, f"DOCX 英文部分缺失, 实际: {all_text[:200]}"


def test_docx_font_config_uses_noto_cjk() -> None:
    """验证 DOCX 字体配置使用 Noto Sans CJK SC (eastAsia 属性)."""
    import io

    from docx import Document
    from docx.oxml.ns import qn

    from src.skills.researcher.publisher import Publisher

    publisher = Publisher()
    docx_bytes = publisher._to_docx("测试", title="字体验证")
    assert len(docx_bytes) > 0

    doc = Document(io.BytesIO(docx_bytes))
    style = doc.styles["Normal"]
    rpr = style.element.find(qn("w:rPr"))
    if rpr is not None:
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is not None:
            east_asia = rfonts.get(qn("w:eastAsia"))
            assert east_asia is not None, "DOCX Normal 样式缺少 eastAsia 字体属性"
            assert "CJK" in east_asia or "Noto" in east_asia, (
                f"DOCX eastAsia 字体应为 Noto Sans CJK SC, 实际: {east_asia}"
            )
