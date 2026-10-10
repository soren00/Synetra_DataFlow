"""只读查询与 API 调试路由（任务 3.1 / 3.2）、调试结果送向导预填。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.models.connection import ConnectionOut, ConnectionType
from app.queries import QueryNotImplemented, QueryRejected, QueryResult, run_select
from app.sources.rest import fetch_sample
from app.store import Store


class QueryRequest(BaseModel):
    project: str
    sql: str
    limit: int = Field(default=200, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class DebugRequest(BaseModel):
    project: str
    method: str = "GET"
    url: str | None = None  # 为空则用连接默认地址
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    limit: int = Field(default=5, ge=1, le=100)


router = APIRouter(prefix="/connections")


def get_store() -> Store:
    return Store(get_settings().data_dir)


StoreDep = Annotated[Store, Depends(get_store)]


def _authorized(store: Store, conn: ConnectionOut, project: str) -> None:
    """最小项目权限校验：请求方 project 必须等于连接所属 project（完整 RBAC 见任务 7）。"""
    if conn.project != project:
        raise HTTPException(status_code=403, detail="无该项目访问权限")
    store.log_event(actor="session-user", project=project, action="query", detail={
        "conn_key": conn.conn_key, "action": "query", "ok": True})


def _get_conn(store: Store, conn_key: str) -> ConnectionOut:
    try:
        return store.get_connection(conn_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="connection not found")


def _secret(store: Store, conn_key: str) -> str | None:
    return store.get_secret(conn_key).decode() if store.has_secret(conn_key) else None


@router.post("/{conn_key}/query")
def query_connection(conn_key: str, req: QueryRequest, store: StoreDep) -> dict:
    """只读查询：守卫校验 + 行数上限/超时 + 分页预览。写/多语句被拒绝。"""
    conn = _get_conn(store, conn_key)
    _authorized(store, conn, req.project)
    if conn.ctype not in (ConnectionType.MYSQL, ConnectionType.POSTGRESQL):
        raise HTTPException(status_code=400, detail="仅关系库连接支持只读查询")
    try:
        res: QueryResult = run_select(
            conn.ctype, conn.meta, _secret(store, conn_key), req.sql,
            limit=req.limit, offset=req.offset,
        )
    except QueryRejected as e:
        raise HTTPException(status_code=422, detail=str(e))
    except QueryNotImplemented as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:  # noqa: BLE001 - 底层驱动异常转可读
        raise HTTPException(status_code=502, detail=f"查询失败: {e}")
    return {
        "columns": res.columns,
        "rows": res.rows[:req.limit],
        "count": res.count,
        "truncated": res.truncated,
        "limit": req.limit,
        "offset": req.offset,
    }


@router.post("/{conn_key}/debug")
def debug_connection(conn_key: str, req: DebugRequest, store: StoreDep) -> dict:
    """REST/API 请求调试：基于连接配置，可临时覆盖方法/头/参数，返回样例+状态。"""
    conn = _get_conn(store, conn_key)
    _authorized(store, conn, req.project)
    if conn.ctype is not ConnectionType.API:
        raise HTTPException(status_code=400, detail="仅 API 连接支持调试")
    url = req.url or conn.meta.get("url", "")
    try:
        result = fetch_sample(
            req.method, url,
            headers=req.headers,
            params=req.params,
            secret=_secret(store, conn_key),
            auth_scheme=conn.meta.get("auth", "none"),
            limit=req.limit,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"调试请求失败: {e}")
    return {
        "ok": result.get("ok"),
        "status": result.get("status"),
        "sample": result.get("sample", []),
        "feed_for_wizard": True,  # 样例字段可直接预填字段选择向导（见任务 3.2/4.1）
    }