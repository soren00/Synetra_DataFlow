"""流水线语义校验：图合法性与步骤配置校验。

与 `PipelineConfig.validate_structure`（纯结构）互补：
本模块依赖步骤注册表的度数约束与字段规则的表达式白名单，因此放在 etl_sdk 侧。
所有错误信息都以「节点[<id>]: 原因」或「连线 a → b: 原因」的形式定位，
便于前端在画布上高亮。
"""

from __future__ import annotations

from app.expr.whitelist import validate_expression
from app.models.pipeline import PipelineConfig, StepType
from app.models.task_config import FieldRule, TransformKind
from app.transform import validate_rules

from .registry import get_spec

#: 各类型必需的配置键
_REQUIRED: dict[StepType, tuple[str, ...]] = {
    StepType.SOURCE_TABLE: ("conn_key",),
    StepType.SOURCE_SQL: ("conn_key", "sql"),
    StepType.SOURCE_CSV: ("path",),
    StepType.SOURCE_API: ("conn_key",),
    StepType.FILTER: ("expression",),
    StepType.CAST: ("field", "cast_to"),
    StepType.EXPRESSION: ("target", "expression"),
    StepType.VALUE_MAP: ("field", "mapping"),
    StepType.NULL_FILL: ("field", "default"),
    StepType.STRING_OPS: ("field", "op"),
    StepType.CONSTANT: ("target",),
    StepType.SEQUENCE: ("target",),
    StepType.SORT: ("keys",),
    StepType.TARGET_TABLE: ("conn_key",),
    StepType.TARGET_CSV: ("path",),
}


def _check_config(node) -> list[str]:
    problems: list[str] = []
    cfg = node.config or {}
    for key in _REQUIRED.get(node.type, ()):  # type: ignore[arg-type]
        value = cfg.get(key)
        if value in (None, "", [], {}):
            problems.append(f"节点[{node.id}]: 缺少必填配置 {key}")
    if (node.type is StepType.TARGET_TABLE
            and str(cfg.get("mode", "append")) == "upsert"
            and not cfg.get("primary_keys")):
        problems.append(f"节点[{node.id}]: upsert 模式需要至少一个主键")
    if node.type is StepType.FILTER and cfg.get("expression"):
        for v in validate_expression(str(cfg["expression"])):
            problems.append(f"节点[{node.id}]: 过滤条件违规 - {v}")
    if node.type is StepType.EXPRESSION and cfg.get("expression"):
        for v in validate_expression(str(cfg["expression"])):
            problems.append(f"节点[{node.id}]: 表达式违规 - {v}")
    if node.type is StepType.SELECT:
        rules = _rules(cfg)
        for v in validate_rules(rules):
            problems.append(f"节点[{node.id}]: {v}")
    if node.type is StepType.AGGREGATE:
        aggs = cfg.get("aggregations") or []
        if not aggs:
            problems.append(f"节点[{node.id}]: 至少需要一个聚合项")
        for a in aggs:
            func = str((a or {}).get("func", "")).lower()
            if func not in _AGG_FUNCS:
                problems.append(f"节点[{node.id}]: 不支持的聚合函数 {func or '(空)'}")
    if node.type is StepType.STRING_OPS:
        op = str(cfg.get("op", ""))
        if op not in _STRING_OPS:
            problems.append(f"节点[{node.id}]: 不支持的字符串操作 {op or '(空)'}")
    return problems


_AGG_FUNCS = {"sum", "count", "min", "max", "avg", "first", "last"}
_STRING_OPS = {"trim", "lower", "upper", "replace", "substr"}


def _rules(cfg: dict) -> list[FieldRule]:
    """把节点配置中的规则字典解析为 FieldRule（坏数据不会静默通过）。"""
    out: list[FieldRule] = []
    for raw in cfg.get("rules") or []:
        rule = FieldRule.model_validate(raw)
        if rule.kind is TransformKind.EXPRESSION and not (rule.expression or "").strip():
            raise ValueError(f"字段[{rule.target or rule.source}]: 表达式为空")
        out.append(rule)
    return out


def validate_pipeline(pipe: PipelineConfig) -> list[str]:
    """返回全部违规；空列表表示可保存与执行。"""
    problems: list[str] = list(pipe.validate_structure())
    if problems:
        return problems

    for node in pipe.nodes:
        spec = get_spec(node.type)
        deg_in = len(pipe.incoming(node.id))
        deg_out = len(pipe.outgoing(node.id))
        if deg_in < spec.min_in or (spec.max_in >= 0 and deg_in > spec.max_in):
            problems.append(
                f"节点[{node.id}]: 入边数量 {deg_in} 不符合「{spec.label}」约束"
                f"（{spec.min_in}..{'∞' if spec.max_in < 0 else spec.max_in}）")
        if deg_out < spec.min_out or (spec.max_out >= 0 and deg_out > spec.max_out):
            problems.append(
                f"节点[{node.id}]: 出边数量 {deg_out} 不符合「{spec.label}」约束"
                f"（{spec.min_out}..{'∞' if spec.max_out < 0 else spec.max_out}）")
        try:
            problems.extend(_check_config(node))
        except Exception as e:  # noqa: BLE001 - 规则解析失败即配置非法
            problems.append(f"节点[{node.id}]: 配置非法 - {e}")

    # 孤立节点：既不源可达也不通往目标（提示而非静默忽略）
    reachable: set[str] = set()
    for src in pipe.source_nodes():
        stack = [src.id]
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            stack.extend(e.target for e in pipe.outgoing(cur))
    for node in pipe.nodes:
        if node.id not in reachable:
            problems.append(f"节点[{node.id}]: 未与源节点连通，运行中不会被处理")

    # 连线指向的节点类型检查已由度数约束覆盖；此处补充配置一致性提示
    for node in pipe.target_nodes():
        if not pipe.incoming(node.id):
            problems.append(f"节点[{node.id}]: 目标节点没有输入")
    return problems


__all__ = ["validate_pipeline"]
