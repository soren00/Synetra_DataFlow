"""多用户角色、项目隔离、凭据签发与外部触发（任务 7.x）。

- POST   /api/users                       (admin) 新增用户（初始密码、角色、显示名）
- GET    /api/users?q=&role=              (admin) 列表（搜索/角色筛选）
- PUT    /api/users/{username}            (admin) 编辑角色/显示名/启用停用
- DELETE /api/users/{username}            (admin) 删除（不能删自己/最后一名管理员）
- PATCH  /api/users/{username}/password   (admin) 重置密码
- POST   /api/auth/change-password        (登录用户) 修改本人密码（校验旧密码）
- POST   /api/projects/{p}/credentials    (developer+) 签发外部触发凭据（明文仅一次）
- GET    /api/projects/{p}/credentials    (developer+) 列出
- POST   /api/projects/{p}/credentials/{id}/revoke  (developer+) 吊销

外部触发（Bearer 服务凭据）在 `app/api/trigger.py`，与会话票据通道隔离。
"""

from __future__ import annotations

import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import (
    AuthContext,
    enforce_project,
    issue_credential_token,
    require_role,
)
from app.core.config import get_settings
from app.models.access import Role, ServiceCredential
from app.store import Store


class UserCreate(BaseModel):
    username: str
    role: Role
    password: str
    display_name: str = ""


class UserUpdate(BaseModel):
    role: Role | None = None
    display_name: str | None = None
    enabled: bool | None = None


class PasswordReset(BaseModel):
    password: str


class CredentialIssue(BaseModel):
    label: str = ""


router = APIRouter()


def get_store() -> Store:
    return Store(get_settings().data_dir)


StoreDep = Annotated[Store, Depends(get_store)]
AdminDep = Annotated[AuthContext, Depends(require_role(Role.ADMIN))]
DeveloperDepInstance = Annotated[AuthContext, Depends(require_role(Role.DEVELOPER))]


def new_id() -> str:
    return secrets.token_urlsafe(12)


# ---- 7.1 用户与角色 ----

@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(req: UserCreate, _: AdminDep, store: StoreDep) -> dict:
    try:
        u = store.create_user(req.username, req.role.value, req.password, req.display_name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {k: u[k] for k in ("username", "role", "display_name", "enabled")}


@router.get("/users")
def list_users(q: str | None = None, role: str | None = None, _: AdminDep = None, store: StoreDep = None) -> list:
    data = store.list_users(q, role)
    for u in data:
        u["enabled"] = bool(u.get("enabled", 1))
    return data


@router.put("/users/{username}")
def update_user(username: str, body: UserUpdate, auth: AdminDep, store: StoreDep) -> dict:
    try:
        target = store.get_user(username)
    except KeyError:
        raise HTTPException(status_code=404, detail="user not found")
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    # 自助停用先于「最后一名管理员」保护判定：自己的账号本就不该被停用
    if username == auth.username and body.enabled is False:
        raise HTTPException(status_code=403, detail="不能停用自己的账号")
    _guard_last_admin(store, target, role=body.role, enabled=body.enabled)
    u = store.update_user(username, role=body.role.value if body.role else None,
                          display_name=body.display_name, enabled=body.enabled)
    return {k: (bool(u[k]) if k == "enabled" else u[k])
            for k in ("username", "role", "display_name", "enabled")}


@router.delete("/users/{username}")
def delete_user(username: str, auth: AdminDep, store: StoreDep) -> dict:
    if username == auth.username:
        raise HTTPException(status_code=403, detail="不能删除当前登录账号")
    target = store.get_user(username)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    _guard_last_admin(store, target)
    if not store.delete_user(username):
        raise HTTPException(status_code=404, detail="user not found")
    return {"username": username, "deleted": True}


@router.patch("/users/{username}/password")
def reset_password(username: str, body: PasswordReset, _: AdminDep, store: StoreDep) -> dict:
    if not body.password:
        raise HTTPException(status_code=422, detail="密码不能为空")
    if not store.set_user_password(username, body.password):
        raise HTTPException(status_code=404, detail="user not found")
    return {"username": username, "password_reset": True}


def _guard_last_admin(store: Store, target: dict, *, role: Role | None = None,
                      enabled: bool | None = None) -> None:
    """保护最后一名启用管理员：无法降级/删除/停用，避免系统失去超级管理员。"""
    if target["role"] != "admin" or not target.get("enabled", 1):
        return
    still_admin = (role is None or role == Role.ADMIN) and enabled is not False
    if still_admin:
        return
    if store.count_enabled_admins() <= 1:
        raise HTTPException(status_code=409, detail="系统至少需要保留一名启用的管理员")


# ---- 7.4 凭据签发/吊销 ----

@router.post("/projects/{project}/credentials", status_code=status.HTTP_201_CREATED)
def issue_credential(project: str, req: CredentialIssue,
                     auth: DeveloperDepInstance, store: StoreDep) -> dict:
    enforce_project(auth, project)
    token, token_hash = issue_credential_token()
    cred_id = new_id()
    store.issue_credential(cred_id, project, req.label or f"cred-{cred_id[:6]}", token_hash)
    # 明文 token 仅在签发响应返回一次；后续仅存哈希
    return {"cred_id": cred_id, "project": project, "label": req.label,
            "token": token, "revoked": False}


@router.get("/projects/{project}/credentials")
def list_credentials(project: str, auth: DeveloperDepInstance, store: StoreDep) -> list:
    enforce_project(auth, project)
    creds = store.list_credentials(project)
    return [ServiceCredential(cred_id=c["cred_id"], project=c["project"], label=c["label"],
                              created_at=c["created_at"], revoked=bool(c["revoked"]),
                              token_hash="").model_dump(exclude={"token_hash"}) for c in creds]


@router.post("/projects/{project}/credentials/{cred_id}/revoke")
def revoke_credential(project: str, cred_id: str, auth: DeveloperDepInstance, store: StoreDep) -> dict:
    enforce_project(auth, project)
    if not store.revoke_credential(cred_id):
        raise HTTPException(status_code=404, detail="credential not found")
    return {"cred_id": cred_id, "revoked": True}


# ---- 审计日志查询（仅管理员） ----

@router.get("/audit/events")
def list_audit_events(_: AdminDep, store: StoreDep,
                      project: str = "", action: str = "", limit: int = 100) -> list[dict]:
    """按项目/操作类型过滤的审计日志（倒序）；响应中不含任何明文凭证。"""
    rows = store.list_events(project=project or None, action=action or None, limit=limit)
    for row in rows:
        raw = row.pop("detail_json", "") or "{}"
        try:
            row["detail"] = json.loads(raw)
        except json.JSONDecodeError:
            row["detail"] = {"raw": raw}
    return rows


# 外部触发（POST /api/trigger/{task_key}）走独立的服务凭据通道，见 app/api/trigger.py