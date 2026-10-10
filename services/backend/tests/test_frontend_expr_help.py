"""前端口径一致性守门：任务表单里的表达式说明必须与后端权威白名单同源。

背景（实测反馈）：`Tasks.jsx` 的 `EXPR_HELP` 是手写文案，曾出现两处偏差——
① 「源字段按**目标列名**直接引用」与实现不符（表达式只能引用**源列名**）；
② 只列了函数、没讲**切片**，导致用户在表单里也写不出脱敏/掩码表达式（与 AI 生成侧同源问题）。
本测试把前端文案纳入同一套权威口径，避免再次漂移。前端无 JS 测试运行器，
故在 pytest 侧做跨端一致性校验。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.expr.whitelist import allowed_functions, apply_expression, validate_expression

_TASKS_JSX = (Path(__file__).resolve().parents[2]
              / "frontend" / "src" / "pages" / "Tasks.jsx")

pytestmark = pytest.mark.skipif(not _TASKS_JSX.exists(), reason="前端源码不在当前环境")

_SQL_DIALECTS = ("CONCAT", "LEFT", "RIGHT", "SUBSTRING", "COALESCE", "TRIM",
                 "LENGTH", "CASE WHEN", "CAST AS")


def _source() -> str:
    return _TASKS_JSX.read_text(encoding="utf-8")


def _help_block() -> str:
    m = re.search(r"const EXPR_HELP =(.+?);\n", _source(), re.DOTALL)
    assert m, "未找到 Tasks.jsx 中的 EXPR_HELP 常量"
    return m.group(1)


def test_frontend_function_list_matches_authority():
    """前端展示的可用函数必须与权威白名单完全一致（多/少一个都失败）。"""
    m = re.search(r"const EXPR_FUNCTIONS = \[(.*?)\];", _source(), re.DOTALL)
    assert m, "未找到 Tasks.jsx 中的 EXPR_FUNCTIONS 列表"
    declared = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert declared == set(allowed_functions())
    assert len(declared) == 16


def test_frontend_examples_are_valid_expressions():
    """前端展示的示例表达式必须都能通过白名单校验并可求值。"""
    m = re.search(r'示例：(.+?)。";', _source(), re.DOTALL)
    assert m, "未找到 EXPR_HELP 中的示例段落"
    examples = [e.strip() for e in m.group(1).split("、") if e.strip()]
    assert len(examples) >= 4
    for expr in examples:
        assert validate_expression(expr) == [], f"前端示例不是合规表达式：{expr}"

    # 切片示例需确实可用（脱敏场景）
    assert apply_expression("concat(phone[0:3], '****', phone[7:])",
                            {"phone": "13800138001"}) == "138****8001"


def test_frontend_help_states_source_columns_and_operators():
    """前端文案必须写清「引用源列名」「切片」「函数小写」，且不得再出现错误措辞。"""
    help_text = _help_block()
    assert "源列名" in help_text
    assert "目标列名" not in help_text, "表达式引用的是源列名，不得写成目标列名"
    assert "切片" in help_text
    assert "小写" in help_text
    for dialect in _SQL_DIALECTS:
        assert dialect in help_text, f"前端文案未提示该 SQL 写法不可用：{dialect}"
