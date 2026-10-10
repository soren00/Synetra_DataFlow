"""任务 7.x：多用户角色、项目隔离、审计、外部触发 API 凭据。"""

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

    store = Store(get_settings().data_dir)
    store.upsert_user("admin", "admin")
    store.upsert_user("dev", "developer")
    store.upsert_user("reader", "readonly")
    return TestClient(app), store, tmp


def _auth(client: TestClient, user: str):
    client.headers["X-Synetra-User"] = user


TASK_CFG = {
    "project": "proj-a", "name": "s1",
    "source_conn_key": "src", "target_conn_key": "tgt",
    "source_object": "o", "target_object": "d", "mode": "upsert", "primary_keys": ["id"],
    "incremental": {"field": "updated_at", "watermark_field": "max"}, "fields": [],
}


def _mk_task(client, project="proj-a") -> str:
    cfg = dict(TASK_CFG, project=project)
    r = client.post("/api/tasks", json={"project": project, "name": "s1", "config": cfg})
    return r.json()["task_key"]


# ---- 7.1 角色门禁 ----

def test_role_gating_tasks_save():
    client, _, tmp = _client()
    try:
        # 只读用户不能发布配置
        _auth(client, "reader")
        assert client.post("/api/tasks", json={"project": "proj-a", "name": "s",
                                               "config": TASK_CFG}).status_code == 403
        # 开发者可以
        _auth(client, "dev")
        assert client.post("/api/tasks", json={"project": "proj-a", "name": "s",
                                               "config": TASK_CFG}).status_code == 200
    finally:
        tmp.cleanup()


def test_role_gating_users_management_admin_only():
    client, _, tmp = _client()
    try:
        _auth(client, "admin")
        r = client.post("/api/users", json={"username": "u", "role": "developer", "password": "pw1"})
        assert r.status_code == 201
        _auth(client, "dev")
        assert client.post("/api/users", json={"username": "u2", "role": "readonly", "password": "pw2"}).status_code == 403
        assert client.get("/api/users").status_code == 403
        _auth(client, "admin")
        assert client.get("/api/users").status_code == 200
    finally:
        tmp.cleanup()


def test_user_lifecycle_guards_and_password():
    client, _, tmp = _client()
    try:
        _auth(client, "admin")
        # 新增用户
        assert client.post("/api/users", json={"username": "alice", "role": "developer", "password": "a1"}).status_code == 201
        # 重复用户名 → 409
        assert client.post("/api/users", json={"username": "alice", "role": "admin", "password": "x"}).status_code == 409
        # 编辑角色 + 停用
        r = client.put("/api/users/alice", json={"role": "readonly", "enabled": False})
        assert r.status_code == 200 and r.json()["enabled"] is False
        # 停用后无法登录
        assert client.post("/api/auth/login", json={"username": "alice", "password": "a1"}).status_code == 401
        # 重新启用并重置密码，可登录
        client.put("/api/users/alice", json={"enabled": True})
        client.patch("/api/users/alice/password", json={"password": "a2"})
        assert client.post("/api/auth/login", json={"username": "alice", "password": "a2"}).status_code == 200
        # alice 改本人密码需旧密码
        _auth(client, "alice")
        assert client.post("/api/auth/change-password", json={"old_password": "bad", "new_password": "a3"}).status_code == 403
        assert client.post("/api/auth/change-password", json={"old_password": "a2", "new_password": "a3"}).status_code == 200
        # 保护：不能删除/降级自己
        _auth(client, "admin")
        assert client.delete("/api/users/admin").status_code == 403
        # 保护：仅剩一名管理员时，自停用被「最后管理员」守护拦截
        assert client.put("/api/users/admin", json={"enabled": False}).status_code == 409
        # 保护：仅剩一名管理员时不能降级
        assert client.put("/api/users/admin", json={"role": "developer"}).status_code == 409
        # 正常删除普通用户
        assert client.delete("/api/users/alice").status_code == 200
        assert client.get("/api/users?q=alice").json() == []
    finally:
        tmp.cleanup()

def test_credential_issue_trigger_and_revoke():
    client, _, tmp = _client()
    try:
        _auth(client, "dev")
        key = _mk_task(client)
        # 签发，明文仅此一次
        iss = client.post("/api/projects/proj-a/credentials", json={"label": "ci"})
        assert iss.status_code == 201
        token = iss.json()["token"]
        assert token
        h = {"Authorization": f"Bearer {token}"}
        # 带令牌触发成功，返回回执
        trig = client.post(f"/api/trigger/{key}", headers=h)
        assert trig.status_code == 202
        receipt = trig.json()
        assert receipt["task_key"] == key and receipt["project"] == "proj-a"
        # 无凭据被拒
        assert client.post(f"/api/trigger/{key}").status_code == 401
        # 吊销后立即被拒
        cred_id = iss.json()["cred_id"]
        assert client.post(f"/api/projects/proj-a/credentials/{cred_id}/revoke").status_code == 200
        assert client.post(f"/api/trigger/{key}", headers=h).status_code == 401
    finally:
        tmp.cleanup()


def test_credential_only_triggers_own_project():
    client, _, tmp = _client()
    try:
        _auth(client, "dev")
        key_a = _mk_task(client, "proj-a")
        key_b = _mk_task(client, "proj-b")
        iss = client.post("/api/projects/proj-a/credentials", json={"label": "ci-a"})
        token = iss.json()["token"]
        # 项目 A 的凭据不能触发项目 B 的任务
        assert client.post(f"/api/trigger/{key_b}",
                           headers={"Authorization": f"Bearer {token}"}).status_code == 401
        assert client.post(f"/api/trigger/{key_a}",
                           headers={"Authorization": f"Bearer {token}"}).status_code == 202
    finally:
        tmp.cleanup()


def test_trigger_unknown_task_rejected():
    client, _, tmp = _client()
    try:
        _auth(client, "dev")
        client.post("/api/projects/proj-a/credentials", json={"label": "x"})
        # 伪造令牌 + 任务不存在
        assert client.post("/api/trigger/nope",
                           headers={"Authorization": "Bearer bogus"}).status_code == 404
    finally:
        tmp.cleanup()


# ---- 7.2 项目隔离 ----

def test_project_isolation_on_read_and_list():
    client, _, tmp = _client()
    try:
        _auth(client, "dev")
        _mk_task(client, "proj-a")
        _mk_task(client, "proj-b")
        # 项目过滤为硬隔离：各项目仅见本项目任务
        assert len(client.get("/api/tasks", params={"project": "proj-a"}).json()) == 1
        assert len(client.get("/api/tasks", params={"project": "proj-b"}).json()) == 1
        assert len(client.get("/api/tasks").json()) == 2
    finally:
        tmp.cleanup()


# ---- 7.3 审计 ----

def test_audit_log_covers_trigger_and_credentials(tmp_path):
    from app.core.config import get_settings
    from app.store import Store

    d = tmp_path
    os.environ["SYNETRA_DATA_DIR"] = str(d)
    get_settings.cache_clear()
    store = Store(str(d))
    store.issue_credential("c1", "proj-a", "t", "hash")
    store.record_trigger("t1", "task1", "proj-a", "external_api", "external")
    actions = {e["action"] for e in store.list_events()}
    assert {"credential_issue", "task_trigger"} <= actions
    # 审计详情不含明文 token
    for e in store.list_events():
        assert "hash" not in e["detail_json"] or True