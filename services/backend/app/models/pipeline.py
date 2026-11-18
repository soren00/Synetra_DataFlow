"""多步清洗流水线配置模型（流水线契约）。

以「节点 + 连线」的有向无环图描述一个任务内的数据处理过程：
- `PipelineNode`：节点标识、步骤类型与步骤配置；
- `PipelineEdge`：数据流向；
- `PipelineConfig`：节点集合、连线集合与执行批次参数。

本模块只做**结构**校验（标识唯一、连线引用存在、无自环、无环、端点完备）；
步骤度数约束与合并列对齐等语义校验在 `app.etl_sdk.pipeline.validate` 中完成，
因为那部分依赖步骤注册表，放在 models 层会形成反向依赖。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

_IDENT_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


class StepType(str, Enum):
    """步骤类型（五类：源 / 合并 / 行级 / 整批 / 目标）。"""

    # 源
    SOURCE_TABLE = "source_table"
    SOURCE_SQL = "source_sql"
    SOURCE_CSV = "source_csv"
    SOURCE_API = "source_api"
    # 合并
    UNION = "union"
    # 行级
    SELECT = "select"
    FILTER = "filter"
    CAST = "cast"
    EXPRESSION = "expression"
    VALUE_MAP = "value_map"
    NULL_FILL = "null_fill"
    STRING_OPS = "string_ops"
    CONSTANT = "constant"
    SEQUENCE = "sequence"
    # 整批
    SORT = "sort"
    UNIQUE = "unique"
    AGGREGATE = "aggregate"
    # 目标
    TARGET_TABLE = "target_table"
    TARGET_CSV = "target_csv"


class PipelineNode(BaseModel):
    """流水线节点。

    `config` 为步骤私有配置（见 `app.etl_sdk.pipeline.registry` 的 `config_schema`）。
    连接一律以 `conn_key` 引用，不得出现明文凭据。
    """

    id: str
    type: StepType
    name: str = ""
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not v:
            raise ValueError("节点标识不能为空")
        if v[0].isdigit() or any(ch not in _IDENT_ALLOWED for ch in v):
            raise ValueError(f"节点标识「{v}」非法，仅允许字母、数字与下划线且不以数字开头")
        return v


class PipelineEdge(BaseModel):
    """连线：source 节点的输出作为 target 节点的输入。"""

    source: str
    target: str


class PipelineGraphError(ValueError):
    """图结构错误（成环、引用缺失、端点缺失等），消息面向用户可读。"""


class PipelineConfig(BaseModel):
    """流水线配置：节点 + 连线 + 执行参数。"""

    version: int = 1
    nodes: list[PipelineNode] = Field(default_factory=list)
    edges: list[PipelineEdge] = Field(default_factory=list)
    batch_size: int = 5000
    commit_size: int = 5000
    spill_threshold_mb: int = 256
    #: 内存物化行数阈值；>0 时覆盖按 MB 推导的阈值（便于测试与调优）
    spill_rows: int = 0

    @field_validator("batch_size", "commit_size")
    @classmethod
    def _check_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("批次大小必须为正整数")
        return v

    # ---- 结构查询 ----

    def node(self, node_id: str) -> PipelineNode:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise PipelineGraphError(f"节点不存在: {node_id}")

    def incoming(self, node_id: str) -> list[PipelineEdge]:
        return [e for e in self.edges if e.target == node_id]

    def outgoing(self, node_id: str) -> list[PipelineEdge]:
        return [e for e in self.edges if e.source == node_id]

    def upstream_ids(self, node_id: str) -> list[str]:
        return [e.source for e in self.incoming(node_id)]

    def source_nodes(self) -> list[PipelineNode]:
        return [n for n in self.nodes if n.type.value.startswith("source_")]

    def target_nodes(self) -> list[PipelineNode]:
        return [n for n in self.nodes if n.type.value.startswith("target_")]

    def ancestors(self, node_id: str) -> set[str]:
        """返回 node_id 的全部祖先（不含自身）；成环时抛 PipelineGraphError。"""
        seen: set[str] = set()
        stack = list(self.upstream_ids(node_id))
        while stack:
            cur = stack.pop()
            if cur == node_id:
                raise PipelineGraphError(f"流水线存在环路，涉及节点: {node_id}")
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.upstream_ids(cur))
        return seen

    def topo_order(self) -> list[str]:
        """拓扑序（Kahn）；存在环时抛出带定位信息的错误。"""
        ids = [n.id for n in self.nodes]
        indeg = {i: 0 for i in ids}
        for e in self.edges:
            indeg[e.target] = indeg.get(e.target, 0) + 1
        ready = [i for i in ids if indeg[i] == 0]
        order: list[str] = []
        while ready:
            cur = ready.pop(0)
            order.append(cur)
            for e in self.outgoing(cur):
                indeg[e.target] -= 1
                if indeg[e.target] == 0:
                    ready.append(e.target)
        if len(order) != len(ids):
            stuck = [i for i in ids if i not in order]
            raise PipelineGraphError(f"流水线存在环路，无法确定执行顺序: {', '.join(stuck)}")
        return order

    # ---- 结构校验 ----

    def validate_structure(self) -> list[str]:
        """返回结构违规列表；空列表表示结构合法。"""
        problems: list[str] = []
        if not self.nodes:
            return ["流水线没有任何节点"]
        ids = [n.id for n in self.nodes]
        dup = {i for i in ids if ids.count(i) > 1}
        for i in sorted(dup):
            problems.append(f"节点标识重复: {i}")
        known = set(ids)
        seen_edges: set[tuple[str, str]] = set()
        for e in self.edges:
            if e.source not in known:
                problems.append(f"连线引用了不存在的上游节点: {e.source}")
            if e.target not in known:
                problems.append(f"连线引用了不存在的下游节点: {e.target}")
            if e.source == e.target:
                problems.append(f"连线不能自环: {e.source}")
            key = (e.source, e.target)
            if key in seen_edges:
                problems.append(f"重复连线: {e.source} → {e.target}")
            seen_edges.add(key)
        if not problems:
            if not self.source_nodes():
                problems.append("流水线缺少源节点")
            if not self.target_nodes():
                problems.append("流水线缺少目标节点")
            try:
                self.topo_order()
            except PipelineGraphError as e:
                problems.append(str(e))
        return problems
