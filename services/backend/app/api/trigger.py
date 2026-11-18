"""外部触发接口（服务凭据通道，任务 8.2）。

与用户会话票据**相互隔离**：本路由只校验 ``Authorization: Bearer <服务凭据>``
（服务凭据由 ``/api/projects/{project}/credentials`` 签发，绑定单一项目），
既不要求也不接受用户会话票据，因此不参与 main.py 的统一会话兜底。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.auth import StoreDep, hash_credential_token
from app.models.access import TriggerReceipt
from app.schedule import new_run_id
from app.store import Store

router = APIRouter(tags=["trigger"])


def _bearer(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _new_trigger_id() -> str:
    return secrets.token_urlsafe(12)


@router.post("/trigger/{task_key}", status_code=status.HTTP_202_ACCEPTED)
def external_trigger(
    task_key: str,
    store: StoreDep,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """服务凭据触发同步：凭据仅能触发本项目任务，返回触发回执（含 run_id）。"""
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="缺少凭据")
    try:
        task = store.get_task(task_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")
    # 凭据仅能触发本项目任务：token_hash + project 必须匹配且未吊销
    if not store.verify_credential(hash_credential_token(token), task.project):
        raise HTTPException(status_code=401, detail="凭据无效、已吊销或越项目")
    trigger_id = _new_trigger_id()
    via = "external_api"
    fired_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.record_trigger(trigger_id, task_key, task.project, via, actor="external")
    run_id = new_run_id()
    store.start_run(run_id, task_key, task.project, via=via, attempt=1)
    store.append_run_log(run_id, "外部 API 触发，已入队（执行结果走监控查询）", level="info")
    return TriggerReceipt(trigger_id=trigger_id, task_key=task_key,
                          project=task.project, fired_at=fired_at,
                          run_id=run_id).model_dump()
