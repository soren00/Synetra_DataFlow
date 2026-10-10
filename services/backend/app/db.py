"""数据库连接抽象层（元数据/日志/审计库）。

设计目标：让 Store / Catalog 的业务 SQL 只写一份，同时支持两种后端：

- **MySQL**（生产默认）：由 ``SYNETRA_METADATA_DB_DSN`` 选择，形如
  ``mysql://user:password@host:port/dbname?charset=utf8mb4``；
- **SQLite**（本地/测试回退）：DSN 为空时使用 ``data_dir/synetra.db``。

``Database.connect()`` 返回的连接对象兼容本项目用到的 sqlite3 API
（``execute`` / ``executescript`` / ``row_factory`` / ``lastrowid`` / ``rowcount`` /
上下文管理器），业务层因此无需为方言分叉；SQLite 的 UPSERT 写法
（``ON CONFLICT ... DO UPDATE SET`` / ``INSERT OR IGNORE``）与 ``?`` 占位符
在 MySQL 通道内被自动翻译。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pymysql
from pymysql.constants import CLIENT
from pymysql.cursors import DictCursor

# ---------------------------------------------------------------- DSN 解析

_DEFAULT_CHARSET = "utf8mb4"


def parse_mysql_dsn(dsn: str | None) -> dict:
    """解析 MySQL DSN；为空、前缀非 mysql 或缺库名时返回空 dict（表示走 SQLite）。"""
    if not dsn or not dsn.strip():
        return {}
    text = dsn.strip()
    parsed = urlparse(text)
    if parsed.scheme.split("+", 1)[0] != "mysql":
        return {}
    if not parsed.hostname:
        return {}
    database = unquote((parsed.path or "").lstrip("/"))
    if not database:
        return {}
    query = parse_qs(parsed.query or "")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": database,
        "charset": (query.get("charset") or [_DEFAULT_CHARSET])[0],
    }


# ---------------------------------------------------------------- 方言翻译

_EXCLUDED_REF = re.compile(r"\bexcluded\.(\w+)", re.IGNORECASE)
_ON_CONFLICT = re.compile(
    r"\bON\s+CONFLICT\s*\([^)]*\)\s*DO\s+UPDATE\s+SET", re.IGNORECASE
)
_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO", re.IGNORECASE)


def translate_mysql_sql(sql: str) -> str:
    """把 SQLite 风格 SQL 翻译为 MySQL：占位符、UPSERT、INSERT OR IGNORE。

    pymysql 使用 ``%`` 作为格式化符，故先把 SQL 文本中的字面 ``%`` 转义为 ``%%``，
    再把 ``?`` 占位符替换为 ``%s``（顺序不可颠倒）。
    """
    out = sql.replace("%", "%%").replace("?", "%s")
    out = _EXCLUDED_REF.sub(r"VALUES(\1)", out)
    out = _ON_CONFLICT.sub("ON DUPLICATE KEY UPDATE", out)
    out = _INSERT_OR_IGNORE.sub("INSERT IGNORE INTO", out)
    return out


def split_statements(script: str) -> list[str]:
    """按 ``;`` 切分多语句脚本，丢弃空语句与 ``--`` 注释行。"""
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


# ---------------------------------------------------------------- 连接适配


class SQLiteConnection:
    """sqlite3 连接的轻量包装，统一上下文管理器语义（成功提交、异常回滚、退出关闭）。"""

    def __init__(self, path: str | Path):
        self._raw = sqlite3.connect(str(path))
        self._raw.row_factory = sqlite3.Row
        self.row_factory = sqlite3.Row

    def execute(self, sql: str, params=()):
        return self._raw.execute(sql, params)

    def executescript(self, script: str) -> None:
        self._raw.executescript(script)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        self._raw.close()
        return False


class MySQLConnection:
    """pymysql 连接的包装，行以 dict 返回（等价于 sqlite3.Row 的按列名访问）。"""

    def __init__(self, config: dict):
        self._raw = pymysql.connect(
            **config,
            autocommit=False,
            cursorclass=DictCursor,
            # FOUND_ROWS：UPDATE 的 rowcount 返回“匹配行数”，与 sqlite3 语义一致，
            # 避免“值未变化”被误判为“记录不存在”。
            client_flag=CLIENT.FOUND_ROWS,
            connect_timeout=10,
        )
        self.row_factory = None

    def execute(self, sql: str, params=()):
        cursor = self._raw.cursor()
        cursor.execute(translate_mysql_sql(sql), params or ())
        return cursor

    def executescript(self, script: str) -> None:
        for stmt in split_statements(script):
            self.execute(stmt)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        finally:
            self._raw.close()
        return False


class Database:
    """后端选择与连接工厂。"""

    def __init__(self, dsn: str | None = "", sqlite_path: str | Path | None = None):
        self.dsn = dsn or ""
        self.config = parse_mysql_dsn(self.dsn)
        self.dialect = "mysql" if self.config else "sqlite"
        self.sqlite_path = Path(sqlite_path) if sqlite_path else None

    @property
    def is_mysql(self) -> bool:
        return self.dialect == "mysql"

    def connect(self):
        if self.is_mysql:
            return MySQLConnection(self.config)
        if self.sqlite_path is None:
            raise ValueError("SQLite 后端需要提供 sqlite_path")
        return SQLiteConnection(self.sqlite_path)

    def ensure_database(self) -> None:
        """MySQL：幂等创建目标库；SQLite 无需处理。"""
        if not self.is_mysql:
            return
        config = dict(self.config)
        database = config.pop("database")
        server = pymysql.connect(**config, autocommit=True, connect_timeout=10)
        try:
            with server.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database}` "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        finally:
            server.close()
