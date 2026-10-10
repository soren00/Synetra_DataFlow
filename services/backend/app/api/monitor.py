"""监控、运行历史、指标、对账与告警 API（任务 9.x）。

- GET  /runs               运行历史（服务端分页 page/page_size + 状态/项目/任务筛选）
- GET  /runs/{run_id}      单次运行详情
- GET  /runs/{run_id}/logs 实时日志
- GET  /runs/{run_id}/metrics 单次指标
- GET  /metrics            指标统计
- POST /reconcile          数据对账（手动触发）
- GET  /reconciliations    对账记录
- POST /alerts             创建阈值告警规则
- GET  /alerts             规则列表
- POST /alerts/{id}/check  按最新指标求值触发
- DELETE /alerts/{id}      删除规则
- GET  /alerts/events      告警事件
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.alerting import send_mail
from app.auth import require_role
from app.core.config import get_settings
from app.models.access import Role
from app.monitor import AlertEvaluator, Reconciler
from app.store import Store


class ReconcileRequest(BaseModel):
    task_key: str
    project: str
    source_rows: list[dict] = Field(default_factory=list)
    target_rows: list[dict] = Field(default_factory=list)


class MetricRequest(BaseModel):
    cpu_percent: float = 0
    memory_mb: float = 0
    latency_ms: float = 0
    duration_s: float = 0
    data_rows: int = 0


class AlertRuleCreate(BaseModel):
    task_key: str
    metric: str
    op: str
    threshold: float
    notify_to: str = ""
    enabled: bool = True


class AlertCheckRequest(BaseModel):
    value: float


router = APIRouter()


def get_store() -> Store:
    return Store(get_settings().data_dir)


StoreDep = Annotated[Store, Depends(get_store)]
DeveloperDep = Annotated[object, Depends(require_role(Role.DEVELOPER))]


def _require_run(store: Store, run_id: str) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return run


# ---- 9.1 运行历史 ----

RUN_STATUSES = ("running", "success", "failed", "retrying", "unscheduled")
RUN_PAGE_SIZES = (10, 20, 50)
RUN_DEFAULT_PAGE_SIZE = 20


def _normalize_page_size(value: int) -> int:
    """只允许 10/20/50 三档，其余值就近归一到最近的合法档位。"""
    if value in RUN_PAGE_SIZES:
        return value
    return min(RUN_PAGE_SIZES, key=lambda s: (abs(s - value), s))


@router.get("/runs")
def list_runs(store: StoreDep, project: str | None = None, task_key: str | None = None,
              status: str | None = None, page: int = 1, page_size: int = RUN_DEFAULT_PAGE_SIZE) -> dict:
    """运行历史：分页 + 筛选均由服务端裁决（非法状态 422，页大小归一到 10/20/50）。"""
    if status and status not in RUN_STATUSES:
        raise HTTPException(status_code=422, detail=f"非法状态: {status}")
    size = _normalize_page_size(page_size)
    total = store.run_totals(project, task_key, status)["count"]
    pages = max(1, -(-total // size))
    current = min(max(1, page), pages)
    runs = store.list_runs(project, task_key=task_key, limit=size,
                           offset=(current - 1) * size, status=status)
    stats = store.run_totals(project, task_key)
    return {
        "runs": runs,
        "total": total,
        "page": current,
        "page_size": size,
        "pages": pages,
        "rows_sum": stats["rows_sum"],
        "status_counts": store.run_status_counts(project, task_key),
        "allowed_statuses": list(RUN_STATUSES),
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str, store: StoreDep) -> dict:
    return _require_run(store, run_id)


# ---- 9.2 实时日志 ----

@router.get("/runs/{run_id}/logs")
def run_logs(run_id: str, store: StoreDep, since: int = 0) -> dict:
    _require_run(store, run_id)
    logs = store.list_run_logs(run_id)
    return {"run_id": run_id, "logs": logs[since:], "cursor": len(logs)}


# ---- 9.3 指标 ----

@router.post("/runs/{run_id}/metrics")
def record_metrics(run_id: str, req: MetricRequest, store: StoreDep) -> dict:
    _require_run(store, run_id)
    store.record_metric(run_id, cpu_percent=req.cpu_percent, memory_mb=req.memory_mb,
                        latency_ms=req.latency_ms, duration_s=req.duration_s,
                        data_rows=req.data_rows)
    return {"run_id": run_id, "recorded": True}


@router.get("/metrics")
def metrics_summary(store: StoreDep, task_key: str | None = None, limit: int = 100) -> list:
    return store.metric_summary(task_key=task_key, limit=limit)


# ---- 9.4 数据对账 ----

@router.post("/reconcile")
def reconcile(req: ReconcileRequest, _: DeveloperDep, store: StoreDep) -> dict:
    rec = Reconciler().reconcile(req.task_key, req.project,
                                 source_rows=req.source_rows, target_rows=req.target_rows)
    rid = store.record_reconciliation(
        req.project, req.task_key, source_rows=rec["source_rows"],
        target_rows=rec["target_rows"], checksum_match=rec["checksum_match"],
        details=rec["details"])
    store.log_event(actor="session-user", project=req.project, action="reconcile",
                    detail={"task_key": req.task_key, "id": rid,
                            "consistent": rec["consistent"]})
    return {"id": rid, **rec}


@router.get("/reconciliations")
def list_reconciliations(store: StoreDep, project: str | None = None, limit: int = 100) -> list:
    return store.list_reconciliations(project, limit=limit)


# ---- 9.5 阈值告警 ----

@router.post("/alerts")
def create_alert_rule(req: AlertRuleCreate, _: DeveloperDep, store: StoreDep) -> dict:
    if req.op not in AlertEvaluator.OPS:
        raise HTTPException(status_code=422, detail=f"非法比较符: {req.op}")
    rid = store.save_alert_rule("", req.task_key, req.metric, req.op, req.threshold,
                                req.notify_to, req.enabled)
    return {"id": rid, "task_key": req.task_key, "metric": req.metric,
            "op": req.op, "threshold": req.threshold}


@router.get("/alerts")
def list_alerts(store: StoreDep) -> list:
    return store.list_alert_rules()


@router.post("/alerts/{rule_id}/check")
def check_alert_rule(rule_id: int, req: AlertCheckRequest, store: StoreDep) -> dict:
    rules = [r for r in store.list_alert_rules() if r["id"] == rule_id]
    if not rules:
        raise HTTPException(status_code=404, detail="alert rule not found")
    evaluator = AlertEvaluator(store)
    event = evaluator.evaluate_and_notify(rules[0], req.value, send=send_mail)
    if event is None:
        return {"rule_id": rule_id, "triggered": False, "value": req.value}
    return {"rule_id": rule_id, "triggered": True, "event": event}


@router.delete("/alerts/{rule_id}")
def delete_alert_rule(rule_id: int, _: DeveloperDep, store: StoreDep) -> dict:
    if not store.delete_alert_rule(rule_id):
        raise HTTPException(status_code=404, detail="alert rule not found")
    return {"deleted": True, "rule_id": rule_id}


@router.get("/alerts/events")
def list_alert_events(store: StoreDep, limit: int = 100) -> list:
    return store.list_alert_events(limit=limit)