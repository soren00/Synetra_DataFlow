"""任务 2.2 验收：关系库连通性测试返回成功或明确失败原因。"""

from __future__ import annotations

import socket

import pytest

from app.connectivity import check_connection
from app.models.connection import ConnectionType

# 本地测试库（由部署环境提供；不可达时跳过相关用例）
MYSQL_META = {"host": "127.0.0.1", "port": "3306", "user": "synetra", "db": "synetra_test"}
MYSQL_PASS = "synetra_pass"


def _mysql_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 3306), timeout=2):
            return True
    except OSError:
        return False


needs_mysql = pytest.mark.skipif(not _mysql_reachable(), reason="本机未运行 MySQL")


def test_mysql_success():
    ok, detail = check_connection(ConnectionType.MYSQL, MYSQL_META, MYSQL_PASS)
    assert ok, f"应连接成功: {detail}"


@needs_mysql
def test_mysql_wrong_password_failure():
    ok, detail = check_connection(ConnectionType.MYSQL, MYSQL_META, "wrong-password")
    assert not ok and "连接失败" in detail


def test_missing_driver_gives_clear_reason():
    ok, detail = check_connection(ConnectionType.SQLSERVER, {"host": "x", "port": "1433"}, "pw")
    assert not ok
    assert ("未安装" in detail) or ("驱动" in detail)


def test_unimplemented_type_clear_reason():
    ok, detail = check_connection(ConnectionType.CSV, {}, None)
    assert not ok and "尚未实现" in detail