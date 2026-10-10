"""任务 3.1 / 3.2 端到端：只读查询（限行/分页/拒写/审计）与项目权限校验。

依赖本机运行 MySQL（synetra_test 库，含 orders 25 行）；不可达则跳过。
"""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

MYSQL_META = {"host": "127.0.0.1", "port": "3306", "user": "synetra", "db": "synetra_test"}


def _mysql_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 3306), timeout=2):
            return True
    except OSError:
        return False


needs_mysql = pytest.mark.skipif(not _mysql_reachable(), reason="本机未运行 MySQL")


def _client() -> tuple[TestClient, Path]:
    import os

    tmp = tempfile.TemporaryDirectory()
    # 覆盖 data_dir 到临时目录
    os.environ["SYNETRA_DATA_DIR"] = tmp.name

    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    client = TestClient(app)
    return client, tmp


@needs_mysql
def test_select_pagination_and_limit():
    client, tmp = _client()
    try:
        ck = _make_conn(client, "src", MYSQL_META)
        r = client.post(f"/api/connections/{ck}/query", json={
            "project": "proj-x", "sql": "SELECT id, name, qty FROM orders ORDER BY id",
            "limit": 10, "offset": 0,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["rows"]) == 10 and body["truncated"] is True
        assert body["columns"] == ["id", "name", "qty"]
        r2 = client.post(f"/api/connections/{ck}/query", json={
            "project": "proj-x", "sql": "SELECT id FROM orders ORDER BY id",
            "limit": 1000, "offset": 20,
        })
        assert r2.status_code == 200
        assert len(r2.json()["rows"]) == 5  # 25 - 20
    finally:
        client.close(); tmp.cleanup()


@needs_mysql
def test_write_rejected():
    client, tmp = _client()
    try:
        ck = _make_conn(client, "src", MYSQL_META)
        r = client.post(f"/api/connections/{ck}/query", json={
            "project": "proj-x", "sql": "DELETE FROM orders WHERE id=1", "limit": 10,
        })
        assert r.status_code == 422
        assert "SELECT" in r.json()["detail"] or "副作用" in r.json()["detail"]
    finally:
        client.close(); tmp.cleanup()


@needs_mysql
def test_cross_project_rejected_and_audit():
    client, tmp = _client()
    try:
        ck = _make_conn(client, "src", MYSQL_META)
        # 传错误 project -> 403
        r = client.post(f"/api/connections/{ck}/query", json={
            "project": "other-project", "sql": "SELECT id FROM orders LIMIT 1",
        })
        assert r.status_code == 403
    finally:
        client.close(); tmp.cleanup()


def _make_conn(client: TestClient, name: str, meta: dict) -> str:
    r = client.post("/api/connections", json={
        "project": "proj-x", "name": name, "ctype": "mysql",
        "direction": "source", "meta": meta, "secret": "synetra_pass",
    })
    assert r.status_code == 201, r.text
    return r.json()["conn_key"]