"""元数据表结构字典与血缘 API（任务 10）。

- POST /api/metadata/connections/{ck}/tables/{table}/sync   同步表结构（10.1；列由连接器/发现提供）
- POST /api/metadata/infer                                  从样例推断字段结构并入库（10.2）
- GET  /api/metadata/connections/{ck}/tables                列出表
- GET  /api/metadata/connections/{ck}/tables/{table}        字段字典
- POST /api/metadata/tasks/{key}/lineage/derive             向导/LLM 路径字段级血缘推导（10.3）
- POST /api/metadata/tasks/{key}/lineage/handwritten        手写路径表级血缘（10.4，字段级不可用）
- GET  /api/metadata/lineage                                血缘查询（source/target/task）
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth import require_role
from app.core.config import get_settings
from app.metadata import Catalog
from app.metadata.catalog import (
    field_lineage_from_config,
    table_lineage_from_task,
)
from app.metadata.infer import infer_columns_from_rows
from app.models.access import Role
from app.store import Store


class SyncRequest(BaseModel):
    schema_name: str = ""
    columns: list[dict] = Field(default_factory=list)  # [{name, cache_type,...}]


class InferRequest(BaseModel):
    conn_key: str
    table: str
    rows: list[dict] = Field(default_factory=list)


router = APIRouter()


def get_catalog() -> Catalog:
    return Catalog(get_settings().data_dir)


def get_store() -> Store:
    return Store(get_settings().data_dir)


CatalogDep = Annotated[Catalog, Depends(get_catalog)]
StoreDep = Annotated[Store, Depends(get_store)]
DeveloperDep = Annotated[object, Depends(require_role(Role.DEVELOPER))]


def _norm(col: dict) -> dict:
    return {
        "name": col.get("name") or col.get("source") or col.get("column_name") or "?",
        "ctype": col.get("ctype") or col.get("type") or "TEXT",
        "nullable": col.get("nullable", True),
        "primary": col.get("primary", False),
    }


@router.post("/metadata/connections/{conn_key}/tables/{table}/sync")
def sync_table(conn_key: str, table: str, req: SyncRequest, _: DeveloperDep, cat: CatalogDep) -> dict:
    cols = _normalize_columns(req.columns)
    n = cat.save_table_schema(conn_key, table, cols, schema_name=req.schema_name)
    return {"conn_key": conn_key, "table": table, "columns_saved": n}


@router.post("/metadata/infer")
def infer_and_save(req: InferRequest, _: DeveloperDep, cat: CatalogDep) -> dict:
    columns = infer_columns_from_rows(req.rows)
    n = cat.save_table_schema(req.conn_key, req.table, columns)
    return {"conn_key": req.conn_key, "table": req.table,
            "columns": columns, "columns_saved": n}


@router.get("/metadata/connections/{conn_key}/tables")
def list_tables(conn_key: str, cat: CatalogDep, schema_name: str = "") -> list:
    return cat.list_tables(conn_key, schema_name)


@router.get("/metadata/connections/{conn_key}/tables/{table}")
def describe_table(conn_key: str, table: str, cat: CatalogDep, schema_name: str = "") -> list:
    return cat.describe_table(conn_key, table, schema_name)


# ---- 血缘 ----

@router.post("/metadata/tasks/{task_key}/lineage/derive")
def derive_lineage(task_key: str, _: DeveloperDep, store: StoreDep, cat: CatalogDep) -> dict:
    cfg = store.get_task(task_key).model_dump()
    edges = field_lineage_from_config(cfg)
    cat.store_lineage(task_key, edges)
    return cat.table_lineage_summary(task_key)


@router.post("/metadata/tasks/{task_key}/lineage/handwritten")
def handwritten_lineage(task_key: str, _: DeveloperDep, store: StoreDep, cat: CatalogDep) -> dict:
    cfg = store.get_task(task_key).model_dump()
    edges = table_lineage_from_task(cfg)
    cat.store_lineage(task_key, edges)
    return cat.table_lineage_summary(task_key)


@router.get("/metadata/lineage")
def search_lineage(cat: CatalogDep, task: str | None = None,
                   source: str | None = None, target: str | None = None) -> dict:
    if task:
        return {"task": task, "summary": cat.table_lineage_summary(task)}
    return {"edges": cat.search_lineage(source, target)}


def _normalize_columns(columns: list[dict]) -> list[dict]:
    return [_norm(c) for c in columns]