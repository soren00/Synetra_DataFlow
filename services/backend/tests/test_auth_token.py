"""会话票据认证与路由鉴权兜底的专项用例（变更 fix-test-report-defects）。

本文件不使用 conftest 的默认管理员身份夹具：每个用例都走真实的
``Authorization: Bearer`` 验签链路，以覆盖匿名拒绝、伪造票据、过期、
账号停用与改密失效等场景。
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

TASK_CFG = {
    "project": "proj-a", "name": "s1",
    "source_conn_key": "src", "target_conn_key": "tgt",
    "source_object": "o", "target_object": "d", "mode": "upsert", "primary_keys": ["id"],
    "incremental": {"field": "updated_at", "watermark_field": "max"}, "fields": [],
}


@pytest.fixture(autouse=True)
def _real_auth_only():
    """移除 conftest 注入的默认管理员身份，强制走真实票据校验。"""
    from app.auth import current_user
    from app.main import app

    app.dependency_overrides.pop(current_user, None)
    yield


@pytest.fixture()
def env():
    tmp = tempfile.TemporaryDirectory()
    os.environ["SYNETRA_DATA_DIR"] = tmp.name
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import app
    from app.store import Store

    store = Store(get_settings().data_dir)
    store.upsert_user("admin", "admin", password="admin")
    store.upsert_user("dev", "developer", password="dev")
    store.upsert_user("reader", "readonly", password="reader")
    yield TestClient(app), store
    tmp.cleanup()


def _login(client: TestClient, username: str, password: str) -> dict:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- 票据签发与校验（1.2）----

def test_issue_and_verify_roundtrip(env):
    _, store = env
    from app.auth import issue_session_token, verify_session_token

    token, expires_at = issue_session_token("admin", store)
    assert token.startswith("v1.") and token.count(".") == 2
    assert expires_at > int(time.time())
    assert verify_session_token(token, store) == "admin"


def test_tampered_payload_rejected(env):
    _, store = env
    from app.auth import issue_session_token, verify_session_token

    token, _ = issue_session_token("admin", store)
    version, payload, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["u"] = "dev"
    forged = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode()).decode().rstrip("=")
    assert verify_session_token(f"{version}.{forged}.{sig}", store) is None


def test_wrong_signature_rejected(env):
    _, store = env
    from app.auth import issue_session_token, verify_session_token

    token, _ = issue_session_token("admin", store)
    version, payload, _sig = token.split(".")
    assert verify_session_token(f"{version}.{payload}.AAAA", store) is None


def test_expired_token_rejected(env, monkeypatch):
    _, store = env
    from app.auth import issue_session_token, verify_session_token

    token, _ = issue_session_token("admin", store)
    now = time.time()
    monkeypatch.setattr("app.auth.time.time", lambda: now + 10 ** 6)
    assert verify_session_token(token, store) is None


def test_unknown_version_rejected(env):
    _, store = env
    from app.auth import issue_session_token, verify_session_token

    token, _ = issue_session_token("admin", store)
    assert verify_session_token("v2" + token[2:], store) is None


def test_garbage_token_rejected(env):
    _, store = env
    from app.auth import verify_session_token

    for bad in ("", "v1", "v1.x.y.z", "v1.bm90LWpzb24.AAAA"):
        assert verify_session_token(bad, store) is None


def test_secret_file_persisted(env):
    from app.auth import session_secret

    first = session_secret()
    assert len(first) >= 32
    assert session_secret() == first


# ---- 路由鉴权兜底（1.3 / 1.6）----

def test_anonymous_rejected_on_business_routes(env):
    client, _ = env
    for path in ("/api/connections", "/api/tasks", "/api/runs", "/api/mdm/models",
                 "/api/projects", "/api/monitor/system"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/connections", json={
        "project": "p", "name": "c", "ctype": "mysql", "meta": {}}).status_code == 401


def test_self_declared_header_not_trusted(env):
    client, _ = env
    assert client.get("/api/connections",
                      headers={"X-Synetra-User": "admin"}).status_code == 401
    assert client.get("/api/users",
                      headers={"X-Synetra-User": "admin"}).status_code == 401


def test_public_routes_open(env):
    client, _ = env
    assert client.get("/health").status_code == 200
    assert client.post("/api/auth/login",
                       json={"username": "admin", "password": "nope"}).status_code == 401


def test_login_token_grants_permissions(env):
    client, _ = env
    body = _login(client, "admin", "admin")
    assert {"username", "role", "token", "expires_at"} <= set(body)
    headers = _bearer(body["token"])
    assert client.get("/api/connections", headers=headers).status_code == 200
    assert client.get("/api/users", headers=headers).status_code == 200


def test_role_shortage_returns_403(env):
    client, _ = env
    token = _login(client, "reader", "reader")["token"]
    headers = _bearer(token)
    assert client.get("/api/connections", headers=headers).status_code == 200
    assert client.post("/api/connections", headers=headers, json={
        "project": "p", "name": "c", "ctype": "mysql", "meta": {}}).status_code == 403
    assert client.get("/api/users", headers=headers).status_code == 403


def test_disabled_account_token_rejected(env):
    client, store = env
    token = _login(client, "dev", "dev")["token"]
    assert client.get("/api/connections", headers=_bearer(token)).status_code == 200
    store.update_user("dev", enabled=False)
    assert client.get("/api/connections", headers=_bearer(token)).status_code == 401


def test_password_reset_invalidates_token(env):
    client, _ = env
    old = _login(client, "dev", "dev")["token"]
    admin_token = _login(client, "admin", "admin")["token"]
    assert client.patch("/api/users/dev/password", headers=_bearer(admin_token),
                        json={"password": "dev2"}).status_code == 200
    assert client.get("/api/connections", headers=_bearer(old)).status_code == 401
    assert client.get("/api/connections",
                      headers=_bearer(_login(client, "dev", "dev2")["token"])).status_code == 200


def test_system_resource_degrades_without_proc(env):
    """非 Linux 平台不返回 500：以 supported=false 与零值降级（D-05）。"""
    client, _ = env
    token = _login(client, "admin", "admin")["token"]
    resp = client.get("/api/monitor/system", headers=_bearer(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "supported" in data
    if os.name != "posix":
        assert data["supported"] is False
        assert data["disk"]["percent"] == 0
        assert data["memory"]["percent"] == 0


# ---- 服务凭据通道与用户会话隔离（1.5）----

def test_service_credential_trigger_needs_no_session(env):
    client, _ = env
    dev_token = _login(client, "dev", "dev")["token"]
    dev = _bearer(dev_token)
    task_key = client.post("/api/tasks", headers=dev,
                           json={"project": "proj-a", "name": "s", "config": TASK_CFG}
                           ).json()["task_key"]
    cred = client.post("/api/projects/proj-a/credentials", headers=dev, json={"label": "ci"})
    assert cred.status_code == 201
    # 仅服务凭据、无用户会话 → 正常受理
    fired = client.post(f"/api/trigger/{task_key}",
                        headers=_bearer(cred.json()["token"]))
    assert fired.status_code == 202, fired.text
    assert fired.json()["task_key"] == task_key
    # 无凭据 → 401（失败原因是缺少凭据，而不是缺少用户会话）
    assert client.post(f"/api/trigger/{task_key}").json()["detail"] == "缺少凭据"
