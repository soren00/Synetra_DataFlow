"""认证与授权（任务 7.1/7.2）。

- 身份：请求头 `X-Synetra-User` 携带用户名，角色以 users 表解析（admin/developer/readonly）。
- 授权：`RequireRole` 依赖限定某角色及以上才能调用；`enforce_project` 保证跨项目隔离。
- 明文仅通过 `X-Synetra-User` 注入由网关/SSO 保证；本实现聚焦角色与项目授权语义。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.core.config import get_settings
from app.models.access import Role
from app.store import Store

_ORDER = {Role.ADMIN: 3, Role.DEVELOPER: 2, Role.READONLY: 1}


def _at_least(role: Role, minimum: Role) -> bool:
    return _ORDER[role] >= _ORDER[minimum]


@dataclass
class AuthContext:
    username: str
    role: Role
    project: str | None = None


def hash_credential_token(token: str) -> str:
    return hmac.new(b"synetra-credential", token.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_credential_token() -> tuple[str, str]:
    """返回 (明文 token, token_hash)。明文仅返回一次。"""
    token = secrets.token_urlsafe(32)
    return token, hash_credential_token(token)


def get_store() -> Store:
    return Store(get_settings().data_dir)


StoreDep = Annotated[Store, Depends(get_store)]


def current_user(
    x_synetra_user: Annotated[str | None, Header()] = None,
    store: StoreDep = None,
) -> AuthContext:
    username = (x_synetra_user or "guest").strip() or "guest"
    user = store.get_user(username)
    role = Role(user["role"]) if user else Role.READONLY
    return AuthContext(username=username, role=role)


def require_role(minimum: Role) -> Callable:
    """返回一个依赖，要求当前用户角色 >= minimum。"""

    def dep(auth: Annotated[AuthContext, Depends(current_user)]) -> AuthContext:
        if not _at_least(auth.role, minimum):
            raise HTTPException(status_code=403, detail=f"需 {minimum.value} 及以上角色")
        return auth

    return dep


def enforce_project(auth: AuthContext, project: str) -> None:
    """跨项目隔离：管理员可跨项目；否则仅能访问所属项目名。"""
    if auth.role is Role.ADMIN:
        return
    if auth.project and project and auth.project != project:
        raise HTTPException(status_code=403, detail="跨项目访问被拒绝")


AuthDep = Annotated[AuthContext, Depends(current_user)]
AdminDep = Annotated[AuthContext, Depends(require_role(Role.ADMIN))]
DeveloperDep = Annotated[AuthContext, Depends(require_role(Role.DEVELOPER))]