"""任务 11.1 / 14.x：etl_sdk 关系库抽载 + CSV/REST extractor + API/对象存储/Hive/ClickHouse loader。"""

from __future__ import annotations

import csv

import pytest

from app.etl_sdk.extractors import CsvExtractor, ExtractError, RestExtractor, pull
from app.etl_sdk.loaders import (
    ApiLoader,
    ClickHouseLoader,
    HiveLoader,
    LoadError,
    ObjectStorageLoader,
    load,
)
from app.etl_sdk.relational import (
    ConnectorError,
    MySQLConnector,
    OracleConnector,
    SqlServerConnector,
    extract,
    get_connector,
)
from app.models.connection import ConnectionType

# ---- 11.1 关系库：方言 SQL / 连接器注册 / 因子驱动缺失的清晰报错 ----

def test_relational_connector_registry_and_missing_driver():
    meta = {"host": "x", "port": "5432", "user": "u", "db": "db"}
    # 关系库类型返回对应连接器实例（连接延迟到 connect 阶段）
    from app.etl_sdk.relational import PostgreSQLConnector

    assert isinstance(get_connector(ConnectionType.POSTGRESQL, meta, "pw"),
                      PostgreSQLConnector)
    assert isinstance(get_connector(ConnectionType.ORACLE, {"host": "o"}, "pw"),
                      OracleConnector)
    assert isinstance(get_connector(ConnectionType.SQLSERVER, {"host": "s"}, "pw"),
                      SqlServerConnector)
    assert isinstance(get_connector(ConnectionType.MYSQL, {"host": "m", "port": "3306"}, "pw"),
                      MySQLConnector)
    # 非关系库类型 → 清晰原因（非裸 ImportError）
    with pytest.raises(ConnectorError):
        get_connector(ConnectionType.CSV, meta, None)


def test_extract_missing_driver_clear_reason():
    with pytest.raises(ConnectorError, match="psycopg"):
        extract(ConnectionType.POSTGRESQL, {"host": "x"}, None, "SELECT 1")


def test_mysql_load_append_and_upsert_sql(monkeypatch):
    # 以假连接验证 MySQL 方言：INSERT 与 ON DUPLICATE KEY UPDATE
    class FakeCur:
        def __init__(self):
            self.sql = []

        def execute(self, q, args=None):
            self.sql.append((q, args or []))

    fake = FakeCur()

    class FakeConn:
        def cursor(self):
            return fake

        def commit(self):
            pass

    def fake_connect(*a, **kw):
        return FakeConn()

    monkeypatch.setattr("pymysql.connect", fake_connect)
    meta = {"host": "127.0.0.1", "port": "3306", "user": "u", "db": "db"}
    conn = get_connector(ConnectionType.MYSQL, meta, "pw")

    conn.load("t", [{"id": 1, "name": "a"}], mode="append", primary_keys=["id"])
    q, args = [(q, a) for q, a in fake.sql if q.startswith("INSERT INTO")][-1]
    assert q.startswith("INSERT INTO") and args == [1, "a"]

    conn.load("t", [{"id": 1, "name": "b"}], mode="upsert", primary_keys=["id"])
    q, _ = [(q, a) for q, a in fake.sql if q.startswith("INSERT INTO")][-1]
    assert "ON DUPLICATE KEY UPDATE" in q and "name=VALUES(name)" in q


# ---- 自动建表：目标表缺失时按字段映射/样例值自动 CREATE ----

def test_mysql_autocreate_table_sql(monkeypatch):
    class FakeCur:
        def __init__(self):
            self.sql = []
        def execute(self, q, args=None):
            self.sql.append((q, args or []))
            if q.startswith("SELECT 1 FROM"):
                raise RuntimeError("Table doesn't exist")

    cur = FakeCur()

    class FakeConn:
        def cursor(self):
            return cur
        def commit(self):
            pass

    monkeypatch.setattr("pymysql.connect", lambda *a, **kw: FakeConn())
    conn = get_connector(ConnectionType.MYSQL, {"host": "h", "db": "db"}, "pw")
    conn.load("orders2", [{"id": 1, "name": "a", "amount": 9.9}], mode="append",
              primary_keys=["id"],
              columns=[{"target": "id", "kind": "direct", "cast_to": "int"},
                       {"target": "name", "kind": "direct", "cast_to": "str"},
                       {"target": "amount", "kind": "direct", "cast_to": "float"}])
    ddl = next(q for q, _ in cur.sql if q.startswith("CREATE TABLE"))
    assert "CREATE TABLE IF NOT EXISTS `orders2`" in ddl
    assert "`id` BIGINT" in ddl and "`name` VARCHAR(255)" in ddl and "`amount` DOUBLE" in ddl
    assert "PRIMARY KEY (`id`)" in ddl
    assert any(q.startswith("INSERT INTO") for q, _ in cur.sql)


def test_mysql_autocreate_infers_cols_from_sample(monkeypatch):
    """未传 columns 时按样例行值推断列类型。"""
    class FakeCur:
        def __init__(self):
            self.sql = []
        def execute(self, q, args=None):
            self.sql.append((q, args or []))
            if q.startswith("SELECT 1 FROM"):
                raise RuntimeError("missing")

    cur = FakeCur()

    class FakeConn:
        def cursor(self):
            return cur
        def commit(self):
            pass

    monkeypatch.setattr("pymysql.connect", lambda *a, **kw: FakeConn())
    conn = get_connector(ConnectionType.MYSQL, {"host": "h", "db": "db"}, "pw")
    conn.load("t2", [{"id": 1, "note": "x"}], mode="append", primary_keys=["id"])
    ddl = next(q for q, _ in cur.sql if q.startswith("CREATE TABLE"))
    assert "`id` BIGINT" in ddl and "`note` VARCHAR(255)" in ddl
    assert "PRIMARY KEY (`id`)" in ddl


def test_autocreate_rejects_bad_identifier(monkeypatch):
    class FakeCur:
        def execute(self, q, args=None):
            raise RuntimeError("missing")

    class FakeConn:
        def cursor(self):
            return FakeCur()
        def commit(self):
            pass

    monkeypatch.setattr("pymysql.connect", lambda *a, **kw: FakeConn())
    conn = get_connector(ConnectionType.MYSQL, {"host": "h", "db": "db"}, "pw")
    with pytest.raises(ConnectorError, match="非法"):
        conn.load("orders2; DROP TABLE x", [{"id": 1}], mode="append", primary_keys=[])


def test_probe_sql_dialects():
    assert "TOP 1" in SqlServerConnector({"host": "s"}, None)._probe_sql("t")
    assert "ROWNUM" in OracleConnector({"host": "o"}, None)._probe_sql("t")
    assert "`t`" in MySQLConnector({"host": "m"}, None)._q("t")


# ---- 14.1 CSV extractor ----

def test_csv_extractor_reads_rows(tmp_path):
    p = tmp_path / "c.csv"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "name"])
        w.writeheader()
        w.writerows([{"id": "1", "name": "a"}, {"id": "2", "name": "b"}])
    res = CsvExtractor({"path": str(p)}).pull()
    assert res.total == 2 and res.rows[0]["name"] == "a"


def test_csv_extractor_missing_file():
    with pytest.raises(Exception) as ei:
        CsvExtractor({"path": "/nope.csv"}).pull()
    assert "CSV" in str(ei.value)


# ---- 14.2 REST extractor（以本地 httpx 假端点验证分页/参数） ----

def test_rest_extractor_pagination(monkeypatch):
    calls = []

    def fake_request_json(method, url, params=None, secret=None, auth_scheme="none", timeout=30):
        calls.append(dict(params))
        page = int(params["page"])
        if page == 1:
            return {"items": [{"id": page}, {"id": page + 1}]}
        return {"items": []}

    monkeypatch.setattr("app.etl_sdk.extractors.request_json", fake_request_json)
    res = RestExtractor({"url": "http://api/i", "page_param": "page", "size_param": "size"},
                        None, pages=5).pull()
    assert res.total == 2
    assert calls[0]["page"] == "1"  # 分页从 1 开始


def test_rest_extractor_error():
    with pytest.raises(ExtractError):
        RestExtractor({"url": "http://127.0.0.1:1/nope"}, None).pull()


# ---- 14.3 API loader ----

def test_api_loader_push_success(monkeypatch):
    received = {}

    def fake_request_json(method, url, json_body=None, secret=None, auth_scheme="none", timeout=30,
                          params=None, body=None):
        received.update(method=method, url=url, payload=json_body)
        return {"ok": True}

    monkeypatch.setattr("app.etl_sdk.loaders.request_json", fake_request_json)
    res = ApiLoader({"url": "http://ingest/hook", "method": "POST"}, None).push(
        [{"id": 1}, {"id": 2}])
    assert res.rows == 2 and "幂等" in res.note
    assert received["method"] == "POST"


def test_api_loader_retry_then_fail(monkeypatch):
    n = {"calls": 0}

    def boom(method, url, json_body=None, secret=None, auth_scheme="none", timeout=30, **kw):
        n["calls"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr("app.etl_sdk.loaders.request_json", boom)
    with pytest.raises(LoadError):
        ApiLoader({"url": "http://ingest"}, None, retries=2).push([{"id": 1}])
    assert n["calls"] == 3  # 1 次初试 + 2 次重试


# ---- 14.4 对象存储 / Hive / ClickHouse loader：驱动缺失给清晰原因 ----

def test_object_storage_missing_driver():
    with pytest.raises(LoadError, match="boto3"):
        ObjectStorageLoader({"endpoint": "http://m", "bucket": "b"}, "s").push([{"a": 1}], "k.csv")


def test_hive_missing_driver():
    with pytest.raises(LoadError):
        HiveLoader({"host": "x"}, None).push("t", [])


def test_clickhouse_missing_driver():
    with pytest.raises(LoadError):
        ClickHouseLoader({"host": "x"}, None).push("t", [{"a": 1}])


def test_load_dispatch_unknown_target():
    with pytest.raises(ExtractError, match="不支持"):
        pull(ConnectionType.MYSQL, {"path": "x"})  # MYSQL 非 extractor 类型
    with pytest.raises(LoadError):
        load("csv", {"url": "x"}, None, [], table="t")  # csv 不是 loader 类型