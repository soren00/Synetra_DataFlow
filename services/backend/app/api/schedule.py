"""调度执行 API（任务 8.x）。

- POST /tasks/{key}/run        手动触发执行（本地执行器 + 失败重试 + 失败邮件告警）
- POST /tasks/{key}/schedule   注册/cron 计划（本地调度）→ 返回持久化结果
- GET  /tasks/{key}/schedule   查询当前调度计划
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.alerting import send_failure_alert
from app.auth import require_role
from app.core.config import get_settings
from app.models.access import Role
from app.schedule import new_run_id, run_with_retry
from app.scripting.executor import run_real_script
from app.scripts import ScriptStore
from app.store import Store


class ScheduleRequest(BaseModel):
    schedule: str


class SchedulePatch(BaseModel):
    enabled: bool | None = None  # 启用/停用开关
    schedule: str | None = None  # 更新 cron 表达式
    schedule_next_run: str | None = None  # 更新下次运行时间


router = APIRouter()


def get_store() -> Store:
    return Store(get_settings().data_dir)


def get_scripts() -> ScriptStore:
    s = get_settings()
    root = s.scripts_root or f"{s.data_dir}"
    return ScriptStore(root)


StoreDep = Annotated[Store, Depends(get_store)]
ScriptsDep = Annotated[ScriptStore, Depends(get_scripts)]
DeveloperDep = Annotated[object, Depends(require_role(Role.DEVELOPER))]


def _task_or_404(store: Store, task_key: str):
    try:
        return store.get_task(task_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")


@router.post("/tasks/{task_key}/run")
def run_task(task_key: str, _: DeveloperDep, store: StoreDep, scripts: ScriptsDep) -> dict:
    """手动触发执行：建运行记录、有脚本则执行（本地执行器）、失败按策略重试并邮件告警。"""
    cfg = _task_or_404(store, task_key)
    run_id = new_run_id()
    via = "schedule" if (cfg.schedule or "").strip() else "manual"

    def execute():
        if not scripts.has_current(task_key):
            return {"status": "failed", "rows": 0,
                    "error": "该任务尚无已确认脚本，请先生成/编写脚本"}
        # 真实执行：不置 dry-run，readers/writers 走真实 DB 路径
        res = run_real_script(scripts.read_current(task_key), cfg.model_dump())
        if res.ok:
            store.append_run_log(run_id, f"执行完成，写入 {res.total_records()} 行", level="info")
            return {"status": "success", "rows": res.total_records()}
        store.append_run_log(run_id, f"脚本执行失败: {res.stderr.strip()}", level="error")
        return {"status": "failed", "rows": 0, "error": res.stderr.strip()}

    store.start_run(run_id, task_key, cfg.project, via, attempt=1)
    status = run_with_retry(store, run_id, execute,
                            retry_times=get_settings().retry_times,
                            retry_interval_s=get_settings().retry_interval_s)
    if status == "failed":
        run = store.get_run(run_id)
        err = (run or {}).get("error") or ""
        # 只透出错误根因（traceback 末行），避免整段堆栈淹没页面
        detail = [ln for ln in err.strip().splitlines() if ln.strip()][-1][:300] if err else "执行失败，详情见运行日志"
        send_failure_alert(task_key, cfg.project, err)
        return {"run_id": run_id, "task_key": task_key, "project": cfg.project,
                "status": status, "trigger_via": via, "detail": detail}
    return {"run_id": run_id, "task_key": task_key, "project": cfg.project,
            "status": status, "trigger_via": via}


@router.post("/tasks/{task_key}/schedule")
def schedule_task(task_key: str, req: ScheduleRequest, _: DeveloperDep,
                  store: StoreDep) -> dict:
    """新建/更新任务的执行计划：仅持久化 cron，本地调度器据此定时触发。"""
    cfg = _task_or_404(store, task_key)
    cfg.schedule = req.schedule
    store.save_task(cfg, task_key=task_key)
    # 清空下次运行时间，交由 LocalScheduler 下一轮用 next_cron_time 重新计算推进
    store.update_task_schedule(task_key, schedule_next_run="")
    store.log_event(actor="session-user", project=cfg.project, action="schedule_update",
                    detail={"task_key": task_key, "cron": req.schedule})
    schedule = (req.schedule or "").strip()
    return {"task_key": task_key, "schedule": schedule, "scheduled": bool(schedule)}


@router.get("/tasks/{task_key}/schedule")
def schedule_status(task_key: str, store: StoreDep) -> dict:
    cfg = _task_or_404(store, task_key)
    schedule = (cfg.schedule or "").strip()
    return {"task_key": task_key, "schedule": schedule,
            "scheduled": bool(schedule),
            "trigger_via": "schedule" if schedule else "unscheduled"}


@router.get("/schedules")
def list_schedules(store: StoreDep) -> list[dict]:
    """列出全部已配置调度任务（cron / 启用状态 / 上次运行 / 下次运行）。"""
    return store.list_schedules()


@router.patch("/schedules/{task_key}")
def patch_schedule(task_key: str, patch: SchedulePatch, _: DeveloperDep,
                   store: StoreDep) -> dict:
    """更新调度任务：启停开关 / 更新 cron / 手写下次运行时间。"""
    cfg = _task_or_404(store, task_key)
    if patch.schedule is not None:
        cfg.schedule = patch.schedule or ""
        store.save_task(cfg, task_key=task_key)
        store.update_task_schedule(task_key, schedule_next_run="")
        store.log_event(actor="session-user", project=cfg.project,
                        action="schedule_update",
                        detail={"task_key": task_key, "cron": cfg.schedule})
        updated = store.get_task(task_key)
    else:
        updated = store.update_task_schedule(
            task_key,
            schedule_enabled=patch.enabled,
            schedule_next_run=patch.schedule_next_run)
        if patch.enabled is not None:
            store.log_event(actor="session-user", project=updated.project,
                            action="schedule_toggle",
                            detail={"task_key": task_key, "enabled": patch.enabled})
    schedule = (updated.schedule or "").strip()
    return {
        "task_key": task_key,
        "schedule": schedule,
        "schedule_enabled": bool(updated.schedule_enabled),
        "schedule_next_run": updated.schedule_next_run,
        "scheduled": bool(schedule),
    }