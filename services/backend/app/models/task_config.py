"""ETL 任务配置模型（任务 4.x）。

向导/脚本生成的唯一配置源：字段选择、映射与转换规则、增量/水位、主键 upsert。
不落明文凭证（只存 conn_key 引用）；LLM 生成路径前后端都只读写该结构。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class InjectMode(str, Enum):
    """写入方式。"""

    APPEND = "append"
    UPSERT = "upsert"


class TransformKind(str, Enum):
    """字段处理方式。"""

    DIRECT = "direct"
    CONSTANT = "constant"
    NULL_FILL = "null_fill"
    CAST = "cast"
    EXPRESSION = "expression"


class FieldRule(BaseModel):
    source: str
    target: str = ""
    kind: TransformKind = TransformKind.DIRECT
    cast_to: str | None = None
    null_default: str | None = None
    constant_value: str | None = None
    expression: str | None = None


class IncrementalConfig(BaseModel):
    field: str | None = None
    source: str = "field"
    watermark_field: str | None = None
    first_run_policy: str = "all"  # all=首跑全量 | incremental=首跑仅增量；向后兼容，旧配置缺省为 all


class TaskConfig(BaseModel):
    version: int = 1
    project: str
    name: str
    source_conn_key: str
    target_conn_key: str
    source_object: str = ""
    target_object: str = ""
    mode: InjectMode = InjectMode.APPEND
    primary_keys: list[str] = Field(default_factory=list)
    fields: list[FieldRule] = Field(default_factory=list)
    incremental: IncrementalConfig = Field(default_factory=IncrementalConfig)
    schedule: str = ""
    schedule_enabled: bool = True
    schedule_next_run: str = ""  # ISO UTC，下次调度触发时间；随 config 持久化
    updated_at: str = ""
    updated_by: str = ""