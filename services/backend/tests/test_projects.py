"""项目管理（ETL 任务归属）：创建/清单/播种/删除约束与角色门禁。"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

TASK_CFG = {
    "project": "proj-a", "name": "s1",
    "source_conn_key": "src", "target_conn_key": "tgt",
    "source_object": "o", "target_object": "d", "mode": "upsert", "primary_keys": ["id"],
    "incremental": {"field": "updated_at", "watermark_field": "max"}, "fields": [],
}


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


def _mk_task(client, project: str) -> str:
    cfg = dict(TASK_CFG, project=project)
    r = client.post("/api/tasks", json={"project": project, "name": "s1", "config": cfg})
    assert r.status_code == 200, r.text
    return r.json()["task_key"]


# ---- 创建与重名 ----

def test_create_project_and_duplicate_conflict():
    client, store, tmp = _client()
    try:
        _auth(client, "dev")
        r = client.post("/api/projects", json={"name": "orders"})
        assert r.status_code == 201
        assert r.json()["name"] == "orders"

        dup = client.post("/api/projects", json={"name": "orders"})
        assert dup.status_code == 409
        assert "已存在" in dup.json()["detail"]

        # 名称仅空白同样被拒（不落库）
        assert client.post("/api/projects", json={"name": "   "}).status_code == 409
        assert store.get_project("orders") is not None
    finally:
        tmp.cleanup()


def test_create_project_requires_developer_or_above():
    client, _, tmp = _client()
    try:
        _auth(client, "reader")
        assert client.post("/api/projects", json={"name": "p1"}).status_code == 403
        _auth(client, "admin")
        assert client.post("/api/projects", json={"name": "p1"}).status_code == 201
    finally:
        tmp.cleanup()


# ---- 清单与任务计数 ----

def test_list_projects_reports_task_count():
    client, _, tmp = _client()
    try:
        _auth(client, "dev")
        client.post("/api/projects", json={"name": "orders"})
        client.post("/api/projects", json={"name": "empty-one"})
        _mk_task(client, "orders")

        listing = client.get("/api/projects").json()
        counts = {p["name"]: p["task_count"] for p in listing}
        assert counts["orders"] == 1
        assert counts["empty-one"] == 0
    finally:
        tmp.cleanup()


# ---- 存量归属播种 ----

def test_seed_existing_task_projects_is_idempotent():
    client, store, tmp = _client()
    try:
        # 直接落库既有任务（模拟升级前的数据），验证播种后仍可见
        from app.models.task_config import TaskConfig

        store.save_task(TaskConfig(project="legacy", name="t1",
                                   source_conn_key="s", target_conn_key="g"))

        listing = client.get("/api/projects").json()
        names = {p["name"] for p in listing}
        assert "legacy" in names

        # 幂等：重复请求不产生重复条目
        again = client.get("/api/projects").json()
        assert len([p for p in again if p["name"] == "legacy"]) == 1
        assert len(again) == len(listing)
    finally:
        tmp.cleanup()


def test_seed_maps_blank_project_to_default():
    client, store, tmp = _client()
    try:
        from app.models.task_config import TaskConfig

        store.save_task(TaskConfig(project="", name="t-blank",
                                   source_conn_key="s", target_conn_key="g"))
        names = {p["name"] for p in client.get("/api/projects").json()}
        assert "default" in names
    finally:
        tmp.cleanup()


# ---- 删除约束 ----

def test_delete_empty_project():
    client, _, tmp = _client()
    try:
        _auth(client, "dev")
        client.post("/api/projects", json={"name": "temp"})
        assert client.delete("/api/projects/temp").status_code == 200
        assert {p["name"] for p in client.get("/api/projects").json()} == set()
    finally:
        tmp.cleanup()


def test_delete_non_empty_project_is_rejected():
    client, store, tmp = _client()
    try:
        _auth(client, "dev")
        client.post("/api/projects", json={"name": "orders"})
        task_key = _mk_task(client, "orders")

        r = client.delete("/api/projects/orders")
        assert r.status_code == 409
        assert "仍有 1 个任务" in r.json()["detail"]
        # 项目与任务都还在（不级联删除）
        assert store.get_project("orders") is not None
        assert client.get(f"/api/tasks/{task_key}").status_code == 200
    finally:
        tmp.cleanup()


def test_delete_missing_project_returns_404():
    client, _, tmp = _client()
    try:
        _auth(client, "dev")
        assert client.delete("/api/projects/nope").status_code == 404
    finally:
        tmp.cleanup()


# ---- Store 层：项目仅管理任务 ----

def test_store_rejects_duplicate_name():
    _, store, tmp = _client()
    try:
        store.create_project("a")
        try:
            store.create_project("a")
        except ValueError as e:
            assert "已存在" in str(e)
        else:  # pragma: no cover - 明确失败信息
            raise AssertionError("重名项目未被拒绝")
    finally:
        tmp.cleanup()
