"""任务 2.3 验收：关系库目标连接的保存/测试，配置不含明文凭证。"""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path

import pytest

from app.models.connection import ConnectionCreate, ConnectionDirection, ConnectionType
from app.store import Store

MYSQL_META = {"host": "127.0.0.1", "port": "3306", "user": "synetra", "db": "synetra_test"}


def _mysql_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 3306), timeout=2):
            return True
    except OSError:
        return False


needs_mysql = pytest.mark.skipif(not _mysql_reachable(), reason="本机未运行 MySQL")


def _store():
    tmp = tempfile.TemporaryDirectory()
    return Store(Path(tmp.name)), tmp


def test_target_connection_saved_with_direction():
    store, tmp = _store()
    try:
        out = store.create_connection(
            ConnectionCreate(
                project="p1", name="dw-mysql", ctype=ConnectionType.MYSQL,
                direction=ConnectionDirection.TARGET,
                meta=MYSQL_META, secret="synetra_pass",
            )
        )
        assert out.direction == ConnectionDirection.TARGET
        assert "synetra_pass" not in out.model_dump(exclude_none=True).__str__()
    finally:
        tmp.cleanup()


@needs_mysql
def test_target_connection_testable():
    store, tmp = _store()
    try:
        out = store.create_connection(
            ConnectionCreate(
                project="p1", name="dw-mysql", ctype=ConnectionType.MYSQL,
                direction=ConnectionDirection.TARGET,
                meta=MYSQL_META, secret="synetra_pass",
            )
        )
        secret = store.get_secret(out.conn_key).decode()
        from app.connectivity import check_connection

        ok, detail = check_connection(out.ctype, out.meta, secret)
        assert ok, detail
    finally:
        tmp.cleanup()


def test_list_filter_by_direction():
    store, tmp = _store()
    try:
        store.create_connection(
            ConnectionCreate(project="p1", name="src", ctype=ConnectionType.MYSQL,
                             direction=ConnectionDirection.SOURCE, secret="a")
        )
        store.create_connection(
            ConnectionCreate(project="p1", name="tgt", ctype=ConnectionType.MYSQL,
                             direction=ConnectionDirection.TARGET, secret="b")
        )
        targets = store.list_connections(project="p1", direction=ConnectionDirection.TARGET)
        sources = store.list_connections(project="p1", direction=ConnectionDirection.SOURCE)
        allc = store.list_connections(project="p1")
        assert [c.name for c in targets] == ["tgt"]
        assert [c.name for c in sources] == ["src"]
        assert len(allc) == 2
    finally:
        tmp.cleanup()