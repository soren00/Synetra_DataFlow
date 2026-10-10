"""只读 SQL 守卫（任务 3.1）。

约束：仅允许单条 SELECT；拒绝 DML/DDL/多语句等副作用语句；
字符串字面量内的关键词不误判。
"""

from __future__ import annotations

import sqlparse
from sqlparse.sql import TokenList
from sqlparse.tokens import Keyword, Name, String

# DML / DDL / 特权 / 其他有副作用的关键词
_RISKY: frozenset[str] = frozenset({
    "INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE", "UPSERT",
    "DROP", "ALTER", "CREATE", "TRUNCATE", "RENAME", "COMMENT",
    "GRANT", "REVOKE", "CALL", "EXEC", "EXECUTE", "LOAD", "LOCK", "UNLOCK",
    "ANALYZE", "OPTIMIZE", "SET", "USE", "ATTACH", "DETACH", "VACUUM",
    "SHUTDOWN", "PURGE",
})


def _outermost_head(parsed: TokenList) -> str:
    """取最外层（括号外）首个实质关键词，例如 SELECT / WITH / UNION。"""
    depth = 0
    for tok in parsed.flatten():
        if tok.is_whitespace:
            continue
        if tok.value == "(":
            depth += 1
        elif tok.value == ")":
            depth -= 1
        elif depth == 0 and tok.ttype in Keyword:
            return tok.normalized.upper()
    return ""


def _scan_risky(parsed: TokenList) -> list[str]:
    """扫描非字符串 token 中的风险关键词。"""
    hits: list[str] = []
    for tok in parsed.flatten():
        if tok.ttype in String:
            continue
        if tok.ttype in (Keyword, Name.Builtin, Name.Function):
            norm = tok.normalized.upper()
            if norm in _RISKY:
                hits.append(norm)
    return hits


def validate_read_only(sql: str) -> list[str]:
    """返回违规列表；空列表表示可通过。"""
    violations: list[str] = []
    statements = [s for s in sqlparse.split(sql) if s and s.strip()]
    if not statements:
        return ["SQL 为空"]
    if len(statements) > 1:
        violations.append(f"检测到 {len(statements)} 条语句（含分号/批量执行），仅允许单条 SELECT")

    try:
        parsed = sqlparse.parse(statements[0])[0]
    except Exception:  # noqa: BLE001 - 解析失败一律拒绝
        return ["SQL 解析失败，无法证实为只读 SELECT"]

    head = _outermost_head(parsed)
    if head != "SELECT":
        violations.append(f"仅允许单条 SELECT，语句头为: {head or '<none>'}")

    risky = _scan_risky(parsed)
    if risky:
        violations.append("包含被禁止语句/副作用关键词: " + ", ".join(sorted(set(risky))))

    return violations