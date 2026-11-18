"""多步清洗流水线：配置契约、步骤注册表、解释执行引擎与旧配置等价编译。"""

from __future__ import annotations

from .compile import (
    SOURCE_ID,
    TARGET_ID,
    TRANSFORM_ID,
    compile_fields_to_pipeline,
    compile_task_pipeline,
    sync_task_pipeline,
)
from .engine import (
    NodeMetric,
    PipelineRunError,
    RunOutcome,
    cleanup_stale_spill,
    pipeline_summary,
    run_pipeline,
    write_snapshots,
)
from .registry import SPECS, StepSpec, all_specs, blocking_types, get_spec, steps_payload
from .schema import derive_union_schema
from .validate import validate_pipeline

__all__ = [
    "SOURCE_ID",
    "SPECS",
    "TARGET_ID",
    "TRANSFORM_ID",
    "NodeMetric",
    "PipelineRunError",
    "RunOutcome",
    "StepSpec",
    "all_specs",
    "blocking_types",
    "cleanup_stale_spill",
    "compile_fields_to_pipeline",
    "compile_task_pipeline",
    "derive_union_schema",
    "get_spec",
    "pipeline_summary",
    "run_pipeline",
    "steps_payload",
    "sync_task_pipeline",
    "validate_pipeline",
    "write_snapshots",
]
