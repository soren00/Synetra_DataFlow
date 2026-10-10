"""任务 13.x：主数据管理（模型定义、记录 CRUD、质量校验、同步与变更历史）。"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

from app.mdm.quality import FieldDef, MasterModel, validate_record


def _client(role: str = "admin"):
    tmp = tempfile.TemporaryDirectory()
    os.environ["SYNETRA_DATA_DIR"] = tmp.name
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import app
    from app.store import Store

    store = Store(get_settings().data_dir)
    store.upsert_user("admin", role)
    client = TestClient(app)
    client.headers["X-Synetra-User"] = "admin"
    return client, tmp, store


MODEL = {
    "entity": "customer",
    "label": "客户",
    "fields": [
        {"name": "id", "type": "string", "required": True, "unique": True},
        {"name": "email", "type": "string", "format": "email"},
        {"name": "phone", "type": "string", "format": "phone"},
        {"name": "status", "type": "string"},
    ],
}


def _upsert(client, model=MODEL):
    return client.post("/api/mdm/models", json=model)


# ---- 13.1 模型定义与约束 ----

def test_upsert_and_list_model():
    client, tmp, _ = _client()
    try:
        assert _upsert(client).status_code == 201
        models = client.get("/api/mdm/models").json()
        assert len(models) == 1
        assert models[0]["entity"] == "customer" and models[0]["label"] == "客户"
        names = {f["name"] for f in models[0]["fields"]}
        assert names == {"id", "email", "phone", "status"}
    finally:
        client.close(); tmp.cleanup()


def test_upsert_model_duplicate_updates():
    client, tmp, _ = _client()
    try:
        _upsert(client)
        revised = {**MODEL, "label": "消费者"}
        assert _upsert(client, revised).status_code == 201
        models = client.get("/api/mdm/models").json()
        assert len(models) == 1 and models[0]["label"] == "消费者"
    finally:
        client.close(); tmp.cleanup()


# ---- 13.2 记录增删改查 ----

def test_record_crud():
    client, tmp, _store = _client()
    try:
        _upsert(client)
        rid = client.post("/api/mdm/models/customer/records",
                          json={"record": {"_id": "C001", "id": "C001", "status": "active"}}).json()["record_id"]
        assert rid == "C001"
        assert client.get("/api/mdm/models/customer/records").json() == [
            {"_id": "C001", "id": "C001", "status": "active"}]

        upd = client.put("/api/mdm/models/customer/records/C001",
                         json={"record": {"_id": "C001", "id": "C001", "status": "inactive"}})
        assert upd.status_code == 200
        assert upd.json()["record"]["status"] == "inactive"

        assert client.delete("/api/mdm/models/customer/records/C001").json()["deleted"] is True
        assert client.get("/api/mdm/models/customer/records").json() == []
    finally:
        client.close(); tmp.cleanup()


def test_record_create_missing_entity_404():
    client, tmp, _ = _client()
    try:
        r = client.post("/api/mdm/models/ghost/records", json={"record": {"id": "1"}})
        assert r.status_code == 404
    finally:
        client.close(); tmp.cleanup()


# ---- 13.3 质量校验（非空/格式/类型/唯一） ----

def test_create_rejects_required_and_format():
    client, tmp, _ = _client()
    try:
        _upsert(client)
        r = client.post("/api/mdm/models/customer/records",
                        json={"record": {"email": "bad-email"}})
        assert r.status_code == 422
        assert "id 为必填" in r.json()["detail"]
    finally:
        client.close(); tmp.cleanup()


def test_unique_enforced():
    client, tmp, _ = _client()
    try:
        _upsert(client)
        client.post("/api/mdm/models/customer/records", json={"record": {"_id": "C001", "id": "C001"}})
        dup = client.post("/api/mdm/models/customer/records", json={"record": {"_id": "C002", "id": "C001"}})
        assert dup.status_code == 422, dup.text
        assert "id 已存在" in dup.json()["detail"]
    finally:
        client.close(); tmp.cleanup()


def test_unique_allows_update_self():
    client, tmp, _ = _client()
    try:
        _upsert(client)
        client.post("/api/mdm/models/customer/records", json={"record": {"_id": "C001", "id": "C001"}})
        upd = client.put("/api/mdm/models/customer/records/C001",
                         json={"record": {"_id": "C001", "id": "C001", "status": "x"}})
        assert upd.status_code == 200
    finally:
        client.close(); tmp.cleanup()


def test_phone_format():
    assert validate_record(MasterModel(entity="e", fields=[FieldDef(name="p", format="phone")]),
                           {"p": "not-a-phone"})  # 非数字格式非法（列表非空）
    assert validate_record(MasterModel(entity="e", fields=[FieldDef(name="p", format="phone")]),
                           {"p": "abc"})
    assert validate_record(MasterModel(entity="e", fields=[FieldDef(name="p", format="phone")]),
                           {"p": "138-0013-8000"}) == []


def test_type_check():
    m = MasterModel(entity="e", fields=[FieldDef(name="n", type="int")])
    assert validate_record(m, {"n": "abc"})
    assert not validate_record(m, {"n": 5})
    assert validate_record(m, {"n": True})  # bool 不算 int


# ---- 13.4 变更历史与同步分发 ----

def test_change_history():
    client, tmp, _ = _client()
    try:
        _upsert(client)
        client.post("/api/mdm/models/customer/records", json={"record": {"_id": "C001", "id": "C001"}})
        client.put("/api/mdm/models/customer/records/C001",
                   json={"record": {"_id": "C001", "id": "C001", "status": "ok"}})
        hist = client.get("/api/mdm/models/customer/records/C001/history").json()
        assert [h["action"] for h in hist] == ["update", "create"]
    finally:
        client.close(); tmp.cleanup()


def test_sync_dispatch():
    client, tmp, store = _client()
    try:
        _upsert(client)
        client.post("/api/mdm/models/customer/records", json={"record": {"_id": "C001", "id": "C001"}})
        client.post("/api/mdm/models/customer/records", json={"record": {"_id": "C002", "id": "C002"}})
        r = client.post("/api/mdm/models/customer/sync")
        assert r.status_code == 200
        assert r.json()["dispatched"] == 2
        events = store.list_events(project="customer")
        assert any(ev["action"] == "mdm_sync" for ev in events)
    finally:
        client.close(); tmp.cleanup()