"""受限表达式 AST 白名单校验与安全求值（任务 4.2）。

约束：仅允许白名单函数调用，禁止属性访问（Attribute）、内建调用（如 __import__、eval、
open 等未白名单内建）、以及任意 unnamed 表达式之外的副作用。
仅用于"规则引擎"的受限表达式字段（FieldRule.kind=expression），不作用于手写 Python 路径
（手写路径由手写安全模型即任务 6 控制）。

单一事实源（决策 6）：白名单命名空间只在此处定义一次——
- 本模块的 `EXPR_NAMESPACE_SOURCE` 是该命名空间的唯一权威源码；
- 运行时求值（`apply_expression`）与生成脚本内联的 `_EXPR_NS`
  （app/scripting/gen_config.py）均从它派生。
因此「校验器可用函数」与「生成脚本运行时可用函数」不存在第二份清单；
LLM 侧的知识资产（app/llm/skills/expression/SKILL.md）由测试守门与本清单保持一致。
"""

from __future__ import annotations

import ast

# ---- 白名单命名空间（唯一权威定义） ----

#: 受限表达式可用的命名空间源码。既用于本模块求值，也被生成脚本内联，
#: 因此**不得**在别处复制这份清单（详见模块 docstring）。
EXPR_NAMESPACE_SOURCE = """\
_EXPR_NS = {
    "str": str, "int": int, "float": float, "bool": bool,
    "len": len, "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "lower": lambda s: s.lower() if isinstance(s, str) else s,
    "upper": lambda s: s.upper() if isinstance(s, str) else s,
    "strip": lambda s: s.strip() if isinstance(s, str) else s,
    "replace": lambda s, a, b: s.replace(a, b) if isinstance(s, str) else s,
    "concat": lambda *parts: "".join(str(p) for p in parts),
    "cast": lambda v, t=str: t(v),
}"""


def _load_namespace() -> dict[str, object]:
    """执行权威源码得到命名空间（保证脚本内联与校验器取值完全同源）。"""
    scope: dict[str, object] = {}
    exec(EXPR_NAMESPACE_SOURCE, scope)  # noqa: S102 - 源码为模块内常量，非外部输入
    return dict(scope["_EXPR_NS"])  # type: ignore[arg-type]


_EXPR_NS: dict[str, object] = _load_namespace()

_ALLOWED_FUNCTIONS: frozenset[str] = frozenset(_EXPR_NS)

#: 禁止项说明（供规则说明、LLM 修复提示与技能包一致性测试复用）
_FORBIDDEN_NOTES: tuple[str, ...] = (
    "禁止属性访问（如 value.strip()、value.lower()），请改用白名单函数 lower/upper/strip；",
    "禁止赋值、海象运算符等任何存储操作；",
    "禁止调用白名单之外的函数（含内建函数与自造函数），也不存在 import；",
    "禁止访问对象属性与下标之外的任何运行时对象（没有 row/now 等上下文变量）；",
    "只能引用当前行的列名（须为合法标识符）与上表函数，字母大小写敏感。",
)


def allowed_functions() -> frozenset[str]:
    """权威白名单函数集合（表达式校验器与生成脚本运行时的唯一来源）。"""
    return _ALLOWED_FUNCTIONS


def forbidden_notes() -> list[str]:
    """禁止项说明（中文可读文案），供提示词/技能包/修复提示复用。"""
    return list(_FORBIDDEN_NOTES)


class ExpressionViolation(Exception):
    pass


def validate_expression(expr: str) -> list[str]:
    """返回违规列表；空列表表示可通过且可安全求值。"""
    violations: list[str] = []
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        return [f"表达式语法错误: {e.msg}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            violations.append("表达式禁止属性访问(Attribute)")
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            violations.append("表达式禁止赋值/存储")
        elif isinstance(node, ast.Call):
            func = node.func
            if not isinstance(func, ast.Name):
                violations.append("仅允许白名单函数调用")
            else:
                if func.id not in _ALLOWED_FUNCTIONS:
                    violations.append(f"函数不在白名单: {func.id}")
    # 顶层必须是表达式（mode=eval 保证了），且非裸函数套娃访问
    return violations


def apply_expression(expr: str, row: dict) -> object:
    """在白名单函数 + 行按名引用的受限命名空间内安全求值。"""
    violations = validate_expression(expr)
    if violations:
        raise ExpressionViolation("; ".join(violations))
    code = compile(ast.parse(expr, mode="eval"), "<expr>", "eval")
    return eval(code, {"__builtins__": {}}, {**_EXPR_NS, **row})
