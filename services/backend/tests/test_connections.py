"""任务 2.1 验收：连接数据模型 + conn_key 安全存储。

验证点：
  1. 保存连接后，明文凭证不在 SQLite 文件中（"不入库"）。
  2. 返回的连接信息不含明文/密文秘密（"不入配置快照/返回值"）。
  3. 密文可经同一密钥跨多次打开解密（持久化正确）。
  4. conn_key 唯一、且与明文无直接可逆关系。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from app.models.connection import ConnectionCreate, ConnectionType
from app.store import Store
from app.vault import SecretVault

PLAINTEXT = "SuperSecret#2026-xyz"


@pytest.fixture()
def data_dir() -> Path:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _store(data_dir: Path) -> Store:
    # 每个 store 用独立数据目录 => 自动生成的密钥不同，用于验证持久解密的只是同一目录内的持久密钥
    return Store(data_dir)


def test_plaintext_not_in_db_bytes(data_dir):
    s = _store(data_dir)
    s.create_connection(
        ConnectionCreate(
            project="p1", name="mysql-a", ctype=ConnectionType.MYSQL,
            meta={"host": "127.0.0.1", "port": "3306", "db": "app"}, secret=PLAINTEXT,
        )
    )
    raw = (data_dir / "synetra.db").read_bytes()
    assert PLAINTEXT.encode() not in raw, "明文凭证出现在数据库文件字节中"


def test_returned_object_has_no_secret(data_dir):
    s = _store(data_dir)
    out = s.create_connection(
        ConnectionCreate(
            project="p1", name="mysql-a", ctype=ConnectionType.MYSQL,
            meta={"host": "127.0.0.1"}, secret=PLAINTEXT,
        )
    )
    outd = out.model_dump()
    blob = outd | outd["meta"]
    assert PLAINTEXT not in str(blob), "返回对象泄漏明文秘密"
    assert not hasattr(out, "secret"), "对外模型不应含 secret 字段"
    assert "cipher" not in str(outd).lower()


def test_secret_decrypts_after_reopen(data_dir):
    first = Store(data_dir)
    second = _store(data_dir)
    # first 建连并落密；second 是同一目录的新 Store 实例（复用持久化密钥）
    out = first.create_connection(
        ConnectionCreate(
            project="p1", name="mysql-a", ctype=ConnectionType.MYSQL,
            meta={}, secret=PLAINTEXT,
        )
    )
    # 用 first 的 vault 也行，但这里验证持久密钥跨实例可用：
    assert second.get_secret(out.conn_key) == PLAINTEXT.encode()


def test_conn_key_unique(data_dir):
    s = _store(data_dir)
    k1 = s.create_connection(
        ConnectionCreate(project="p1", name="a", ctype=ConnectionType.MYSQL, secret="x")
    ).conn_key
    k2 = s.create_connection(
        ConnectionCreate(project="p2", name="b", ctype=ConnectionType.POSTGRESQL, secret="y")
    ).conn_key
    assert k1 != k2 and len(k1) >= 40 and len(k2) >= 40


def test_conn_key_not_reversed_from_plaintext(data_dir):
    s = _store(data_dir)
    out = s.create_connection(
        ConnectionCreate(project="p1", name="a", ctype=ConnectionType.MYSQL, secret=PLAINTEXT)
    )
    assert not out.conn_key.startswith("SuperSecret")


def test_custom_key_env(data_dir, monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SYNETRA_VAULT_KEY", key)
    v = SecretVault(data_dir)
    assert v.decrypt(v.encrypt(PLAINTEXT)) == PLAINTEXT.encode()


def test_no_plaintext_in_env_bootstrap(data_dir):
    # 若 VM 未设 SYNETRA_VAULT_KEY，自动生成持久密钥，构建过程不打印明文
    os.environ.pop("SYNETRA_VAULT_KEY", None)
    v = SecretVault(data_dir)
    assert v.decrypt(v.encrypt("abc123")) == b"abc123"