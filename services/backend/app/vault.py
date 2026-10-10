"""conn_key 指向的安全存储（Vault）。

核心约束：明文凭证只在此处被加密为密文并以 conn_key 引用，
任务配置、配置快照、版本记录中一律不出现明文凭证（对应 data-connections 连接凭证安全需求）。

实现：对称加密（Fernet）——密钥来自环境变量 SYNETRA_VAULT_KEY（Fernet base64url 格式），
未提供时首次运行自动生成并持久化到数据目录（仅用于本地单机部署；
生产建议通过 KMS/Secret 注入环境变量）。
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

_VAULT_KEY_ENV = "SYNETRA_VAULT_KEY"


def _load_or_create_key(data_dir: Path) -> str:
    """取 Fernet 密钥字符串：环境变量 > 数据目录密钥文件 > 新生成并持久化。"""
    env = os.environ.get(_VAULT_KEY_ENV)
    if env:
        return env
    keyfile = data_dir / "vault.key"
    if keyfile.exists():
        return keyfile.read_text().strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key().decode()
    keyfile.write_text(key + "\n")
    try:
        os.chmod(keyfile, 0o600)
    except OSError:
        pass
    return key


def generate_conn_key() -> str:
    """生成不可猜的引用 key（32 字节 url-safe）。"""
    return secrets.token_urlsafe(32)


class SecretVault:
    """加密/解密明文秘密的实例。"""

    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else Path("data")
        self._fernet = Fernet(_load_or_create_key(self.data_dir))

    def encrypt(self, raw: str | bytes) -> str:
        data = raw if isinstance(raw, bytes) else raw.encode()
        return self._fernet.encrypt(data).decode()

    def decrypt(self, token: str) -> bytes:
        return self._fernet.decrypt(token.encode())