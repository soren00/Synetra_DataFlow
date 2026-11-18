"""步骤注册表：每类步骤的元数据与配置契约（单一事实源）。

前端的节点面板与节点配置抽屉由 `GET /api/pipeline/steps` 返回本注册表驱动，
避免前后端各维护一份步骤清单造成漂移（与表达式白名单同源的思路一致）。

每个步骤声明：
- `label` / `category`：展示名与五类分组；
- `min_in` / `max_in` / `min_out` / `max_out`：入边与出边数量约束（-1 表示不限）；
- `blocking`：是否需要跨行全量（排序/去重/聚合）；
- `config_schema`：配置项清单（key/label/type/required/options/help），供 UI 生成表单；
- `rules_editor`：是否在节点内提供「字段规则」编辑器（复用既有五类规则）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.pipeline import StepType


@dataclass(frozen=True)
class StepSpec:
    type: StepType
    label: str
    category: str  # source | merge | row | batch | target
    min_in: int = 1
    max_in: int = 1
    min_out: int = 1
    max_out: int = 1
    blocking: bool = False
    rules_editor: bool = False
    config_schema: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {
            "type": self.type.value,
            "label": self.label,
            "category": self.category,
            "min_in": self.min_in,
            "max_in": self.max_in,
            "min_out": self.min_out,
            "max_out": self.max_out,
            "blocking": self.blocking,
            "rules_editor": self.rules_editor,
            "config_schema": self.config_schema,
        }


def _conn_field(label: str = "连接") -> dict:
    return {"key": "conn_key", "label": label, "type": "connection", "required": True}


SPECS: dict[StepType, StepSpec] = {
    # ---- 源 ----
    StepType.SOURCE_TABLE: StepSpec(
        type=StepType.SOURCE_TABLE, label="关系库表", category="source",
        min_in=0, max_in=0, min_out=1, max_out=1,
        config_schema={"fields": [
            _conn_field(),
            {"key": "object", "label": "表名", "type": "text", "required": True,
             "placeholder": "orders"},
            {"key": "watermark_field", "label": "增量字段（可选）", "type": "text",
             "help": "配置后仅在存在已保存水位时按该字段过滤"},
            {"key": "track_watermark", "label": "水位推进字段（可选）", "type": "text",
             "help": "配置后按该列最大值推进水位；留空则不推进（旧 fields 配置编译时默认取增量字段）"},
        ]},
    ),
    StepType.SOURCE_SQL: StepSpec(
        type=StepType.SOURCE_SQL, label="只读 SQL", category="source",
        min_in=0, max_in=0, min_out=1, max_out=1,
        config_schema={"fields": [
            _conn_field(),
            {"key": "sql", "label": "SQL", "type": "sql", "required": True,
             "help": "仅允许只读语句"},
        ]},
    ),
    StepType.SOURCE_CSV: StepSpec(
        type=StepType.SOURCE_CSV, label="CSV 文件", category="source",
        min_in=0, max_in=0, min_out=1, max_out=1,
        config_schema={"fields": [
            {"key": "path", "label": "文件路径或 URL", "type": "text", "required": True},
            {"key": "delimiter", "label": "分隔符", "type": "text", "default": ","},
        ]},
    ),
    StepType.SOURCE_API: StepSpec(
        type=StepType.SOURCE_API, label="REST/API", category="source",
        min_in=0, max_in=0, min_out=1, max_out=1,
        config_schema={"fields": [
            _conn_field(),
            {"key": "path", "label": "接口路径", "type": "text", "default": ""},
            {"key": "records_path", "label": "记录数组字段（可选）", "type": "text",
             "placeholder": "data.items"},
        ]},
    ),
    # ---- 合并 ----
    StepType.UNION: StepSpec(
        type=StepType.UNION, label="多源合并（union）", category="merge",
        min_in=2, max_in=-1, min_out=1, max_out=1, blocking=False,
        config_schema={"fields": [
            {"key": "columns", "label": "结果列（可选，留空取各输入列并集）", "type": "columns"},
            {"key": "aliases", "label": "结果列重命名（可选）", "type": "mapping",
             "help": "形如 源列=结果列；未列出的列保持原名，重名时后者覆盖前者"},
        ]},
    ),
    # ---- 行级 ----
    StepType.SELECT: StepSpec(
        type=StepType.SELECT, label="字段选择与映射", category="row", rules_editor=True,
        config_schema={"fields": [
            {"key": "rules", "label": "字段规则", "type": "field_rules"},
        ]},
    ),
    StepType.FILTER: StepSpec(
        type=StepType.FILTER, label="行过滤", category="row",
        config_schema={"fields": [
            {"key": "expression", "label": "保留条件（受限表达式）", "type": "expression",
             "required": True, "help": "结果为真时保留该行"},
        ]},
    ),
    StepType.CAST: StepSpec(
        type=StepType.CAST, label="类型转换", category="row",
        config_schema={"fields": [
            {"key": "field", "label": "源字段", "type": "text", "required": True},
            {"key": "target", "label": "目标字段", "type": "text", "default": ""},
            {"key": "cast_to", "label": "目标类型", "type": "select", "required": True,
             "options": ["int", "float", "str", "bool", "bool_int"]},
        ]},
    ),
    StepType.EXPRESSION: StepSpec(
        type=StepType.EXPRESSION, label="受限表达式", category="row",
        config_schema={"fields": [
            {"key": "target", "label": "目标字段", "type": "text", "required": True},
            {"key": "expression", "label": "表达式", "type": "expression", "required": True},
        ]},
    ),
    StepType.VALUE_MAP: StepSpec(
        type=StepType.VALUE_MAP, label="码值映射", category="row",
        config_schema={"fields": [
            {"key": "field", "label": "源字段", "type": "text", "required": True},
            {"key": "target", "label": "目标字段", "type": "text", "default": ""},
            {"key": "mapping", "label": "映射表", "type": "mapping", "required": True},
            {"key": "default", "label": "未命中时的默认值", "type": "text", "default": ""},
        ]},
    ),
    StepType.NULL_FILL: StepSpec(
        type=StepType.NULL_FILL, label="空值填充", category="row",
        config_schema={"fields": [
            {"key": "field", "label": "源字段", "type": "text", "required": True},
            {"key": "target", "label": "目标字段", "type": "text", "default": ""},
            {"key": "default", "label": "填充值", "type": "text", "required": True},
        ]},
    ),
    StepType.STRING_OPS: StepSpec(
        type=StepType.STRING_OPS, label="字符串处理", category="row",
        config_schema={"fields": [
            {"key": "field", "label": "源字段", "type": "text", "required": True},
            {"key": "target", "label": "目标字段", "type": "text", "default": ""},
            {"key": "op", "label": "操作", "type": "select", "required": True,
             "options": ["trim", "lower", "upper", "replace", "substr"]},
            {"key": "args", "label": "参数", "type": "string_list",
             "help": "replace 需要 [旧, 新]；substr 需要 [起, 止]"},
        ]},
    ),
    StepType.CONSTANT: StepSpec(
        type=StepType.CONSTANT, label="常量列", category="row",
        config_schema={"fields": [
            {"key": "target", "label": "目标字段", "type": "text", "required": True},
            {"key": "value", "label": "常量值", "type": "text", "default": ""},
        ]},
    ),
    StepType.SEQUENCE: StepSpec(
        type=StepType.SEQUENCE, label="序列号", category="row",
        config_schema={"fields": [
            {"key": "target", "label": "目标字段", "type": "text", "required": True},
            {"key": "start", "label": "起始值", "type": "number", "default": 1},
            {"key": "step", "label": "步长", "type": "number", "default": 1},
        ]},
    ),
    # ---- 整批 ----
    StepType.SORT: StepSpec(
        type=StepType.SORT, label="排序", category="batch", blocking=True,
        config_schema={"fields": [
            {"key": "keys", "label": "排序键", "type": "sort_keys", "required": True},
        ]},
    ),
    StepType.UNIQUE: StepSpec(
        type=StepType.UNIQUE, label="去重", category="batch", blocking=True,
        config_schema={"fields": [
            {"key": "keys", "label": "去重键", "type": "string_list", "required": True,
             "help": "留空表示整行去重"},
        ]},
    ),
    StepType.AGGREGATE: StepSpec(
        type=StepType.AGGREGATE, label="分组聚合", category="batch", blocking=True,
        config_schema={"fields": [
            {"key": "group_by", "label": "分组键", "type": "string_list", "default": []},
            {"key": "aggregations", "label": "聚合项", "type": "aggregations", "required": True},
        ]},
    ),
    # ---- 目标 ----
    StepType.TARGET_TABLE: StepSpec(
        type=StepType.TARGET_TABLE, label="关系库目标", category="target",
        min_in=1, max_in=1, min_out=0, max_out=0,
        config_schema={"fields": [
            _conn_field("目标连接"),
            {"key": "object", "label": "目标表", "type": "text", "required": True},
            {"key": "mode", "label": "写入方式", "type": "select", "default": "append",
             "options": ["append", "upsert"]},
            {"key": "primary_keys", "label": "主键（upsert 必填）", "type": "string_list",
             "default": []},
        ]},
    ),
    StepType.TARGET_CSV: StepSpec(
        type=StepType.TARGET_CSV, label="CSV 文件目标", category="target",
        min_in=1, max_in=1, min_out=0, max_out=0,
        config_schema={"fields": [
            {"key": "path", "label": "输出文件路径", "type": "text", "required": True},
        ]},
    ),
}


def get_spec(step_type: StepType) -> StepSpec:
    try:
        return SPECS[step_type]
    except KeyError:  # pragma: no cover - 枚举完备性由注册表测试守门
        raise KeyError(f"未注册的步骤类型: {getattr(step_type, 'value', step_type)}")


def all_specs() -> list[StepSpec]:
    """按五类分组顺序返回全部步骤声明。"""
    order = ["source", "merge", "row", "batch", "target"]
    return sorted(SPECS.values(), key=lambda s: (order.index(s.category), s.label))


def steps_payload() -> list[dict]:
    """对外（前端）返回的步骤清单。"""
    return [s.public() for s in all_specs()]


def blocking_types() -> frozenset[StepType]:
    return frozenset(t for t, s in SPECS.items() if s.blocking)


__all__ = [
    "SPECS",
    "StepSpec",
    "all_specs",
    "blocking_types",
    "get_spec",
    "steps_payload",
]
