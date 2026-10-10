"""脚本静态校验（任务 5.3/5.4）。

- syntax_check：仅做 Python 语法编译检查（手写路径保存前的可选校验）。
- static_validate：LLM 生成路径的更强约束——必须源自 etl_sdk 契约（导入白名单、
  dry-run 兼容、无危险内建使用）。
"""

from __future__ import annotations

import ast

_ALLOWED_TOP_IMPORTS = frozenset({"etl_sdk", "os", "json", "sys", "types"})


def syntax_check(source: str) -> list[str]:
    try:
        compile(source, "<script>", "exec")
    except SyntaxError as e:
        return [f"语法错误: {e.msg} (行 {e.lineno})"]
    return []


def static_validate(source: str) -> list[str]:
    violations = syntax_check(source)
    if violations:
        return violations
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in _ALLOWED_TOP_IMPORTS:
                    violations.append(f"禁止导入 {alias.name}")
        if (isinstance(node, ast.ImportFrom) and node.module
                and node.module.split(".")[0] not in _ALLOWED_TOP_IMPORTS):
            violations.append(f"禁止导入 {node.module}")
    return violations