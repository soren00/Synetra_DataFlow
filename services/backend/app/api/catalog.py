"""只读查询工作台：关系库库表/视图实时枚举路由。

`GET /api/connections/{conn_key}/catalog`：
- 校验连接存在性与项目权限（与只读查询同一口径），失败即 403/404；
- 实时读取目标库系统目录，返回 schema 清单与表/视图清单（含预填 SQL）；
- 审计留痕：action=query，detail.op=catalog（与查询留痕同一通道）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import get_settings
from app.models.connection import ConnectionOut, ConnectionType
from app.queries import CatalogNotSupported, CatalogResult, list_objects
from app.store import Store

router = APIRouter(prefix="/connections")


def get_store() -> Store:
    return Store(get_settings().data_dir)


StoreDep = Annotated[Store, Depends(get_store)]


def _get_conn(store: Store, conn_key: str) -> ConnectionOut:
    try:
        return store.get_connection(conn_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="connection not found")


def _secret(store: Store, conn_key: str) -> str | None:
    return store.get_secret(conn_key).decode() if store.has_secret(conn_key) else None


@router.get("/{conn_key}/catalog")
def catalog_connection(
    conn_key: str,
    store: StoreDep,
    project: str = Query(..., min_length=1),
    schema: str = "",
    kind: str = Query("all", pattern="^(all|table|view)$"),
    q: str = "",
) -> dict:
    """枚举关系库连接的 schema 与表/视图（只读；不写入本地元数据字典）。"""
    conn = _get_conn(store, conn_key)
    if conn.project != project:
        raise HTTPException(status_code=403, detail="无该项目访问权限")
    if conn.ctype not in (ConnectionType.MYSQL, ConnectionType.POSTGRESQL):
        raise HTTPException(status_code=400, detail="仅关系库连接支持库表枚举")

    store.log_event(actor="session-user", project=project, action="query", detail={
        "conn_key": conn.conn_key, "op": "catalog", "schema": schema, "kind": kind})

    try:
        res: CatalogResult = list_objects(
            conn.ctype, conn.meta, _secret(store, conn_key),
            schema=schema or None, kind=kind, q=q,
        )
    except CatalogNotSupported as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 - 底层驱动异常转可读
        raise HTTPException(status_code=502, detail=f"库表枚举失败: {e}")

    return {
        "conn_key": conn.conn_key,
        "ctype": conn.ctype.value,
        "default_schema": res.default_schema,
        "schemas": res.schemas,
        "items": res.items,
        "truncated": res.truncated,
    }
