"""任务执行编排：手动触发与定时触发共用同一真实执行通道。

统一处理：
- 增量水位：执行前从元数据读取该任务已保存水位并注入，成功后推进，失败保持原值；
- 执行通道：真实执行（非 dry-run），运行记录标注通道与批次参数；
- 步级指标：写入 run_step，失败时同时记录失败节点。

调度器与 API 都调用 `execute_task`，避免两条通道语义再次分叉
（历史问题：定时触发误用 dry-run，导致「定时成功但没写数据」）。
"""

from __future__ import annotations

from typing import Any

from app.etl_sdk.pipeline.compile import compile_task_pipeline
from app.scripting.executor import run_real_script


def execute_task(store: Any, scripts: Any, task_key: str, run_id: str) -> dict:
    """执行一次任务（由 run_with_retry 包裹重试）；返回 {status, rows, error?, node?}。"""
    cfg = store.get_task(task_key)
    if not scripts.has_current(task_key):
        return {"status": "failed", "rows": 0,
                "error": "该任务尚无已确认脚本，请先生成/编写脚本"}

    watermark = store.get_watermark(task_key) if (cfg.incremental.field or "").strip() else ""

    result = run_real_script(
        scripts.read_current(task_key),
        cfg.model_dump(mode="json"),
        watermark=watermark,
        run_id=run_id,
    )

    # 步级指标与失败节点（成功/失败都尽量落库，便于运行详情定位）
    steps = getattr(result, "step_metrics", []) or []
    if steps:
        store.add_run_steps(run_id, steps)
    failed = getattr(result, "failed_step", {}) or {}

    if result.ok:
        rows = result.total_records()
        if steps:
            blocked = [m.get("node_id") for m in steps if m.get("status") == "failed"]
            if blocked:
                return {"status": "failed", "rows": rows,
                        "error": f"步骤 {blocked[0]} 执行失败",
                        "node": blocked[0]}
        new_watermark = getattr(result, "watermark", None)
        if watermark_field_configured(cfg) and new_watermark:
            store.set_watermark(task_key, str(new_watermark))
            store.append_run_log(run_id, f"水位推进至 {new_watermark}", level="info")
        store.append_run_log(run_id, f"执行完成，写入 {rows} 行", level="info")
        return {"status": "success", "rows": rows}

    error = (result.stderr or "").strip()
    node = str(failed.get("node") or "")
    if failed.get("error"):
        error = f"步骤 {node} 失败: {failed['error']}"
    store.append_run_log(run_id, f"脚本执行失败: {error}", level="error")
    return {"status": "failed", "rows": 0, "error": error, "node": node}


def watermark_field_configured(cfg) -> bool:
    inc = cfg.incremental
    return bool((inc.watermark_field or inc.field or "").strip())


def pipeline_sizes(cfg) -> tuple[int, int]:
    """本次执行使用的批次与提交批次（用于运行记录回溯）。"""
    try:
        pipe = compile_task_pipeline(cfg)
    except Exception:  # noqa: BLE001 - 配置异常不阻塞运行记录
        return 0, 0
    return int(pipe.batch_size), int(pipe.commit_size)


__all__ = ["execute_task", "pipeline_sizes", "watermark_field_configured"]
