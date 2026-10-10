"""认证与会话、系统资源监控（任务 7.x / 9.6）。

- 登录：POST /api/auth/login 校验用户名+密码（PBKDF2），成功返回身份；鉴权仍由角色模型兜底。
- 系统资源：GET /api/monitor/system 读取主机 CPU/内存/磁盘占用（读取 /proc，无需系统依赖）。
"""

from __future__ import annotations

import os
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import AuthContext, StoreDep, require_role
from app.models.access import Role

# 注意：统一前缀 /api 由 main.py 注册时追加，此处不重复定义。
router = APIRouter()

AuthorizedDep = Annotated[AuthContext, Depends(require_role(Role.READONLY))]


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/auth/login")
def login(req: LoginRequest, store: StoreDep) -> dict:
    user = store.verify_credentials(req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"username": user["username"], "role": user["role"]}


@router.post("/auth/change-password")
def change_password(req: ChangePasswordRequest, auth: AuthorizedDep,
                    store: StoreDep) -> dict:
    """任意登录用户修改本人密码：需校验旧密码，避免越权改密。"""
    if not req.new_password:
        raise HTTPException(status_code=422, detail="新密码不能为空")
    if store.verify_credentials(auth.username, req.old_password) is None:
        raise HTTPException(status_code=403, detail="旧密码不正确")
    store.set_user_password(auth.username, req.new_password)
    return {"username": auth.username, "updated": True}


# ---- 系统资源监控（/proc 实现，无第三方依赖） ----

def _cpu_percent() -> int:
    def _read() -> list[int]:
        with open("/proc/stat", encoding="utf-8") as f:
            parts = f.readline().split()[1:]
        return [int(x) for x in parts]

    a = _read()
    time.sleep(0.2)
    b = _read()
    idle_a, idle_b = a[3] + a[4], b[3] + b[4]
    total_a, total_b = sum(a), sum(b)
    diff_total = total_b - total_a
    return round(100 * (1 - (idle_b - idle_a) / diff_total)) if diff_total else 0


def _memory() -> dict:
    fields: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, rest = line.split(":", 1)
                fields[k] = int(rest.strip().split()[0])  # kB
    except OSError:
        return {"total_mb": 0, "used_mb": 0, "percent": 0}
    total = fields.get("MemTotal", 0)
    avail = fields.get("MemAvailable", total)
    used = max(0, total - avail)
    pct = round(100 * used / total) if total else 0
    return {"total_mb": total // 1024, "used_mb": used // 1024, "percent": pct}


def _disk(path: str = "/") -> dict:
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        used = st.f_blocks * st.f_frsize - st.f_bfree * st.f_frsize
        return {
            "total_gb": round(total / 1e9, 1),
            "used_gb": round(used / 1e9, 1),
            "percent": round(100 * used / total) if total else 0,
        }
    except OSError:
        return {"total_gb": 0, "used_gb": 0, "percent": 0}


@router.get("/monitor/system")
def system_resource() -> dict:
    return {
        "cpu_percent": _cpu_percent(),
        "cpu_cores": os.cpu_count() or 0,
        "memory": _memory(),
        "disk": _disk(),
        "hostname": os.uname().nodename if hasattr(os, "uname") else "",
        "ts": time.strftime("%H:%M:%S"),
    }