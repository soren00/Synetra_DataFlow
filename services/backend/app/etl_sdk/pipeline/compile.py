"""旧字段规则配置 → 三节点流水线的等价编译。

存量任务只有 `TaskConfig.fields`（源 → 一次列映射 → 目标），本模块把它编译为
`source_table → select → target_table` 三节点流水线，使旧配置零迁移即可走新执行引擎；
编译结果必须与原配置的抽取范围、字段映射、转换规则、写入模式与增量语义一致。
"""

from __future__ import annotations

import re

from app.core.config import get_settings
from app.models.pipeline import PipelineConfig, PipelineEdge, PipelineNode, StepType
from app.models.task_config import TaskConfig

SOURCE_ID = "source"
TRANSFORM_ID = "transform"
TARGET_ID = "target"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _node_id(base: str, prefix: str) -> str:
    """节点标识必须是合法标识符：非标识符基名退化为带序号的安全名。"""
    candidate = f"{prefix}_{base}" if prefix else base
    if _IDENT_RE.match(candidate):
        return candidate
    safe = re.sub(r"[^A-Za-z0-9_]", "_", candidate)
    if not safe or safe[0].isdigit():
        safe = f"n_{safe}"
    return safe


def compile_fields_to_pipeline(cfg: TaskConfig) -> PipelineConfig:
    """把字段规则配置编译为三节点流水线（纯函数）。"""
    incremental_source = (cfg.incremental.field or "").strip()
    track_field = (cfg.incremental.watermark_field or cfg.incremental.field or "").strip()

    source = PipelineNode(
        id=SOURCE_ID,
        type=StepType.SOURCE_TABLE,
        name="数据源",
        config={
            "conn_key": cfg.source_conn_key,
            "object": cfg.source_object,
            "watermark_field": incremental_source,
            "track_watermark": track_field,
        },
    )
    transform = PipelineNode(
        id=TRANSFORM_ID,
        type=StepType.SELECT,
        name="字段映射",
        config={"rules": [r.model_dump(mode="json") for r in cfg.fields]},
    )
    target = PipelineNode(
        id=TARGET_ID,
        type=StepType.TARGET_TABLE,
        name="数据目标",
        config={
            "conn_key": cfg.target_conn_key,
            "object": cfg.target_object,
            "mode": cfg.mode.value,
            "primary_keys": list(cfg.primary_keys),
        },
    )
    settings = get_settings()
    return PipelineConfig(
        version=1,
        nodes=[source, transform, target],
        edges=[
            PipelineEdge(source=SOURCE_ID, target=TRANSFORM_ID),
            PipelineEdge(source=TRANSFORM_ID, target=TARGET_ID),
        ],
        batch_size=max(1, int(settings.pipeline_batch_size)),
        commit_size=max(1, int(settings.pipeline_commit_size)),
        spill_threshold_mb=max(1, int(settings.pipeline_spill_threshold_mb)),
    )


def compile_task_pipeline(cfg: TaskConfig) -> PipelineConfig:
    """返回任务生效的流水线：显式配置优先，否则由字段规则等价编译。"""
    pipe = cfg.pipeline
    if pipe is not None and pipe.nodes:
        return pipe
    return compile_fields_to_pipeline(cfg)


def sync_task_pipeline(cfg: TaskConfig) -> PipelineConfig:
    """确保任务配置带有流水线（保存链路调用）；返回生效流水线。"""
    pipe = compile_task_pipeline(cfg)
    if cfg.pipeline is None or not cfg.pipeline.nodes:
        cfg.pipeline = pipe
    return pipe


__all__ = [
    "SOURCE_ID",
    "TARGET_ID",
    "TRANSFORM_ID",
    "compile_fields_to_pipeline",
    "compile_task_pipeline",
    "sync_task_pipeline",
]
