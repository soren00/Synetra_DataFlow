"""任务 2.6：对象存储/Hive/ClickHouse/API 目标连接器与推送幂等说明。"""

from __future__ import annotations

import tempfile

from app.connectivity import check_connection, target_push_idempotency
from app.models.connection import ConnectionCreate, ConnectionDirection, ConnectionType
from app.store import Store
from app.vault import SecretVault

_KEEP: list = []


def _store():
    tmp = tempfile.TemporaryDirectory()
    _KEEP.append(tmp)
    return Store(tmp.name, SecretVault(tmp.name))


def _check(ctype, meta):
    return check_connection(ctype, meta, "secret")


def test_object_storage_target_returns_result_without_crash():
    ok, detail = _check(ConnectionType.OBJECT_STORAGE,
                        {"endpoint": "http://minio:9000", "bucket": "b", "access_key": "k"})
    assert isinstance(ok, bool)
    assert isinstance(detail, str) and detail  # 明确原因（驱动缺失或真实连接结果）


def test_hive_target_returns_result():
    ok, detail = _check(ConnectionType.HIVE, {"host": "h", "port": "10000"})
    assert isinstance(ok, bool) and isinstance(detail, str) and detail


def test_clickhouse_target_returns_result():
    ok, detail = _check(ConnectionType.CLICKHOUSE, {"host": "c", "port": "8123"})
    assert isinstance(ok, bool) and isinstance(detail, str) and detail


def test_api_target_idempotency_note():
    assert "幂等" in (target_push_idempotency(ConnectionType.API) or "")
    # 关系库目标返回通用事务说明（None）
    assert target_push_idempotency(ConnectionType.POSTGRESQL) is None


def test_target_can_be_saved_and_push_note_exposed():
    store = _store()
    conn = store.create_connection(ConnectionCreate(
        project="p", name="api-tgt", ctype=ConnectionType.API,
        direction=ConnectionDirection.TARGET,
        meta={"url": "https://ingest.example/hook", "method": "POST", "auth": "bearer"},
        secret="tok", created_by="t"))
    # 直接覆盖目标推送幂等函数，不依赖 HTTP: 断言 API 目标说明存在
    assert "幂等" in (target_push_idempotency(conn.ctype) or "")
    assert conn.direction.value == "target"