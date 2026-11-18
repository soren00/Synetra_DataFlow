"""流水线编排 API：步骤清单、生效流水线、节点级试运行。

- GET  /api/pipeline/steps                     步骤类型清单（前端画布的唯一事实源）
- GET  /api/tasks/{key}/pipeline               任务生效流水线（旧配置返回等价编译结果）
- POST /api/tasks/{key}/nodes/{node_id}/preview 节点级试运行（只读、不写目标）
- POST /api/tasks/{key}/nodes/{node_id}/schema  合并节点的列集合与类型推导（画布核对）
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.etl_sdk.pipeline import (
    PipelineRunError,
    compile_task_pipeline,
    derive_union_schema,
    pipeline_summary,
    run_pipeline,
    steps_payload,
    validate_pipeline,
)
from app.models.pipeline import PipelineConfig
from app.store import Store

router = APIRouter()


def get_store() -> Store:
    return Store(get_settings().data_dir)


StoreDep = Annotated[Store, Depends(get_store)]


class NodePreviewRequest(BaseModel):
    """节点试运行入参。

    - `sample`：无源可读或希望用样例验证时注入的行；
    - `pipeline`：画布尚未保存时传入的临时流水线（缺省用已保存配置/等价编译结果）。
    """

    sample: list[dict] = Field(default_factory=list)
    limit: int = 100
    pipeline: dict | None = None


@router.get("/pipeline/steps")
def pipeline_steps() -> dict:
    """返回五类步骤清单（分类/名称/度数约束/blocking/配置 schema）。"""
    return {"steps": steps_payload()}


@router.get("/tasks/{task_key}/pipeline")
def task_pipeline(task_key: str, store: StoreDep) -> dict:
    try:
        cfg = store.get_task(task_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")
    pipe = compile_task_pipeline(cfg)
    return {
        "task_key": task_key,
        "pipeline": pipe.model_dump(mode="json"),
        "summary": pipeline_summary(pipe),
        "problems": validate_pipeline(pipe),
    }


class NodeSnapshotRequest(NodePreviewRequest):
    """手动保留样本：写入本地运行目录（不写目标库），run_id 缺省自动生成。"""

    run_id: str = ""


@router.post("/tasks/{task_key}/nodes/{node_id}/snapshot")
def node_snapshot(task_key: str, node_id: str, req: NodeSnapshotRequest,
                  store: StoreDep) -> dict:
    """对任意节点手动保留样本（4.5）：落到 data/runs/<run_id>/<node>.jsonl。"""
    import time

    try:
        cfg = store.get_task(task_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")
    pipe = (PipelineConfig.model_validate(req.pipeline) if req.pipeline
            else compile_task_pipeline(cfg))
    if node_id not in {n.id for n in pipe.nodes}:
        raise HTTPException(status_code=404, detail=f"节点不存在: {node_id}")
    run_id = req.run_id or f"manual-{int(time.time())}"
    try:
        outcome = run_pipeline(
            pipe, task_config=cfg.model_dump(mode="json"), dry_run=True,
            sample=req.sample or None, store=store, sample_node=node_id,
            sample_limit=max(1, min(int(req.limit or 1000), 1000)),
            run_id=run_id, collect_samples=True, snapshot_nodes=[node_id],
        )
    except PipelineRunError as e:
        raise HTTPException(status_code=422, detail=f"节点 {e.node_id} 执行失败: {e.error}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"task_key": task_key, "node_id": node_id, "run_id": run_id,
            "rows": len(outcome.samples.get(node_id, [])),
            "snapshot": f"runs/{run_id}/{node_id}.jsonl"}


@router.post("/tasks/{task_key}/nodes/{node_id}/preview")
def node_preview(task_key: str, node_id: str, req: NodePreviewRequest,
                 store: StoreDep) -> dict:
    """执行到指定节点为止并返回样本；绝不写目标。"""
    try:
        cfg = store.get_task(task_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")
    if req.pipeline:
        try:
            pipe = PipelineConfig.model_validate(req.pipeline)
        except Exception as e:  # noqa: BLE001 - 画布草稿非法时返回可读原因
            raise HTTPException(status_code=422, detail=f"流水线配置非法: {e}")
    else:
        pipe = compile_task_pipeline(cfg)
    if node_id not in {n.id for n in pipe.nodes}:
        raise HTTPException(status_code=404, detail=f"节点不存在: {node_id}")
    try:
        outcome = run_pipeline(
            pipe,
            task_config=cfg.model_dump(mode="json"),
            dry_run=True,
            sample=req.sample or None,
            store=store,
            sample_node=node_id,
            sample_limit=max(1, min(int(req.limit or 100), 1000)),
        )
    except PipelineRunError as e:
        raise HTTPException(
            status_code=422,
            detail=f"节点 {e.node_id} 执行失败: {e.error}",
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {
        "task_key": task_key,
        "node_id": node_id,
        "sample": outcome.samples.get(node_id, []),
        "metrics": outcome.metrics,
        "spilled": outcome.spilled,
    }


class NodeSchemaRequest(NodePreviewRequest):
    """合并节点列对齐推导入参；`sample` 缺省时按真实源分批探测（上限 limit 行）。"""


@router.post("/tasks/{task_key}/nodes/{node_id}/schema")
def node_schema(task_key: str, node_id: str, req: NodeSchemaRequest,
                store: StoreDep) -> dict:
    """推导合并节点的入边列集合、对齐结果列与类型提升（只读、不写目标）。"""
    try:
        cfg = store.get_task(task_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")
    if req.pipeline:
        try:
            pipe = PipelineConfig.model_validate(req.pipeline)
        except Exception as e:  # noqa: BLE001 - 画布草稿非法时返回可读原因
            raise HTTPException(status_code=422, detail=f"流水线配置非法: {e}")
    else:
        pipe = compile_task_pipeline(cfg)
    if node_id not in {n.id for n in pipe.nodes}:
        raise HTTPException(status_code=404, detail=f"节点不存在: {node_id}")
    try:
        result = derive_union_schema(
            pipe, node_id,
            sample=req.sample or None,
            store=store,
            task_config=cfg.model_dump(mode="json"),
            limit=max(1, min(int(req.limit or 200), 1000)),
        )
    except PipelineRunError as e:
        raise HTTPException(status_code=422, detail=f"节点 {e.node_id} 执行失败: {e.error}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"task_key": task_key, **result}
