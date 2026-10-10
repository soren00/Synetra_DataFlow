"""连接管理 API：创建/列举/查询连接（不含明文凭证），并能返回秘密供连通性测试。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import AuthContext, require_role
from app.connectivity import check_connection, target_push_idempotency
from app.core.config import get_settings
from app.models.access import Role
from app.models.connection import (
    ConnectionCreate,
    ConnectionDirection,
    ConnectionOut,
    ConnectionType,
)
from app.sources.rest import fetch_sample
from app.store import Store


def get_store() -> Store:
    return Store(get_settings().data_dir)


router = APIRouter(prefix="/connections", tags=["connections"])

StoreDep = Annotated[Store, Depends(get_store)]
DeveloperDep = Annotated[AuthContext, Depends(require_role(Role.DEVELOPER))]


@router.post("", response_model=ConnectionOut, status_code=201)
def create_connection(data: ConnectionCreate, store: StoreDep) -> ConnectionOut:
    """创建连接：秘密经 Vault 加密后落库，返回信息不含任何密文/明文 secrets。"""
    return store.create_connection(data)


@router.get("", response_model=list[ConnectionOut])
def list_connections(
    store: StoreDep,
    project: str | None = None,
    direction: ConnectionDirection | None = None,
) -> list[ConnectionOut]:
    return store.list_connections(project, direction)


@router.get("/{conn_key}", response_model=ConnectionOut)
def get_connection(conn_key: str, store: StoreDep) -> ConnectionOut:
    try:
        return store.get_connection(conn_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="connection not found")


@router.put("/{conn_key}", response_model=ConnectionOut)
def update_connection(conn_key: str, data: ConnectionCreate, store: StoreDep) -> ConnectionOut:
    """编辑连接：更新元数据；secret 留空表示保持原秘密，提供则轮换密码并重加密。"""
    try:
        return store.update_connection(conn_key, data)
    except KeyError:
        raise HTTPException(status_code=404, detail="connection not found")


@router.delete("/{conn_key}")
def delete_connection(conn_key: str, auth: DeveloperDep, store: StoreDep) -> dict:
    """删除连接及其密文。

    被 ETL 任务引用时拒绝删除（409）并列出任务，避免任务指向已不存在的连接；
    连接不存在返回 404；仅 developer 及以上可删除。
    """
    try:
        conn = store.get_connection(conn_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="connection not found")
    used_by = store.tasks_using_connection(conn_key)
    if used_by:
        shown = "、".join((t["name"] or t["task_key"][:8]) for t in used_by[:5])
        more = f" 等 {len(used_by)} 个任务" if len(used_by) > 5 else ""
        raise HTTPException(
            status_code=409,
            detail=f"连接「{conn.name}」仍被 {len(used_by)} 个 ETL 任务引用（{shown}{more}），"
                   "请先删除或改配这些任务",
        )
    store.delete_connection(conn_key)
    store.log_event(actor=auth.username, project=conn.project, action="connection_delete",
                    detail={"conn_key": conn_key, "name": conn.name, "ctype": conn.ctype.value})
    return {"deleted": True, "conn_key": conn_key}


@router.get("/{conn_key}/push-note", response_model=dict)
def connection_push_note(conn_key: str, store: StoreDep) -> dict:
    """目标连接的推送幂等说明（任务 2.6）：API 目标明确提示幂等由端点保证。"""
    conn = _get_conn_or_404(store, conn_key)
    note = target_push_idempotency(conn.ctype)
    return {"conn_key": conn_key, "ctype": conn.ctype.value,
            "push_idempotency_note": note or "该类型由数据库事务保证写入幂等/覆盖语义。"}


class ConnectionTestResult(BaseModel):
    ok: bool
    detail: str


@router.post("/test", response_model=ConnectionTestResult)
def test_connection_config(data: ConnectionCreate, store: StoreDep) -> ConnectionTestResult:
    """未保存即可试连：用表单中的 meta 与秘密直接跑连通性检查，便于「新建连接」时即时验证。"""
    ok, detail = check_connection(data.ctype, data.meta, data.secret)
    return ConnectionTestResult(ok=ok, detail=detail)


@router.post("/{conn_key}/test", response_model=ConnectionTestResult)
def test_connection_endpoint(conn_key: str, store: StoreDep) -> ConnectionTestResult:
    """用存储的 meta 与解密秘密对连接执行连通性测试；不返回秘密本身。"""
    try:
        conn = store.get_connection(conn_key)
        secret = store.get_secret(conn_key) if store.has_secret(conn_key) else None
    except KeyError:
        raise HTTPException(status_code=404, detail="connection not found")
    ok, detail = check_connection(
        conn.ctype, conn.meta, secret.decode() if secret else None
    )
    return ConnectionTestResult(ok=ok, detail=detail)


def _secret_of(store: Store, conn_key: str) -> str | None:
    return store.get_secret(conn_key).decode() if store.has_secret(conn_key) else None


@router.post("/{conn_key}/sniff", response_model=dict)
def sniff_connection(conn_key: str, store: StoreDep) -> dict:
    """CSV 源：读取表结构（列 + 推断类型 + 行数 + 预览）。"""
    conn = _get_conn_or_404(store, conn_key)
    if conn.ctype is not ConnectionType.CSV:
        raise HTTPException(status_code=400, detail="仅 CSV 类型支持读取表结构")
    try:
        from app.sources.csv_reader import read_csv_structure

        return read_csv_structure(conn.meta.get("path", ""))
    except FileNotFoundError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/{conn_key}/sample", response_model=dict)
def sample_connection(conn_key: str, store: StoreDep) -> dict:
    """REST/API 源：发起请求并返回样例数据。"""
    conn = _get_conn_or_404(store, conn_key)
    if conn.ctype is not ConnectionType.API:
        raise HTTPException(status_code=400, detail="仅 API 类型支持样例拉取")
    secret = _secret_of(store, conn_key)
    try:
        params = dict(conn.meta.get("params") or {})
        params.setdefault("limit", conn.meta.get("limit", "5"))
        return fetch_sample(
            conn.meta.get("method", "GET"),
            conn.meta.get("url", ""),
            headers=dict(conn.meta.get("headers") or {}),
            params=params,
            secret=secret,
            auth_scheme=conn.meta.get("auth", "none"),
            limit=int(conn.meta.get("limit", "5")),
        )
    except Exception as e:  # noqa: BLE001 - 转可读原因
        raise HTTPException(status_code=502, detail=f"请求失败: {e}")


def _get_conn_or_404(store: Store, conn_key: str) -> ConnectionOut:
    try:
        return store.get_connection(conn_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="connection not found")