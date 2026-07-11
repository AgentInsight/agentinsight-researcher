"""三级 JSON 容错解析工具.

AGENTS.md 第 3 章: common/ 公用基础模块, 不得依赖 agents/ 或业务模块.

解析链:
1. json.loads (标准解析)
2. json_repair.loads (修复常见格式错误)
3. regex 提取 (兜底, 提取 JSON 数组或对象)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def safe_json_parse(text: str, fallback: Any = None) -> Any:
    """三级 JSON 容错解析.

    Args:
        text: 待解析的字符串 (可能含 markdown 代码块、多余文本、格式错误)
        fallback: 全部失败时返回的兜底值

    Returns:
        解析得到的 Python 对象 (dict/list/str/...), 或 fallback
    """
    if not text or not isinstance(text, str):
        return fallback

    text = text.strip()

    # 第一级: 标准解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 第二级: json_repair (修复常见 LLM 输出格式错误)
    try:
        import json_repair

        result = json_repair.loads(text)
        if result is not None:
            return result
    except Exception as e:
        logger.debug("json_repair 失败: %s", e)

    # 第三级: regex 提取
    try:
        # 剥离 markdown 代码块
        cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.MULTILINE).strip()

        # 尝试标准解析剥离后的内容
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 提取 JSON 数组
        array_match = re.search(r"\[[\s\S]*\]", cleaned)
        if array_match:
            try:
                return json.loads(array_match.group())
            except json.JSONDecodeError:
                pass

        # 提取 JSON 对象
        obj_match = re.search(r"\{[\s\S]*\}", cleaned)
        if obj_match:
            try:
                return json.loads(obj_match.group())
            except json.JSONDecodeError:
                pass
    except Exception as e:
        logger.warning("JSON 三级解析 regex 兜底失败: %s", e)

    logger.warning("JSON 三级解析全部失败, 返回 fallback, 原文前 200 字: %s", text[:200])
    return fallback


def safe_json_parse_dict(text: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """安全解析为 dict, 非字典时返回 fallback.

    接入主流程: mcp_coordinator._execute_mcp 解析 env_vars (JSONB 字段) 时调用,
    替代裸 json.loads, 提升一致性与容错能力 (含 json_repair + regex 兜底).
    """
    if fallback is None:
        fallback = {}
    result = safe_json_parse(text, fallback=fallback)
    if isinstance(result, dict):
        return result
    return fallback
