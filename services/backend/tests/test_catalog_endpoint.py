"""只读查询工作台：库表/视图实时枚举的单元测试与接口测试。

- 方言分支、kind/q 过滤、截断与 snippet 生成用猴子补丁隔离驱动，无需真实数据库；
- 接口层覆盖 200 形状、403 越权、400 非关系库、404 连接不存在与审计留痕；
- 真实 MySQL 枚举用例仅在凭据可用时运行，否则跳过。
"""

from __future__ import annotations

import json
import socket

import pytest
from fastapi.testclient import TestClient

from app.models.connection import ConnectionType
from app.queries import catalog

MYSQL_META = {"host": "127.0.0.1", "port": "3306", "user": "synetra", "db": "synetra_test"}


# ---------------------------------------------------------------- 纯函数与方言


def test_quote_ident_and_snippet_dialects():
    assert catalog.build_snippet(ConnectionType.MYSQL, "ds1", "orders") == \
        "SELECT * FROM `ds1`.`orders` LIMIT 100;"
    assert catalog.build_snippet(ConnectionType.POSTGRESQL, "public", "orders") == \
        'SELECT * FROM "public"."orders" LIMIT 100;'
    assert catalog.build_snippet(ConnectionType.MYSQL, "", "orders") == \
        "SELECT * FROM `orders` LIMIT 100;"


def test_snippet_escapes_special_identifiers():
    # 含空格 / 大写 / 引号字符的标识符按方言转义，生成语句可直接执行
    assert catalog.build_snippet(ConnectionType.MYSQL, "ds1", "Order Items") == \
        "SELECT * FROM `ds1`.`Order Items` LIMIT 100;"
    assert catalog.build_snippet(ConnectionType.POSTGRESQL, "public", 'we"ird') == \
        'SELECT * FROM "public"."we""ird" LIMIT 100;'
    assert catalog.build_snippet(ConnectionType.MYSQL, "d`b", "t") == \
        "SELECT * FROM `d``b`.`t` LIMIT 100;"


def test_system_schema_filter():
    assert catalog._is_system_schema("pg_catalog")
    assert catalog._is_system_schema("information_schema")
    assert catalog._is_system_schema("pg_toast_temp_1")
    assert not catalog._is_system_schema("public")
    assert not catalog._is_system_schema("analytics")


# ---------------------------------------------------------------- 枚举行为分支


def test_mysql_enumeration_maps_kinds(monkeypatch):
    rows = [("ds1", "dim_area", "BASE TABLE"), ("ds1", "v_area", "VIEW")]

    def fake_fetch(meta, secret, schema, default_db):
        return "ds1", ["ds1"], rows

    monkeypatch.setattr(catalog, "_fetch_mysql", fake_fetch)
    res = catalog.list_objects(ConnectionType.MYSQL, {"db": "ds1"}, "pw")

    assert res.default_schema == "ds1"
    assert res.schemas == ["ds1"]
    assert [i["kind"] for i in res.items] == ["table", "view"]
    assert res.items[0]["snippet"] == "SELECT * FROM `ds1`.`dim_area` LIMIT 100;"
    assert res.truncated is False


def test_mysql_enumeration_filters_kind_and_keyword(monkeypatch):
    rows = [("ds1", "dim_area", "BASE TABLE"), ("ds1", "dim_city", "BASE TABLE"),
            ("ds1", "v_city", "VIEW")]
    monkeypatch.setattr(catalog, "_fetch_mysql",
                        lambda meta, secret, schema, default_db: ("ds1", ["ds1"], rows))

    only_views = catalog.list_objects(ConnectionType.MYSQL, {"db": "ds1"}, None, kind="view")
    assert [i["name"] for i in only_views.items] == ["v_city"]

    only_tables = catalog.list_objects(ConnectionType.MYSQL, {"db": "ds1"}, None, kind="table")
    assert [i["name"] for i in only_tables.items] == ["dim_area", "dim_city"]

    filtered = catalog.list_objects(ConnectionType.MYSQL, {"db": "ds1"}, None, q="CITY")
    assert [i["name"] for i in filtered.items] == ["dim_city", "v_city"]


def test_postgres_enumeration_excludes_system_schemas(monkeypatch):
    captured: dict = {}

    def fake_fetch(meta, secret, schema):
        captured["schema"] = schema
        return "public", ["public", "analytics"], [("public", "orders", "BASE TABLE"),
                                                   ("public", "v_orders", "VIEW")]

    monkeypatch.setattr(catalog, "_fetch_postgres", fake_fetch)
    res = catalog.list_objects(ConnectionType.POSTGRESQL, {"db": "pgdb"}, "pw")

    assert res.schemas == ["public", "analytics"]
    assert "pg_catalog" not in res.schemas and "information_schema" not in res.schemas
    assert [i["kind"] for i in res.items] == ["table", "view"]
    assert res.items[0]["snippet"] == 'SELECT * FROM "public"."orders" LIMIT 100;'

    catalog.list_objects(ConnectionType.POSTGRESQL, {"db": "pgdb"}, "pw", schema="analytics")
    assert captured["schema"] == "analytics"


def test_enumeration_truncates_at_limit(monkeypatch):
    rows = [("ds1", f"t{i}", "BASE TABLE") for i in range(catalog.MAX_OBJECTS + 1)]
    monkeypatch.setattr(catalog, "_fetch_mysql",
                        lambda meta, secret, schema, default_db: ("ds1", ["ds1"], rows))

    res = catalog.list_objects(ConnectionType.MYSQL, {"db": "ds1"}, None)
    assert res.truncated is True
    assert len(res.items) == catalog.MAX_OBJECTS


def test_enumeration_rejects_non_relational_types():
    for ctype in (ConnectionType.API, ConnectionType.CSV):
        with pytest.raises(catalog.CatalogNotSupported):
            catalog.list_objects(ctype, {}, None)


def test_list_schemas_reuses_enumeration(monkeypatch):
    monkeypatch.setattr(catalog, "_fetch_mysql",
                        lambda meta, secret, schema, default_db: ("ds1", ["ds1", "ds2"], []))
    assert catalog.list_schemas(ConnectionType.MYSQL, {"db": "ds1"}, None) == ["ds1", "ds2"]


# ---------------------------------------------------------------- 接口层

def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("SYNETRA_DATA_DIR", str(tmp_path))

    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    return TestClient(app)


def _make_conn(client: TestClient, name: str, ctype: str, meta: dict) -> str:
    r = client.post("/api/connections", json={
        "project": "proj-x", "name": name, "ctype": ctype,
        "direction": "source", "meta": meta, "secret": "synetra_pass",
    })
    assert r.status_code == 201, r.text
    return r.json()["conn_key"]


def test_catalog_endpoint_returns_navigation_payload(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    ck = _make_conn(client, "src", "mysql", MYSQL_META)

    def fake_list_objects(ctype, meta, secret, schema=None, kind="all", q=""):
        return catalog.CatalogResult(
            default_schema="synetra_test",
            schemas=["synetra_test"],
            items=[{"schema": "synetra_test", "name": "orders", "kind": "table",
                    "snippet": "SELECT * FROM `synetra_test`.`orders` LIMIT 100;"}],
        )

    monkeypatch.setattr("app.api.catalog.list_objects", fake_list_objects)
    r = client.get(f"/api/connections/{ck}/catalog", params={"project": "proj-x"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ctype"] == "mysql"
    assert body["default_schema"] == "synetra_test"
    assert body["schemas"] == ["synetra_test"]
    assert body["truncated"] is False
    assert body["items"][0]["name"] == "orders"
    assert "LIMIT 100" in body["items"][0]["snippet"]


def test_catalog_endpoint_writes_audit_event(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    ck = _make_conn(client, "src", "mysql", MYSQL_META)
    monkeypatch.setattr("app.api.catalog.list_objects",
                        lambda *a, **kw: catalog.CatalogResult(default_schema="synetra_test"))

    assert client.get(f"/api/connections/{ck}/catalog",
                      params={"project": "proj-x"}).status_code == 200

    from app.store import Store

    events = Store(str(tmp_path)).list_events(project="proj-x")
    details = [json.loads(e["detail_json"]) for e in events if e["action"] == "query"]
    assert any(d.get("op") == "catalog" for d in details)


def test_catalog_endpoint_rejects_cross_project(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    ck = _make_conn(client, "src", "mysql", MYSQL_META)

    r = client.get(f"/api/connections/{ck}/catalog", params={"project": "other-project"})
    assert r.status_code == 403
    assert "items" not in r.json()


def test_catalog_endpoint_rejects_non_relational_connection(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    ck = _make_conn(client, "api-src", "api", {"url": "http://127.0.0.1:9/x"})

    r = client.get(f"/api/connections/{ck}/catalog", params={"project": "proj-x"})
    assert r.status_code == 400
    assert "关系库" in r.json()["detail"]


def test_catalog_endpoint_unknown_connection(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/connections/nope/catalog", params={"project": "proj-x"})
    assert r.status_code == 404


def test_catalog_endpoint_validates_kind(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    ck = _make_conn(client, "src", "mysql", MYSQL_META)
    r = client.get(f"/api/connections/{ck}/catalog",
                   params={"project": "proj-x", "kind": "materialized"})
    assert r.status_code == 422


# ------------------------------------------------- 查询契约回归（不依赖真实库）

def test_query_contract_unchanged(monkeypatch, tmp_path):
    """重设计仅改界面与导航：`POST /{ck}/query` 的请求/响应契约必须一字不变。"""
    client = _client(monkeypatch, tmp_path)
    ck = _make_conn(client, "src", "mysql", MYSQL_META)

    from app.queries import QueryResult

    monkeypatch.setattr("app.api.query.run_select", lambda *a, **kw: QueryResult(
        columns=["id", "name"], rows=[[1, "a"]], count=1, truncated=False))

    r = client.post(f"/api/connections/{ck}/query", json={
        "project": "proj-x", "sql": "SELECT id, name FROM t", "limit": 10, "offset": 0,
    })
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"columns", "rows", "count", "truncated", "limit", "offset"}

    props = client.get("/openapi.json").json()["components"]["schemas"]["QueryRequest"]
    assert set(props["properties"]) == {"project", "sql", "limit", "offset"}
    assert props["properties"]["limit"]["default"] == 200
    assert props["properties"]["offset"]["default"] == 0


# ---------------------------------------------------------------- 真实库（可选）

def _mysql_reachable_with_credentials() -> bool:
    try:
        import pymysql

        with socket.create_connection(("127.0.0.1", 3306), timeout=2):
            pass
        conn = pymysql.connect(host="127.0.0.1", port=3306, user=MYSQL_META["user"],
                              password="synetra_pass", database=MYSQL_META["db"],
                              connect_timeout=2)
        conn.close()
        return True
    except Exception:  # noqa: BLE001 - 环境不可用时跳过
        return False


needs_mysql = pytest.mark.skipif(
    not _mysql_reachable_with_credentials(),
    reason="本机未运行 MySQL 或测试凭据不可用",
)


@needs_mysql
def test_catalog_endpoint_live_mysql(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    ck = _make_conn(client, "src", "mysql", MYSQL_META)

    r = client.get(f"/api/connections/{ck}/catalog",
                   params={"project": "proj-x", "q": "orders"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default_schema"] == MYSQL_META["db"]
    assert any(i["name"] == "orders" and i["kind"] == "table" for i in body["items"])

    snippet = next(i["snippet"] for i in body["items"] if i["name"] == "orders")
    executed = client.post(f"/api/connections/{ck}/query", json={
        "project": "proj-x", "sql": snippet, "limit": 5,
    })
    assert executed.status_code == 200, executed.text
