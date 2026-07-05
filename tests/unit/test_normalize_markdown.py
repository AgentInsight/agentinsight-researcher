"""验证 _normalize_markdown 逻辑(本地单元测试,无需容器)"""

import sys

from src.skills.researcher.report_generator import ReportGenerator

# 测试用例: (输入, 描述)
TEST_CASES = [
    # 1. 表格内部不应被插入空行
    (
        "## 表格测试\n\n| 名称 | 值 |\n| --- | --- |\n| A | 1 |\n| B | 2 |\n\n正文",
        "表格内部不应有空行",
    ),
    # 2. 表格前后应有空行
    (
        "正文\n| 名称 | 值 |\n| --- | --- |\n| A | 1 |\n正文",
        "表格前后应补空行",
    ),
    # 3. 段落紧贴标题
    (
        "## 标题\n正文内容",
        "标题后应补空行",
    ),
    # 4. 引用紧贴
    (
        "研究表明[1][2]显示",
        "引用紧贴应修复为 [1] [2]",
    ),
    # 5. 连续空行压缩
    (
        "段落1\n\n\n\n\n段落2",
        "连续4空行应压缩为2",
    ),
    # 6. 列表项之间不应有空行(避免松散列表)
    (
        "## 列表\n- 项目1\n- 项目2\n- 项目3\n\n正文",
        "列表内部不应有空行",
    ),
    # 7. 列表前后应有空行
    (
        "正文\n- 项目1\n- 项目2\n正文",
        "列表前后应补空行",
    ),
    # 8. 末尾空行清理
    (
        "内容\n\n\n\n",
        "末尾空行应清理",
    ),
    # 9. 综合用例(模拟 LLM 输出)
    (
        "## 摘要\n这是摘要内容\n## 关键维度\n### 物理原理\n量子比特[1][2]是基本单元\n| 技术 | 优势 |\n| --- | --- |\n| 超导 | 高速 |\n| 离子阱 | 长相干 |\n## 结论\n量子计算前景广阔",
        "综合用例: 标题/段落/引用/表格混合",
    ),
]


def run_tests():
    print("=" * 80)
    print("_normalize_markdown 单元测试")
    print("=" * 80)
    passed = 0
    failed = 0
    for i, (input_md, desc) in enumerate(TEST_CASES, 1):
        result = ReportGenerator._normalize_markdown(input_md)
        # 简单断言: 不崩溃即视为基本通过
        print(f"\n--- 测试 {i}: {desc} ---")
        print(f"输入:  {repr(input_md[:80])}")
        print(f"输出:  {repr(result[:120])}")

        # 针对性断言
        ok = True
        err = ""
        if i == 1:
            # 表格内部不应有 "\n\n|" (空行后跟 |)
            if "|\n\n|" in result:
                ok = False
                err = "表格内部被插入空行"
        elif i == 2:
            # 表格前后应有空行
            if "正文\n|" in result or "|\n正文" in result:
                ok = False
                err = "表格前后未补空行"
        elif i == 3:
            if "## 标题\n正文" in result:
                ok = False
                err = "标题后未补空行"
        elif i == 4:
            if "[1][2]" in result:
                ok = False
                err = "引用紧贴未修复"
        elif i == 5:
            if "\n\n\n\n" in result:
                ok = False
                err = "连续空行未压缩"
        elif i == 6:
            # 列表项之间不应有空行
            if "- 项目1\n\n- 项目2" in result:
                ok = False
                err = "列表项之间被插入空行"
        elif i == 7:
            # 列表前后应有空行
            if "正文\n- 项目1" in result or "- 项目2\n正文" in result:
                ok = False
                err = "列表前后未补空行"
        elif i == 8:
            if result != "内容\n":
                ok = False
                err = f"末尾空行未清理, 结果={repr(result)}"
        elif i == 9:
            # 综合检查
            if "|\n\n|" in result:
                ok = False
                err = "表格内部被插入空行"
            if "[1][2]" in result:
                ok = False
                err = "引用紧贴未修复"
            if "## 摘要\n这是" in result:
                ok = False
                err = "标题后未补空行"

        if ok:
            print("结果: ✓ 通过")
            passed += 1
        else:
            print(f"结果: ✗ 失败 - {err}")
            failed += 1

    print(f"\n{'=' * 80}")
    print(f"汇总: {passed} 通过, {failed} 失败, 共 {len(TEST_CASES)} 个测试")
    print(f"{'=' * 80}")
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
