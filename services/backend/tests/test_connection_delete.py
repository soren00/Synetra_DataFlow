"""连接删除：级联清理密文、被任务引用时拦截、角色门禁与审计留痕。"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient


def _client():
    # ignore_cleanup_errors：Windows 上后台调度线程可能仍持有 sqlite 文件，
    # 目录清理失败与用例断言无关，不应让用例变红。
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    os.environ["SYNETRA_DATA_DIR"] = tmp.name
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import app
    from app.store import Store

    store = Store(get_settings().data_dir)
    store.upsert_user("admin", "admin")
    store.upsert_user("dev", "developer")
    store.upsert_user("reader", "readonly")
    return TestClient(app), store, tmp


def _auth(client: TestClient, user: str):
    client.headers["X-Synetra-User"] = user


def _mk_conn(client, name: str = "订单库", secret: str | None = "pw") -> str:
    r = client.post("/api/connections", json={
        "project": "demo", "name": name, "ctype": "mysql", "direction": "both",
        "meta": {"host": "127.0.0.1", "port": "3306", "db": "orders", "user": "u"},
        "secret": secret,
    })
    assert r.status_code == 201, r.text
    return r.json()["conn_key"]


def _mk_task(client, source_conn_key: str) -> str:
    cfg = {
        "project": "demo", "name": "同步任务",
        "source_conn_key": source_conn_key, "target_conn_key": "tgt",
        "source_object": "orders", "target_object": "orders_dw",
        "mode": "append", "primary_keys": [], "fields": [],
    }
    r = client.post("/api/tasks", json={"project": "demo", "name": "同步任务", "config": cfg})
    assert r.status_code == 200, r.text
    return r.json()["task_key"]


# ---- 正常删除 ----

def test_delete_connection_removes_row_and_secret():
    client, store, tmp = _client()
    try:
        _auth(client, "dev")
        ck = _mk_conn(client)
        assert store.has_secret(ck) is True

        r = client.delete(f"/api/connections/{ck}")
        assert r.status_code == 200, r.text
        assert r.json() == {"deleted": True, "conn_key": ck}

        assert [c["conn_key"] for c in client.get("/api/connections").json()] == []
        assert store.has_secret(ck) is False
        assert client.get(f"/api/connections/{ck}").status_code == 404
    finally:
        client.close(); tmp.cleanup()


def test_delete_connection_without_secret():
    """无密钥连接（如 CSV）同样可删除。"""
    client, store, tmp = _client()
    try:
        _auth(client, "dev")
        ck = _mk_conn(client, name="本地CSV", secret=None)
        assert store.has_secret(ck) is False
        assert client.delete(f"/api/connections/{ck}").status_code == 200
        assert client.get(f"/api/connections/{ck}").status_code == 404
    finally:
        client.close(); tmp.cleanup()


def test_delete_unknown_connection_404():
    client, _, tmp = _client()
    try:
        _auth(client, "dev")
        r = client.delete("/api/connections/not-exist")
        assert r.status_code == 404
        assert "not found" in r.json()["detail"]
    finally:
        client.close(); tmp.cleanup()


# ---- 引用拦截 ----

def test_delete_refused_when_task_references_connection():
    client, store, tmp = _client()
    try:
        _auth(client, "dev")
        ck = _mk_conn(client)
        _mk_task(client, source_conn_key=ck)

        r = client.delete(f"/api/connections/{ck}")
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert "1 个 ETL 任务" in detail
        assert "同步任务" in detail
        assert "请先删除或改配" in detail

        # 连接与密文都还在
        assert client.get(f"/api/connections/{ck}").status_code == 200
        assert store.has_secret(ck) is True
    finally:
        client.close(); tmp.cleanup()


def test_delete_succeeds_after_referencing_task_removed():
    client, _, tmp = _client()
    try:
        _auth(client, "dev")
        ck = _mk_conn(client)
        task_key = _mk_task(client, source_conn_key=ck)

        assert client.delete(f"/api/connections/{ck}").status_code == 409
        assert client.delete(f"/api/tasks/{task_key}").status_code == 200
        assert client.delete(f"/api/connections/{ck}").status_code == 200
    finally:
        client.close(); tmp.cleanup()


def test_delete_checks_target_side_reference_too():
    """目标连接同样纳入引用校验。"""
    client, _, tmp = _client()
    try:
        _auth(client, "dev")
        ck = _mk_conn(client, name="目标库")
        cfg = {
            "project": "demo", "name": "写目标任务",
            "source_conn_key": "src", "target_conn_key": ck,
            "source_object": "o", "target_object": "d",
            "mode": "append", "primary_keys": [], "fields": [],
        }
        assert client.post("/api/tasks", json={"project": "demo", "name": "写目标任务",
                                              "config": cfg}).status_code == 200
        assert client.delete(f"/api/connections/{ck}").status_code == 409
    finally:
        client.close(); tmp.cleanup()


# ---- 角色门禁 ----

def test_delete_requires_developer_role():
    client, _store, tmp = _client()
    try:
        _auth(client, "dev")
        ck = _mk_conn(client)

        _auth(client, "reader")
        r = client.delete(f"/api/connections/{ck}")
        assert r.status_code == 403
        assert client.get(f"/api/connections/{ck}").status_code == 200

        _auth(client, "admin")
        assert client.delete(f"/api/connections/{ck}").status_code == 200
    finally:
        client.close(); tmp.cleanup()


# ---- 审计留痕 ----

def test_delete_writes_audit_event():
    client, store, tmp = _client()
    try:
        _auth(client, "dev")
        ck = _mk_conn(client, name="待删库")
        assert client.delete(f"/api/connections/{ck}").status_code == 200

        events = [e for e in store.list_events(limit=50) if e["action"] == "connection_delete"]
        assert len(events) == 1
        assert events[0]["actor"] == "dev"
        assert events[0]["project"] == "demo"
        assert ck in events[0]["detail_json"]
    finally:
        client.close(); tmp.cleanup()
