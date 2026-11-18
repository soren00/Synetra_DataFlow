"""认证与授权（任务 7.1/7.2）。

- 身份：`POST /api/auth/login` 校验口令后签发 HMAC 会话票据，客户端以
  ``Authorization: Bearer <票据>`` 携带；服务端校验签名、有效期与账号当前状态。
  任何客户端自称的身份（如用户名请求头）一律不予采信。
- 授权：`require_role` 依赖限定某角色及以上才能调用；`enforce_project` 保证跨项目隔离。

票据格式 ``v1.<b64url(payload)>.<b64url(sig)>``，payload 仅含 ``u``（用户名）与
``exp``（过期时间戳）。签名密钥按用户派生（``HMAC(session_secret, password_hash)``），
因此改密/重置密码会令该用户此前签发的全部票据立即失效，且票据本身不含任何口令材料。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.core.config import get_settings
from app.models.access import Role
from app.store import Store

_ORDER = {Role.ADMIN: 3, Role.DEVELOPER: 2, Role.READONLY: 1}

_TOKEN_VERSION = "v1"
_SECRET_FILE = ".session_secret"
_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def _at_least(role: Role, minimum: Role) -> bool:
    return _ORDER[role] >= _ORDER[minimum]


@dataclass
class AuthContext:
    username: str
    role: Role
    project: str | None = None


# ---- 外部触发服务凭据（任务 7.4）----

def hash_credential_token(token: str) -> str:
    return hmac.new(b"synetra-credential", token.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_credential_token() -> tuple[str, str]:
    """返回 (明文 token, token_hash)。明文仅返回一次。"""
    token = secrets.token_urlsafe(32)
    return token, hash_credential_token(token)


# ---- 会话票据 ----

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


_secret_cache: dict[tuple[str, str], bytes] = {}


def _load_or_create_secret(data_dir: Path) -> bytes:
    path = data_dir / _SECRET_FILE
    if path.exists():
        raw = path.read_bytes().strip()
        if raw:
            return raw
    data_dir.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32).encode("ascii")
    path.write_bytes(value)
    try:  # 仅 POSIX 有意义；Windows 下不强制
        path.chmod(0o600)
    except OSError:
        pass
    return value


def session_secret() -> bytes:
    """签名密钥：环境变量/配置优先；缺省在 data_dir 生成并持久化，重启后票据仍有效。"""
    settings = get_settings()
    configured = (settings.session_secret or "").strip()
    if configured:
        return configured.encode("utf-8")
    key = (configured, str(Path(settings.data_dir).resolve()))
    if key not in _secret_cache:
        _secret_cache[key] = _load_or_create_secret(Path(settings.data_dir))
    return _secret_cache[key]


def _signing_key(username: str, store: Store) -> bytes | None:
    """按用户派生签名密钥；用户不存在返回 None。"""
    user = store.get_user(username)
    if not user:
        return None
    material = user.get("password_hash") or f"no-password::{username}"
    return hmac.new(session_secret(), material.encode("utf-8"), hashlib.sha256).digest()


def issue_session_token(username: str, store: Store) -> tuple[str, int]:
    """签发会话票据，返回 (token, expires_at_unix)。用户不存在抛 KeyError。"""
    key = _signing_key(username, store)
    if key is None:
        raise KeyError(username)
    expires_at = int(time.time()) + max(1, get_settings().session_ttl_minutes) * 60
    payload = _b64e(json.dumps({"u": username, "exp": expires_at},
                               separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{_TOKEN_VERSION}.{payload}".encode("ascii")
    signature = _b64e(hmac.new(key, signing_input, hashlib.sha256).digest())
    return f"{_TOKEN_VERSION}.{payload}.{signature}", expires_at


def verify_session_token(token: str, store: Store) -> str | None:
    """校验票据并返回用户名；签名不符、格式非法、已过期或用户不存在均返回 None。"""
    parts = (token or "").split(".")
    if len(parts) != 3:
        return None
    version, payload, signature = parts
    if version != _TOKEN_VERSION:
        return None
    try:
        data = json.loads(_b64d(payload))
        username = str(data["u"])
        expires_at = int(data["exp"])
    except (ValueError, KeyError, TypeError):
        return None
    key = _signing_key(username, store)
    if key is None:
        return None
    expected = _b64e(hmac.new(key, f"{version}.{payload}".encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, signature):
        return None
    if expires_at < int(time.time()):
        return None
    return username


# ---- 依赖 ----

def get_store() -> Store:
    return Store(get_settings().data_dir)


StoreDep = Annotated[Store, Depends(get_store)]


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def current_user(
    authorization: Annotated[str | None, Header()] = None,
    store: StoreDep = None,
) -> AuthContext:
    """仅信任服务端签发的会话票据：未提供/伪造/过期/账号停用一律 401。"""
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="未提供会话票据，请重新登录",
                            headers=_UNAUTHORIZED_HEADERS)
    username = verify_session_token(token, store)
    if not username:
        raise HTTPException(status_code=401, detail="会话票据无效或已过期，请重新登录",
                            headers=_UNAUTHORIZED_HEADERS)
    user = store.get_user(username)
    if not user or not user.get("enabled", 1):
        raise HTTPException(status_code=401, detail="账号不可用，请重新登录",
                            headers=_UNAUTHORIZED_HEADERS)
    return AuthContext(username=username, role=Role(user["role"]))


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
ReadOnlyDep = Annotated[AuthContext, Depends(require_role(Role.READONLY))]
