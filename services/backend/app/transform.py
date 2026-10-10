"""字段映射与转换规则的应用（任务 4.2）。

将 TaskConfig.fields 的规则应用到样例行，产出预览映射。
表达式经 AST 白名单校验；校验失败不允许保存（见 api）。
"""

from __future__ import annotations

from app.expr.whitelist import apply_expression, validate_expression
from app.models.task_config import FieldRule, TransformKind


def _cast_value(value: object, cast_to: str | None) -> object:
    if cast_to in ("int", "float", "str", "bool", "bool_int"):
        try:
            if cast_to == "bool_int":
                return 1 if value else 0
            return {"int": int, "float": float, "str": str, "bool": bool}[cast_to](value)
        except (TypeError, ValueError):
            return None
    return value


def apply_rule(rule: FieldRule, row: dict) -> object:
    """对单条规则应用到一行，返回目标值。"""
    if rule.kind is TransformKind.DIRECT:
        return row.get(rule.source)
    if rule.kind is TransformKind.CONSTANT:
        return rule.constant_value
    if rule.kind is TransformKind.NULL_FILL:
        val = row.get(rule.source)
        if val is None or val == "":
            return rule.null_default
        return _cast_value(val, rule.cast_to)
    if rule.kind is TransformKind.CAST:
        return _cast_value(row.get(rule.source), rule.cast_to)
    if rule.kind is TransformKind.EXPRESSION:
        return apply_expression(rule.expression or "", {**row, **row_names(row)})
    return row.get(rule.source)


def row_names(row: dict) -> dict:
    """将列名投影为可作为标识符引用的名称（简单映射，供表达式取值）。"""
    out: dict = {}
    for k, v in row.items():
        if k and k.isidentifier():
            out[k] = v
    return out


def apply_rules(fields: list[FieldRule], rows: list[dict]) -> list[dict]:
    """对多条样例行应用全部规则，返回映射后的行。"""
    result: list[dict] = []
    for row in rows:
        out = {}
        for rule in fields:
            out[rule.target or rule.source] = apply_rule(rule, row)
        result.append(out)
    return result


def validate_rules(fields: list[FieldRule]) -> list[str]:
    """校验全部规则；返回违规列表。"""
    violations: list[str] = []
    for rule in fields:
        if rule.kind is TransformKind.EXPRESSION:
            expr = rule.expression or ""
            bad = validate_expression(expr)
            for b in bad:
                violations.append(f"字段[{rule.target or rule.source}]: {b}")
        if not rule.target and not rule.source:
            violations.append("字段缺少 source/target")
    return violations