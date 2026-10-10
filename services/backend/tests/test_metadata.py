"""任务 10.x：表结构字典、字段推断、字段级与表级血缘。"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient


def _client():
    tmp = tempfile.TemporaryDirectory()
    os.environ["SYNETRA_DATA_DIR"] = tmp.name
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import app
    from app.store import Store

    Store(get_settings().data_dir).upsert_user("admin", "admin")
    client = TestClient(app)
    client.headers["X-Synetra-User"] = "admin"
    return client, tmp


CFG = {
    "project": "proj-a", "name": "s", "mode": "upsert", "primary_keys": ["id"],
    "source_conn_key": "src", "target_conn_key": "tgt",
    "source_object": "orders", "target_object": "orders_dw",
    "incremental": {"field": "updated_at", "watermark_field": "max"},
    "fields": [
        {"source": "id", "target": "id", "kind": "direct"},
        {"source": "name", "target": "name_upper", "kind": "expression", "expression": "upper(name)"},
        {"source": "", "target": "note", "kind": "constant", "constant_value": "x"},
    ],
}


# ---- 10.1 表结构字典 ----

def test_sync_and_query_table_schema():
    client, tmp = _client()
    try:
        r = client.post("/api/metadata/connections/c1/tables/orders/sync",
                        json={"columns": [{"name": "id", "ctype": "int", "primary": True},
                                          {"name": "name", "ctype": "varchar", "nullable": False}]})
        assert r.status_code == 200 and r.json()["columns_saved"] == 2
        tables = client.get("/api/metadata/connections/c1/tables").json()
        assert tables == [{"table": "orders", "column_count": 2}]
        cols = client.get("/api/metadata/connections/c1/tables/orders").json()
        ids = {c["column_name"]: c for c in cols}
        assert ids["id"]["is_primary"] == 1
    finally:
        tmp.cleanup()


# ---- 10.2 从样例推断字段 ----

def test_infer_columns_from_rows():
    client, tmp = _client()
    try:
        r = client.post("/api/metadata/infer",
                        json={"conn_key": "c2", "table": "api_orders",
                              "rows": [{"id": 1, "name": "a", "active": True},
                                       {"id": 2, "name": "b", "active": False}]})
        body = r.json()
        by_name = {c["name"]: c for c in body["columns"]}
        assert by_name["id"]["ctype"] == "number"
        assert by_name["active"]["ctype"] == "boolean"
        assert body["columns_saved"] == 3
    finally:
        tmp.cleanup()


# ---- 10.3 字段级血缘 ----

def test_field_lineage_derived_on_save_and_query():
    client, tmp = _client()
    try:
        key = client.post("/api/tasks", json={"project": "proj-a", "name": "s", "config": CFG}).json()["task_key"]
        # 保存即自动推导字段级血缘
        summary = client.get("/api/metadata/lineage", params={"task": key}).json()["summary"]
        assert summary["field_level_available"] is True
        edges = summary["edges"]
        targets = {e["target"] for e in edges}
        # 有源字段才具字段级血缘；常量(note 无源)不产生血缘边
        assert targets == {"orders_dw.id", "orders_dw.name_upper"}
        # 显式 derive 幂等
        r = client.post(f"/api/metadata/tasks/{key}/lineage/derive")
        assert r.json()["field_level_available"] is True
    finally:
        tmp.cleanup()


# ---- 10.4 手写路径表级血缘，字段级不可用 ----

def test_handwritten_table_level_lineage():
    client, tmp = _client()
    try:
        key = client.post("/api/tasks", json={"project": "proj-a", "name": "s", "config": CFG}).json()["task_key"]
        r = client.post(f"/api/metadata/tasks/{key}/lineage/handwritten")
        body = r.json()
        assert body["field_level_available"] is False  # 手写路径不得伪造字段级血缘
        assert body["edges"] == [{"source": "orders", "target": "orders_dw", "field": None, "kind": "table"}]
        # 血缘视图据此标识字段级不可用
        summary = client.get("/api/metadata/lineage", params={"task": key}).json()["summary"]
        assert summary["field_level_available"] is False
    finally:
        tmp.cleanup()


# ---- 血缘查询 ----

def test_lineage_search_by_source():
    client, tmp = _client()
    try:
        client.post("/api/tasks", json={"project": "proj-a", "name": "s", "config": CFG})
        res = client.get("/api/metadata/lineage", params={"source": "orders.id"}).json()["edges"]
        assert any(e["target"] == "orders_dw.id" for e in res)
    finally:
        tmp.cleanup()