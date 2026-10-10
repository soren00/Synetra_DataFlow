"""连接与秘密的持久化存储（MySQL 默认，SQLite 可回退）。

- connection 表：仅存结构化元数据与 conn_key 引用，不含明文凭证。
- secret 表：由 Vault 加密后的密文 token，以 conn_key 关联。
任何一条写入路径都不允许出现明文 secret（除 Vault 加密输入外）。

存储后端由 ``SYNETRA_METADATA_DB_DSN`` 决定：设置为 ``mysql://...`` 时使用 MySQL，
为空时回退到 ``data_dir/synetra.db``（本地开发与测试）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.db import Database
from app.mdm.quality import MasterModel
from app.models.connection import (
    ConnectionCreate,
    ConnectionDirection,
    ConnectionOut,
)
from app.models.task_config import TaskConfig
from app.vault import SecretVault, generate_conn_key

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS connection (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conn_key TEXT UNIQUE NOT NULL,
    project TEXT NOT NULL,
    name TEXT NOT NULL,
    ctype TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'source',
    meta_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS secret (
    conn_key TEXT PRIMARY KEY,
    cipher_blob TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    project TEXT NOT NULL,
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT UNIQUE NOT NULL,
    project TEXT NOT NULL,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    current_path TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    saved_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_account (
    username TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS service_credential (
    cred_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    label TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trigger_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_id TEXT UNIQUE NOT NULL,
    task_key TEXT NOT NULL,
    project TEXT NOT NULL,
    fired_at TEXT NOT NULL,
    via TEXT NOT NULL,
    actor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mdm_model (
    entity TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    fields_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mdm_record (
    entity TEXT NOT NULL,
    record_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (entity, record_id)
);
CREATE TABLE IF NOT EXISTS mdm_change (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    record_id TEXT NOT NULL,
    action TEXT NOT NULL,
    record_json TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    task_key TEXT NOT NULL,
    project TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger_via TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    rows_total INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    attempt INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    line TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_metric (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    cpu_percent REAL NOT NULL DEFAULT 0,
    memory_mb REAL NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0,
    duration_s REAL NOT NULL DEFAULT 0,
    data_rows INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reconciliation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    task_key TEXT NOT NULL,
    source_rows INTEGER NOT NULL DEFAULT 0,
    target_rows INTEGER NOT NULL DEFAULT 0,
    checksum_match INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_rule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    task_key TEXT NOT NULL,
    metric TEXT NOT NULL,
    op TEXT NOT NULL,
    threshold REAL NOT NULL,
    notify_to TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS alert_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL,
    project TEXT NOT NULL,
    task_key TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    ts TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS llm_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT NOT NULL DEFAULT 'deepseek',
    base_url TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    api_key_cipher TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS project (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS llm_generation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    task_key TEXT NOT NULL DEFAULT '',
    task_name TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    prompts_json TEXT NOT NULL DEFAULT '[]',
    draft_json TEXT NOT NULL DEFAULT '{}',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    skills_json TEXT NOT NULL DEFAULT '[]',
    conversation_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_llm_generation_created ON llm_generation(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_generation_status ON llm_generation(status);
"""

# MySQL 版表结构（与 _SCHEMA 等价）：
# - 主键/唯一键/索引列必须为 VARCHAR 并控制长度（utf8mb4 下索引前缀上限 3072 字节）；
# - JSON/长文本列使用 LONGTEXT（不能带 DEFAULT，插入路径均显式赋值）；
# - AUTO_INCREMENT 列须为键，故将其置于 PRIMARY KEY 之前。
_SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS connection (
    id INT AUTO_INCREMENT PRIMARY KEY,
    conn_key VARCHAR(128) UNIQUE NOT NULL,
    project VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    ctype VARCHAR(64) NOT NULL,
    direction VARCHAR(32) NOT NULL DEFAULT 'source',
    meta_json LONGTEXT NOT NULL,
    created_by VARCHAR(255) NOT NULL,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS secret (
    conn_key VARCHAR(128) PRIMARY KEY,
    cipher_blob LONGTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS audit_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ts VARCHAR(64) NOT NULL,
    actor VARCHAR(255) NOT NULL,
    project VARCHAR(255) NOT NULL,
    action VARCHAR(128) NOT NULL,
    detail_json LONGTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS task (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_key VARCHAR(128) UNIQUE NOT NULL,
    project VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    version INT NOT NULL,
    config_json LONGTEXT NOT NULL,
    current_path VARCHAR(1024) NOT NULL DEFAULT '',
    updated_at VARCHAR(64) NOT NULL,
    updated_by VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS task_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_key VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    config_json LONGTEXT NOT NULL,
    saved_at VARCHAR(64) NOT NULL,
    saved_by VARCHAR(255) NOT NULL,
    KEY idx_task_history_key (task_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS user_account (
    username VARCHAR(128) PRIMARY KEY,
    role VARCHAR(64) NOT NULL,
    password_hash VARCHAR(255) NOT NULL DEFAULT '',
    display_name VARCHAR(255) NOT NULL DEFAULT '',
    enabled INT NOT NULL DEFAULT 1,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS service_credential (
    cred_id VARCHAR(128) PRIMARY KEY,
    project VARCHAR(255) NOT NULL,
    label VARCHAR(255) NOT NULL,
    token_hash VARCHAR(191) NOT NULL,
    created_at VARCHAR(64) NOT NULL,
    revoked INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS trigger_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trigger_id VARCHAR(128) UNIQUE NOT NULL,
    task_key VARCHAR(128) NOT NULL,
    project VARCHAR(255) NOT NULL,
    fired_at VARCHAR(64) NOT NULL,
    via VARCHAR(64) NOT NULL,
    actor VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS mdm_model (
    entity VARCHAR(191) PRIMARY KEY,
    label VARCHAR(255) NOT NULL DEFAULT '',
    fields_json LONGTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS mdm_record (
    entity VARCHAR(191) NOT NULL,
    record_id VARCHAR(191) NOT NULL,
    record_json LONGTEXT NOT NULL,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (entity, record_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS mdm_change (
    id INT AUTO_INCREMENT PRIMARY KEY,
    entity VARCHAR(191) NOT NULL,
    record_id VARCHAR(191) NOT NULL,
    action VARCHAR(64) NOT NULL,
    record_json LONGTEXT NOT NULL,
    ts VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS run_record (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(128) UNIQUE NOT NULL,
    task_key VARCHAR(128) NOT NULL,
    project VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL,
    trigger_via VARCHAR(64) NOT NULL,
    started_at VARCHAR(64) NOT NULL,
    finished_at VARCHAR(64) NULL,
    rows_total BIGINT NOT NULL DEFAULT 0,
    error LONGTEXT NULL,
    attempt INT NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS run_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(128) NOT NULL,
    ts VARCHAR(64) NOT NULL,
    level VARCHAR(16) NOT NULL DEFAULT 'info',
    line LONGTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS run_metric (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(128) NOT NULL,
    cpu_percent DOUBLE NOT NULL DEFAULT 0,
    memory_mb DOUBLE NOT NULL DEFAULT 0,
    latency_ms DOUBLE NOT NULL DEFAULT 0,
    duration_s DOUBLE NOT NULL DEFAULT 0,
    data_rows BIGINT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS reconciliation (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project VARCHAR(255) NOT NULL,
    task_key VARCHAR(128) NOT NULL,
    source_rows BIGINT NOT NULL DEFAULT 0,
    target_rows BIGINT NOT NULL DEFAULT 0,
    checksum_match INT NOT NULL DEFAULT 0,
    details_json LONGTEXT NOT NULL,
    created_at VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS alert_rule (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project VARCHAR(255) NOT NULL,
    task_key VARCHAR(128) NOT NULL,
    metric VARCHAR(64) NOT NULL,
    op VARCHAR(16) NOT NULL,
    threshold DOUBLE NOT NULL,
    notify_to VARCHAR(512) NOT NULL DEFAULT '',
    enabled INT NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS alert_event (
    id INT AUTO_INCREMENT PRIMARY KEY,
    rule_id INT NOT NULL,
    project VARCHAR(255) NOT NULL,
    task_key VARCHAR(128) NOT NULL,
    metric VARCHAR(64) NOT NULL,
    value DOUBLE NOT NULL,
    ts VARCHAR(64) NOT NULL,
    sent INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS llm_config (
    id INT NOT NULL PRIMARY KEY CHECK (id = 1),
    provider VARCHAR(64) NOT NULL DEFAULT 'deepseek',
    base_url VARCHAR(512) NOT NULL DEFAULT '',
    model VARCHAR(255) NOT NULL DEFAULT '',
    api_key_cipher LONGTEXT NOT NULL,
    updated_at VARCHAR(64) NOT NULL DEFAULT '',
    updated_by VARCHAR(255) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS project (
    name VARCHAR(255) PRIMARY KEY,
    created_at VARCHAR(64) NOT NULL,
    created_by VARCHAR(255) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS llm_generation (
    id INT AUTO_INCREMENT PRIMARY KEY,
    created_at VARCHAR(64) NOT NULL,
    actor VARCHAR(255) NOT NULL DEFAULT '',
    project VARCHAR(255) NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL,
    task_key VARCHAR(128) NOT NULL DEFAULT '',
    task_name VARCHAR(255) NOT NULL DEFAULT '',
    error LONGTEXT NOT NULL,
    prompts_json LONGTEXT NOT NULL,
    draft_json LONGTEXT NOT NULL,
    provider VARCHAR(64) NOT NULL DEFAULT '',
    model VARCHAR(255) NOT NULL DEFAULT '',
    skills_json LONGTEXT NOT NULL,
    conversation_id VARCHAR(128) NOT NULL DEFAULT '',
    KEY idx_llm_generation_created (created_at),
    KEY idx_llm_generation_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _password_hash(password: str) -> str:
    import hashlib

    salt = hashlib.sha256((password + "synetra-salt").encode()).digest()[:16]
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"{salt.hex()}${dk.hex()}"


def _password_verify(password: str, stored: str) -> bool:
    import hashlib

    try:
        salt_hex, dk_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
        return dk.hex() == dk_hex
    except ValueError:
        return False


# 进程级「建表已完成」标记（键：MySQL DSN 或 SQLite 文件路径）
_INIT_LOCK = threading.Lock()
_INITIALIZED: set[str] = set()


class Store:
    def __init__(self, data_dir: str | Path, vault: SecretVault | None = None,
                 dsn: str | None = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "synetra.db"
        self.vault = vault or SecretVault(self.data_dir)
        # dsn 为空字符串 => 显式回退 SQLite；None => 读取配置（默认 MySQL DSN）
        if dsn is None:
            dsn = get_settings().metadata_db_dsn
        self.db = Database(dsn, sqlite_path=self.db_path)
        self._init_schema()

    def _conn(self):
        return self.db.connect()

    def _existing_columns(self, c, table: str) -> set[str]:
        """已存在列名集合，用于幂等补列（SQLite 与 MySQL 两种取法）。"""
        if self.db.is_mysql:
            # information_schema 返回大写列名，显式别名以稳定取键
            rows = c.execute(
                "SELECT COLUMN_NAME AS col FROM information_schema.columns"
                " WHERE table_schema=DATABASE() AND table_name=?",
                (table,),
            ).fetchall()
            return {r["col"] for r in rows}
        rows = c.execute(f"PRAGMA table_info({table})").fetchall()
        return {r["name"] for r in rows}

    def _init_schema(self) -> None:
        # 建表/补列是幂等但昂贵的操作：按后端做进程级一次性初始化，
        # 避免每次请求构造 Store 都重复执行 DDL（MySQL 下尤甚）。
        target = self.db.dsn or f"sqlite:{self.db_path}"
        with _INIT_LOCK:
            if target in _INITIALIZED:
                return
            self._ensure_schema()
            _INITIALIZED.add(target)

    def _ensure_schema(self) -> None:
        if self.db.is_mysql:
            self.db.ensure_database()
        schema = _SCHEMA_MYSQL if self.db.is_mysql else _SCHEMA
        with self._conn() as c:
            c.executescript(schema)
            # 存量库迁移：逐列补齐 user_account 新字段（已存在则跳过）
            existing = self._existing_columns(c, "user_account")
            for col, col_sql in (
                ("password_hash", "ALTER TABLE user_account ADD COLUMN password_hash VARCHAR(255) NOT NULL DEFAULT ''" if self.db.is_mysql else "ALTER TABLE user_account ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''"),
                ("display_name", "ALTER TABLE user_account ADD COLUMN display_name VARCHAR(255) NOT NULL DEFAULT ''" if self.db.is_mysql else "ALTER TABLE user_account ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"),
                ("enabled", "ALTER TABLE user_account ADD COLUMN enabled INT NOT NULL DEFAULT 1" if self.db.is_mysql else "ALTER TABLE user_account ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"),
                ("updated_at", "ALTER TABLE user_account ADD COLUMN updated_at VARCHAR(64) NOT NULL DEFAULT ''" if self.db.is_mysql else "ALTER TABLE user_account ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"),
            ):
                if col in existing:
                    continue
                c.execute(col_sql)

    def create_connection(self, data: ConnectionCreate) -> ConnectionOut:
        """落库：秘密经 Vault 加密为密文存储，元数据/快照中无明文。"""
        conn_key = generate_conn_key()
        now = _utcnow()
        meta_json = json.dumps(data.meta, ensure_ascii=False)
        with self._conn() as c:
            c.execute(
                "INSERT INTO connection"
                "(conn_key, project, name, ctype, direction, meta_json, created_by, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (conn_key, data.project, data.name, data.ctype.value,
                 data.direction.value, meta_json, data.created_by, now, now),
            )
            if data.secret:
                cipher = self.vault.encrypt(data.secret)
                c.execute(
                    "INSERT INTO secret(conn_key, cipher_blob) VALUES (?,?)",
                    (conn_key, cipher),
                )
        return self.get_connection(conn_key)

    def update_connection(self, conn_key: str, data: ConnectionCreate) -> ConnectionOut:
        """更新连接元数据；secret 为空表示保持不变，非空则重加密 upsert（可轮换密码）。"""
        now = _utcnow()
        meta_json = json.dumps(data.meta, ensure_ascii=False)
        with self._conn() as c:
            cur = c.execute(
                "UPDATE connection SET project=?, name=?, ctype=?, direction=?, meta_json=?, updated_at=? "
                "WHERE conn_key=?",
                (data.project, data.name, data.ctype.value, data.direction.value,
                 meta_json, now, conn_key),
            )
            if cur.rowcount == 0:
                raise KeyError(conn_key)
            if data.secret:
                cipher = self.vault.encrypt(data.secret)
                c.execute(
                    "INSERT INTO secret(conn_key, cipher_blob) VALUES (?,?) "
                    "ON CONFLICT(conn_key) DO UPDATE SET cipher_blob=excluded.cipher_blob",
                    (conn_key, cipher),
                )
        return self.get_connection(conn_key)

    def get_connection(self, conn_key: str) -> ConnectionOut:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM connection WHERE conn_key=?", (conn_key,)
            ).fetchone()
        if not row:
            raise KeyError(conn_key)
        return ConnectionOut(
            conn_key=row["conn_key"],
            project=row["project"],
            name=row["name"],
            ctype=row["ctype"],
            direction=dict(row).get("direction", "source"),
            meta=json.loads(row["meta_json"]),
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_connections(
        self,
        project: str | None = None,
        direction: ConnectionDirection | None = None,
    ) -> list[ConnectionOut]:
        sql, args = "SELECT conn_key FROM connection WHERE 1=1", []
        if project:
            sql += " AND project=?"
            args.append(project)
        if direction:
            sql += " AND direction=?"
            args.append(direction.value)
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [self.get_connection(r["conn_key"]) for r in rows]

    def get_secret(self, conn_key: str) -> bytes:
        """解出密文对应的明文秘密（运行时仅供连通性测试/执行路径使用，不落库）。"""
        with self._conn() as c:
            row = c.execute(
                "SELECT cipher_blob FROM secret WHERE conn_key=?", (conn_key,)
            ).fetchone()
        if not row:
            raise KeyError(conn_key)
        return self.vault.decrypt(row["cipher_blob"])

    def has_secret(self, conn_key: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM secret WHERE conn_key=?", (conn_key,)
            ).fetchone()
        return row is not None

    def tasks_using_connection(self, conn_key: str) -> list[dict]:
        """返回引用了该连接的 ETL 任务（源或目标任一命中），用于删除前拦截。

        连接被任务引用时直接删除会让任务指向不存在的连接，故 API 层据此拒绝删除。
        """
        out = []
        for task in self.list_tasks():
            if conn_key in (task.get("source_conn_key"), task.get("target_conn_key")):
                out.append({"task_key": task.get("task_key", ""), "name": task.get("name", "")})
        return out

    def delete_connection(self, conn_key: str) -> bool:
        """删除连接及其密文（同一事务）；连接不存在返回 False。

        无外键约束，引用校验由调用方（API 层）在删除前完成。
        """
        with self._conn() as c:
            cur = c.execute("DELETE FROM connection WHERE conn_key=?", (conn_key,))
            if cur.rowcount == 0:
                return False
            c.execute("DELETE FROM secret WHERE conn_key=?", (conn_key,))
        return True

    def log_event(self, actor: str, project: str, action: str, detail: dict) -> None:
        """写审计日志（任务 3.2 / 7.3）；detail 中不得含明文凭证。"""
        with self._conn() as c:
            c.execute(
                "INSERT INTO audit_log(ts, actor, project, action, detail_json)"
                " VALUES (?,?,?,?,?)",
                (_utcnow(), actor, project, action, json.dumps(detail, ensure_ascii=False)),
            )

    def list_events(self, project: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT ts, actor, project, action, detail_json FROM audit_log"
        args: list = []
        if project:
            sql += " WHERE project=?"
            args.append(project)
        sql += f" ORDER BY id DESC LIMIT {int(limit)}"
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # ---- 任务配置持久化（任务 4.1 / 4.3） ----

    def _next_task_version(self, c, key: str) -> int:
        row = c.execute("SELECT MAX(version) AS v FROM task_history WHERE task_key=?", (key,)).fetchone()
        return (row["v"] if row and row["v"] else 0) + 1

    def save_task(self, cfg: TaskConfig, task_key: str | None = None) -> str:
        """保存配置并写历史版本（任务 4.x/12.1）；返回 task_key。"""
        key = task_key or generate_conn_key()
        now = _utcnow()
        with self._conn() as c:
            version = self._next_task_version(c, key)
            cfg.version = version
            payload = json.dumps(cfg.model_dump(), ensure_ascii=False)
            c.execute(
                "INSERT INTO task(task_key, project, name, version, config_json, updated_at, updated_by)"
                " VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(task_key) DO UPDATE SET"
                " project=excluded.project, name=excluded.name, version=excluded.version,"
                " config_json=excluded.config_json, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (key, cfg.project, cfg.name, version, payload, now, cfg.updated_by),
            )
            c.execute(
                "INSERT INTO task_history(task_key, version, config_json, saved_at, saved_by)"
                " VALUES (?,?,?,?,?)",
                (key, version, payload, now, cfg.updated_by),
            )
        return key

    def get_task_history(self, task_key: str) -> list[dict]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT version, config_json, saved_at, saved_by FROM task_history "
                "WHERE task_key=? ORDER BY version DESC", (task_key,),
            ).fetchall()
        return [dict(r) for r in rows]

    def rollback_task(self, task_key: str, version: int, *, saved_by: str = "system") -> dict:
        """把配置回滚到指定历史版本，作为新 current 写入并追加历史；返回新配置。"""
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT config_json FROM task_history WHERE task_key=? AND version=?",
                (task_key, version),
            ).fetchone()
            if not row:
                raise KeyError((task_key, version))
        cfg = TaskConfig.model_validate(json.loads(row["config_json"]))
        now = _utcnow()
        with self._conn() as c:
            new_version = self._next_task_version(c, task_key)
            cfg.version = new_version
            payload = json.dumps(cfg.model_dump(), ensure_ascii=False)
            c.execute(
                "UPDATE task SET config_json=?, version=? WHERE task_key=?",
                (payload, new_version, task_key),
            )
            c.execute(
                "INSERT INTO task_history(task_key, version, config_json, saved_at, saved_by)"
                " VALUES (?,?,?,?,?)",
                (task_key, new_version, payload, now, saved_by),
            )
        return cfg.model_dump()

    def get_task(self, task_key: str) -> TaskConfig:
        with self._conn() as c:
            row = c.execute(
                "SELECT config_json FROM task WHERE task_key=?", (task_key,)
            ).fetchone()
        if not row:
            raise KeyError(task_key)
        return TaskConfig.model_validate(json.loads(row["config_json"]))

    def list_tasks(self, project: str | None = None) -> list[TaskConfig]:
        with self._conn() as c:
            sql = "SELECT task_key, config_json FROM task"
            args: list = []
            if project:
                sql += " WHERE project=?"
                args.append(project)
            rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            cfg = TaskConfig.model_validate(json.loads(r["config_json"])).model_dump()
            cfg["task_key"] = r["task_key"]
            out.append(cfg)
        return out

    def delete_task(self, task_key: str) -> bool:
        """删除任务及其配置；任务不存在返回 False。

        调度计划内嵌在任务配置（config_json.schedule）中，随该行删除即一并停用/移除，
        不会留下孤儿调度。删除后 task_history 等历史流水保留以便追溯。
        """
        with self._conn() as c:
            cur = c.execute("DELETE FROM task WHERE task_key=?", (task_key,))
        return cur.rowcount > 0

    # ---- 项目管理（ETL 任务归属；仅管理任务） ----

    def create_project(self, name: str, *, created_by: str = "") -> dict:
        """创建项目；重名抛 ValueError（由 API 转 409），名称为空同样拒绝。"""
        clean = (name or "").strip()
        if not clean:
            raise ValueError("项目名称不能为空")
        now = _utcnow()
        with self._conn() as c:
            if c.execute("SELECT 1 FROM project WHERE name=?", (clean,)).fetchone():
                raise ValueError(f"项目「{clean}」已存在")
            c.execute("INSERT INTO project(name, created_at, created_by) VALUES (?,?,?)",
                      (clean, now, created_by))
        return {"name": clean, "created_at": now, "created_by": created_by, "task_count": 0}

    def list_projects(self) -> list[dict]:
        """列出项目及其任务数。项目清单只反映 ETL 任务归属，不含连接/调度等资源。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT p.name, p.created_at, p.created_by,"
                " (SELECT COUNT(*) FROM task t WHERE t.project = p.name) AS task_count"
                " FROM project p ORDER BY p.name"
            ).fetchall()
        return [{"name": r["name"], "created_at": r["created_at"],
                 "created_by": r["created_by"], "task_count": int(r["task_count"])}
                for r in rows]

    def get_project(self, name: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT name, created_at, created_by FROM project WHERE name=?", (name,)
            ).fetchone()
        if not row:
            return None
        return {"name": row["name"], "created_at": row["created_at"],
                "created_by": row["created_by"],
                "task_count": self.project_task_count(row["name"])}

    def project_task_count(self, name: str) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) AS n FROM task WHERE project=?", (name,)).fetchone()
        return int(row["n"])

    def delete_project(self, name: str) -> bool:
        """删除项目本身；项目不存在返回 False。任务级联不在本方法职责内（由 API 先行校验非空）。"""
        with self._conn() as c:
            cur = c.execute("DELETE FROM project WHERE name=?", (name,))
        return cur.rowcount > 0

    def seed_projects_from_tasks(self, *, default_project: str = "default") -> int:
        """把既有任务的 project 值播种为项目条目（幂等），返回本次新增条数。

        用于引入项目实体后保持存量任务可见：已有 project 值的任务不因新实体而丢失归属；
        空 project 归入默认项目。仅在任务侧播种（项目只管理 ETL 任务）。
        """
        now = _utcnow()
        with self._conn() as c:
            rows = c.execute("SELECT DISTINCT project FROM task").fetchall()
        names = [((r["project"] or "").strip() or default_project) for r in rows]
        created = 0
        with self._conn() as c:
            for name in dict.fromkeys(names):
                cur = c.execute(
                    "INSERT OR IGNORE INTO project(name, created_at, created_by) VALUES (?,?,?)",
                    (name, now, "seed"),
                )
                created += cur.rowcount
        return created

    # ---- AI 生成与创建记录（AI ETL 路径的创建留痕） ----

    def save_generation_record(self, *, actor: str, project: str, status: str,
                               prompts: list[str], draft: dict | None = None,
                               task_key: str = "", task_name: str = "", error: str = "",
                               provider: str = "", model: str = "", skills: list[str] | None = None,
                               conversation_id: str = "") -> int:
        """写一条「确认创建任务」的记录（成功或失败都写）。

        记录中的提示词与草稿快照由调用方先行脱敏；失败记录中的草稿仅用于诊断复核，
        不等同于任务落库（不进入 task 表、不可执行）。
        """
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO llm_generation(created_at, actor, project, status, task_key, task_name,"
                " error, prompts_json, draft_json, provider, model, skills_json, conversation_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_utcnow(), actor, project, status, task_key, task_name, error,
                 json.dumps(list(prompts or []), ensure_ascii=False),
                 json.dumps(draft or {}, ensure_ascii=False), provider, model,
                 json.dumps(list(skills or []), ensure_ascii=False), conversation_id),
            )
        return int(cur.lastrowid or 0)

    @staticmethod
    def _generation_row(row) -> dict:
        return {
            "id": int(row["id"]),
            "created_at": row["created_at"],
            "actor": row["actor"],
            "project": row["project"],
            "status": row["status"],
            "task_key": row["task_key"],
            "task_name": row["task_name"],
            "error": row["error"],
            "prompts": json.loads(row["prompts_json"] or "[]"),
            "draft": json.loads(row["draft_json"] or "{}"),
            "provider": row["provider"],
            "model": row["model"],
            "skills": json.loads(row["skills_json"] or "[]"),
            "conversation_id": row["conversation_id"],
        }

    @staticmethod
    def _generation_filters(project: str | None, status: str | None) -> tuple[str, list]:
        clauses: list[str] = []
        args: list = []
        if project:
            clauses.append("project=?")
            args.append(project)
        if status:
            clauses.append("status=?")
            args.append(status)
        return (f" WHERE {' AND '.join(clauses)}" if clauses else ""), args

    _GENERATION_COLS = ("id, created_at, actor, project, status, task_key, task_name, error,"
                        " prompts_json, draft_json, provider, model, skills_json, conversation_id")

    def list_generations(self, *, project: str | None = None, status: str | None = None,
                         limit: int = 20, offset: int = 0) -> list[dict]:
        where, args = self._generation_filters(project, status)
        with self._conn() as c:
            rows = c.execute(
                f"SELECT {self._GENERATION_COLS} FROM llm_generation{where}"
                " ORDER BY id DESC LIMIT ? OFFSET ?",
                [*args, int(limit), int(offset)],
            ).fetchall()
        return [self._generation_row(r) for r in rows]

    def get_generation(self, record_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                f"SELECT {self._GENERATION_COLS} FROM llm_generation WHERE id=?", (int(record_id),)
            ).fetchone()
        return self._generation_row(row) if row else None

    def generation_totals(self, *, project: str | None = None, status: str | None = None) -> dict:
        where, args = self._generation_filters(project, status)
        with self._conn() as c:
            row = c.execute(f"SELECT COUNT(*) AS n FROM llm_generation{where}", args).fetchone()
        return {"count": int(row["n"])}

    def generation_status_counts(self, *, project: str | None = None) -> dict:
        where, args = self._generation_filters(project, None)
        with self._conn() as c:
            rows = c.execute(
                f"SELECT status, COUNT(*) AS n FROM llm_generation{where} GROUP BY status", args
            ).fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    # ---- 调度管理（任务 8.x） ----

    def list_schedules(self) -> list[dict]:
        """列出全部已配置调度任务，附最近一次运行完成时间。"""
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT task_key, config_json FROM task WHERE 1=1 ORDER BY name"
            ).fetchall()
        out = []
        for r in rows:
            cfg = json.loads(r["config_json"])
            schedule = (cfg.get("schedule") or "").strip()
            if not schedule:
                continue
            task_key = r["task_key"]
            last_run_at = self._latest_finished_at(task_key)
            out.append({
                "task_key": task_key,
                "name": cfg.get("name", ""),
                "project": cfg.get("project", ""),
                "schedule": schedule,
                "schedule_enabled": bool(cfg.get("schedule_enabled", True)),
                "schedule_next_run": cfg.get("schedule_next_run", ""),
                "last_run_at": last_run_at,
            })
        return out

    def _latest_finished_at(self, task_key: str) -> str:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT finished_at FROM run_record WHERE task_key=? AND finished_at IS NOT NULL "
                "ORDER BY id DESC LIMIT 1", (task_key,)
            ).fetchone()
        return (row["finished_at"] if row else "") or ""

    def update_task_schedule(self, task_key: str, *, schedule_enabled: bool | None = None,
                             schedule_next_run: str | None = None) -> TaskConfig:
        """仅更新当前任务的调度相关字段，不递增版本/不写历史；返回更新后的配置。"""
        cfg = self.get_task(task_key)
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT config_json FROM task WHERE task_key=?", (task_key,)
            ).fetchone()
            payload = json.loads(row["config_json"])
        if schedule_enabled is not None:
            cfg.schedule_enabled = bool(schedule_enabled)
        if schedule_next_run is not None:
            cfg.schedule_next_run = schedule_next_run
        payload.update(cfg.model_dump())
        with self._conn() as c:
            c.execute(
                "UPDATE task SET config_json=? WHERE task_key=?",
                (json.dumps(payload, ensure_ascii=False), task_key),
            )
        return cfg

    # ---- 多用户与角色（任务 7.1） ----

    def upsert_user(self, username: str, role: str, password: str | None = None) -> None:
        if password:
            ph = _password_hash(password)
            with self._conn() as c:
                c.execute(
                    "INSERT INTO user_account(username, role, password_hash, created_at, updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(username) DO UPDATE SET role=excluded.role, password_hash=excluded.password_hash, updated_at=excluded.updated_at",
                    (username, role, ph, _utcnow(), _utcnow()),
                )
            return
        with self._conn() as c:
            c.execute(
                "INSERT INTO user_account(username, role, created_at, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(username) DO UPDATE SET role=excluded.role, updated_at=excluded.updated_at",
                (username, role, _utcnow(), _utcnow()),
            )

    def create_user(self, username: str, role: str, password: str, display_name: str = "") -> dict:
        """新增用户（初始密码必填）。用户名已存在时抛 ValueError。"""
        if self.get_user(username):
            raise ValueError(f"用户 {username} 已存在")
        ph = _password_hash(password)
        now = _utcnow()
        with self._conn() as c:
            c.execute(
                "INSERT INTO user_account(username, role, password_hash, display_name, enabled, created_at, updated_at)"
                " VALUES (?,?,?,?,1,?,?)",
                (username, role, ph, display_name, now, now),
            )
        return self.get_user(username)

    def update_user(self, username: str, *, role: str | None = None,
                    display_name: str | None = None, enabled: bool | None = None) -> dict:
        """局部更新用户（角色/显示名/启用状态）。用户不存在抛 KeyError。"""
        fields, args = [], []
        if role is not None:
            fields.append("role=?"); args.append(role)
        if display_name is not None:
            fields.append("display_name=?"); args.append(display_name)
        if enabled is not None:
            fields.append("enabled=?"); args.append(1 if enabled else 0)
        if not fields:
            return self.get_user(username)
        fields.append("updated_at=?"); args.append(_utcnow())
        args.append(username)
        with self._conn() as c:
            cur = c.execute(f"UPDATE user_account SET {', '.join(fields)} WHERE username=?", args)
            if cur.rowcount == 0:
                raise KeyError(username)
        return self.get_user(username)

    def set_user_password(self, username: str, password: str) -> bool:
        """重置密码；用户不存在返回 False。"""
        ph = _password_hash(password)
        with self._conn() as c:
            cur = c.execute(
                "UPDATE user_account SET password_hash=?, updated_at=? WHERE username=?",
                (ph, _utcnow(), username))
        return cur.rowcount > 0

    def delete_user(self, username: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM user_account WHERE username=?", (username,))
        return cur.rowcount > 0

    def count_enabled_admins(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) AS n FROM user_account WHERE role='admin' AND enabled=1").fetchone()
        return row["n"]

    def verify_credentials(self, username: str, password: str) -> dict | None:
        """校验用户名与密码；被停用（enabled=0）的用户一律拒绝登录。"""
        u = self.get_user(username)
        if not u or not u.get("password_hash"):
            return None
        if not u.get("enabled", 1):
            return None
        if not _password_verify(password, u["password_hash"]):
            return None
        return {k: u[k] for k in ("username", "role")}

    def get_user(self, username: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM user_account WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def list_users(self, q: str | None = None, role: str | None = None) -> list[dict]:
        sql = "SELECT username, role, display_name, enabled, created_at, updated_at FROM user_account WHERE 1=1"
        args: list = []
        if q:
            sql += " AND (username LIKE ? OR display_name LIKE ?)"
            like = f"%{q}%"
            args += [like, like]
        if role:
            sql += " AND role=?"
            args.append(role)
        sql += " ORDER BY username"
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # ---- 外部触发 API 凭据（任务 7.4） ----

    def issue_credential(self, cred_id: str, project: str, label: str, token_hash: str) -> None:
        self.log_event(actor="service", project=project, action="credential_issue",
                       detail={"cred_id": cred_id, "label": label})
        with self._conn() as c:
            c.execute(
                "INSERT INTO service_credential(cred_id, project, label, token_hash, created_at, revoked)"
                " VALUES (?,?,?,?,?,0)",
                (cred_id, project, label, token_hash, _utcnow()),
            )

    def get_credential(self, cred_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM service_credential WHERE cred_id=?", (cred_id,)).fetchone()
        return dict(row) if row else None

    def list_credentials(self, project: str | None = None) -> list[dict]:
        sql, args = "SELECT cred_id, project, label, created_at, revoked FROM service_credential", []
        if project:
            sql += " WHERE project=?"
            args.append(project)
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def revoke_credential(self, cred_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("UPDATE service_credential SET revoked=1 WHERE cred_id=?", (cred_id,))
        if cur.rowcount == 0:
            return False
        row = self.get_credential(cred_id)
        self.log_event(actor="service", project=row["project"] if row else "",
                       action="credential_revoke", detail={"cred_id": cred_id})
        return True

    def verify_credential(self, token_hash: str, project: str) -> dict | None:
        """校验 token_hash 对应凭据：存在、未吊销、项目匹配；返回凭据或 None。"""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM service_credential WHERE token_hash=? AND project=?",
                (token_hash, project),
            ).fetchone()
        if not row or row["revoked"]:
            return None
        return dict(row)

    # ---- LLM 配置（决策 1：api_key 密文落库，查询仅返回掩码） ----

    def get_llm_config_row(self) -> dict | None:
        """读取单行 LLM 配置（含密文；仅内部使用，不得直接对外）。"""
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT provider, base_url, model, api_key_cipher, updated_at, updated_by"
                " FROM llm_config WHERE id=1"
            ).fetchone()
        return dict(row) if row else None

    def save_llm_config(self, provider: str, base_url: str, model: str,
                        api_key: str | None = None, updated_by: str = "") -> dict:
        """保存配置；api_key 为 None/空 表示保持原密文不变，非空则重加密轮换。"""
        now = _utcnow()
        with self._conn() as c:
            existing = c.execute(
                "SELECT api_key_cipher FROM llm_config WHERE id=1"
            ).fetchone()
            if api_key:
                cipher = self.vault.encrypt(api_key)
            elif existing:
                cipher = existing["api_key_cipher"]
            else:
                cipher = ""
            c.execute(
                "INSERT INTO llm_config(id, provider, base_url, model, api_key_cipher, updated_at, updated_by)"
                " VALUES (1,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET provider=excluded.provider, base_url=excluded.base_url,"
                " model=excluded.model, api_key_cipher=excluded.api_key_cipher,"
                " updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (provider, base_url, model, cipher, now, updated_by),
            )
        return self.get_llm_config_row()

    def clear_llm_api_key(self) -> bool:
        """吊销 API Key：清除密文，此后生成/测试均按未配置处理。"""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE llm_config SET api_key_cipher='', updated_at=? WHERE id=1",
                (_utcnow(),),
            )
        return cur.rowcount > 0

    # ---- 外部触发记录（供 8.2 对接 DS 触发；此处先落本地回执） ----

    def record_trigger(self, trigger_id: str, task_key: str, project: str, via: str, actor: str) -> None:
        self.log_event(actor=actor, project=project, action="task_trigger",
                       detail={"trigger_id": trigger_id, "task_key": task_key, "via": via})
        with self._conn() as c:
            c.execute(
                "INSERT INTO trigger_log(trigger_id, task_key, project, fired_at, via, actor)"
                " VALUES (?,?,?,?,?,?)",
                (trigger_id, task_key, project, _utcnow(), via, actor),
            )

    # ---- 主数据管理 MDM（任务 13） ----

    def save_model(self, model: MasterModel) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO mdm_model(entity, label, fields_json) VALUES (?,?,?) "
                "ON CONFLICT(entity) DO UPDATE SET label=excluded.label, fields_json=excluded.fields_json",
                (model.entity, model.label, json.dumps(model.model_dump(), ensure_ascii=False)),
            )

    def get_model(self, entity: str) -> MasterModel | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM mdm_model WHERE entity=?", (entity,)).fetchone()
        if not row:
            return None
        return MasterModel.model_validate(json.loads(row["fields_json"]))

    def list_models(self) -> list[MasterModel]:
        with self._conn() as c:
            rows = c.execute("SELECT entity, fields_json FROM mdm_model ORDER BY entity").fetchall()
        return [MasterModel.model_validate(json.loads(r["fields_json"])) for r in rows]

    def record_change(self, entity: str, record_id: str, action: str, record: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO mdm_change(entity, record_id, action, record_json, ts) VALUES (?,?,?,?,?)",
                (entity, record_id, action, json.dumps(record, ensure_ascii=False), _utcnow()),
            )

    @staticmethod
    def _normalized(record: dict, record_id: str) -> dict:
        """确保记录带 _id 主键字段且不被业务字段覆盖。"""
        rec = dict(record)
        rec["_id"] = record_id
        return rec

    def create_record(self, entity: str, record: dict) -> str:
        record_id = str(record.get("_id") or generate_conn_key())
        now = _utcnow()
        normalized = self._normalized(record, record_id)
        with self._conn() as c:
            c.execute(
                "INSERT INTO mdm_record(entity, record_id, record_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (entity, record_id, json.dumps(normalized, ensure_ascii=False), now, now),
            )
        self.record_change(entity, record_id, "create", normalized)
        return record_id

    def list_records(self, entity: str) -> list[dict]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("SELECT record_id, record_json FROM mdm_record WHERE entity=? ORDER BY updated_at DESC",
                             (entity,)).fetchall()
        return [self._normalized(json.loads(r["record_json"]), r["record_id"]) for r in rows]

    def get_record(self, entity: str, record_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT record_json FROM mdm_record WHERE entity=? AND record_id=?",
                            (entity, record_id)).fetchone()
        return self._normalized(json.loads(row["record_json"]), record_id) if row else None

    def update_record(self, entity: str, record_id: str, record: dict) -> bool:
        normalized = self._normalized(record, record_id)
        with self._conn() as c:
            cur = c.execute(
                "UPDATE mdm_record SET record_json=?, updated_at=? WHERE entity=? AND record_id=?",
                (json.dumps(normalized, ensure_ascii=False), _utcnow(), entity, record_id),
            )
        if cur.rowcount == 0:
            return False
        self.record_change(entity, record_id, "update", normalized)
        return True

    def delete_record(self, entity: str, record_id: str) -> bool:
        rec = self.get_record(entity, record_id)
        with self._conn() as c:
            cur = c.execute("DELETE FROM mdm_record WHERE entity=? AND record_id=?",
                            (entity, record_id))
        if cur.rowcount == 0:
            return False
        self.record_change(entity, record_id, "delete", rec or {})
        return True

    def record_history(self, entity: str, record_id: str) -> list[dict]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT action, record_json, ts FROM mdm_change WHERE entity=? AND record_id=? ORDER BY id DESC",
                (entity, record_id)).fetchall()
        return [dict(r) for r in rows]

    # ---- 运行历史与日志（任务 8.x / 9.1 / 9.2） ----

    def start_run(self, run_id: str, task_key: str, project: str, via: str,
                  attempt: int = 1) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO run_record(run_id, task_key, project, status, trigger_via, started_at, attempt)"
                " VALUES (?,?,?,?,?,?,?)",
                (run_id, task_key, project, "running", via, _utcnow(), attempt))

    def finish_run(self, run_id: str, *, status: str, rows_total: int = 0,
                   error: str | None = None) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE run_record SET status=?, finished_at=?, rows_total=?, error=?"
                " WHERE run_id=?",
                (status, _utcnow(), rows_total, error, run_id))

    @staticmethod
    def _run_filters(project: str | None = None, task_key: str | None = None,
                     status: str | None = None) -> tuple[str, list]:
        """构造 run_record 的 WHERE 子句与参数（分页/筛选统一下推数据库）。"""
        where = " WHERE 1=1"
        args: list = []
        if project:
            where += " AND project=?"
            args.append(project)
        if task_key:
            where += " AND task_key=?"
            args.append(task_key)
        if status:
            where += " AND status=?"
            args.append(status)
        return where, args

    def list_runs(self, project: str | None = None, task_key: str | None = None,
                  limit: int = 100, offset: int = 0,
                  status: str | None = None) -> list[dict]:
        where, args = self._run_filters(project, task_key, status)
        sql = ("SELECT run_id, task_key, project, status, trigger_via, started_at, finished_at,"
               f" rows_total, error, attempt FROM run_record{where}"
               " ORDER BY id DESC LIMIT ? OFFSET ?")
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(sql, [*args, int(limit), int(offset)]).fetchall()
        return [dict(r) for r in rows]

    def run_totals(self, project: str | None = None, task_key: str | None = None,
                   status: str | None = None) -> dict:
        """符合筛选条件的运行条数与同步行数合计（用于分页总数与统计卡）。"""
        where, args = self._run_filters(project, task_key, status)
        with self._conn() as c:
            row = c.execute(
                f"SELECT COUNT(*) AS n, COALESCE(SUM(rows_total), 0) AS rows_sum FROM run_record{where}",
                args).fetchone()
        return {"count": int(row["n"] or 0), "rows_sum": int(row["rows_sum"] or 0)}

    def run_status_counts(self, project: str | None = None,
                          task_key: str | None = None) -> dict[str, int]:
        """按状态分组计数（忽略状态筛选，供分段控件展示各状态总量）。"""
        where, args = self._run_filters(project, task_key)
        with self._conn() as c:
            rows = c.execute(
                f"SELECT status, COUNT(*) AS n FROM run_record{where} GROUP BY status",
                args).fetchall()
        return {str(r["status"]): int(r["n"]) for r in rows}

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT run_id, task_key, project, status, trigger_via, started_at, finished_at,"
                " rows_total, error, attempt FROM run_record WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def append_run_log(self, run_id: str, line: str, level: str = "info") -> None:
        with self._conn() as c:
            c.execute("INSERT INTO run_log(run_id, ts, level, line) VALUES (?,?,?,?)",
                      (run_id, _utcnow(), level, line))

    def list_run_logs(self, run_id: str, limit: int = 500) -> list[dict]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT ts, level, line FROM run_log WHERE run_id=? ORDER BY id ASC LIMIT ?",
                (run_id, int(limit))).fetchall()
        return [dict(r) for r in rows]

    # ---- 指标统计（任务 9.3） ----

    def record_metric(self, run_id: str, *, cpu_percent: float = 0, memory_mb: float = 0,
                      latency_ms: float = 0, duration_s: float = 0, data_rows: int = 0) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO run_metric(run_id, cpu_percent, memory_mb, latency_ms, duration_s, data_rows)"
                " VALUES (?,?,?,?,?,?)",
                (run_id, cpu_percent, memory_mb, latency_ms, duration_s, data_rows))

    def metric_summary(self, task_key: str | None = None, limit: int = 100) -> list[dict]:
        sql = ("SELECT run_id, cpu_percent, memory_mb, latency_ms, duration_s, data_rows"
               " FROM run_metric WHERE 1=1")
        args: list = []
        if task_key:
            sql += " AND run_id IN (SELECT run_id FROM run_record WHERE task_key=?)"
            args.append(task_key)
        sql += f" ORDER BY id DESC LIMIT {int(limit)}"
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # ---- 数据对账（任务 9.4） ----

    def record_reconciliation(self, project: str, task_key: str, *, source_rows: int,
                              target_rows: int, checksum_match: bool,
                              details: dict | None = None) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO reconciliation(project, task_key, source_rows, target_rows,"
                " checksum_match, details_json, created_at) VALUES (?,?,?,?,?,?,?)",
                (project, task_key, source_rows, target_rows, int(checksum_match),
                 json.dumps(details or {}, ensure_ascii=False), _utcnow()))
            return int(cur.lastrowid)

    def list_reconciliations(self, project: str | None = None, limit: int = 100) -> list[dict]:
        sql = ("SELECT id, project, task_key, source_rows, target_rows, checksum_match,"
               " details_json, created_at FROM reconciliation WHERE 1=1")
        args: list = []
        if project:
            sql += " AND project=?"
            args.append(project)
        sql += f" ORDER BY id DESC LIMIT {int(limit)}"
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["checksum_match"] = bool(d["checksum_match"])
            d["details"] = json.loads(d.pop("details_json"))
            out.append(d)
        return out

    # ---- 阈值告警规则与事件（任务 9.5） ----

    def save_alert_rule(self, project: str, task_key: str, metric: str, op: str,
                        threshold: float, notify_to: str = "", enabled: bool = True) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO alert_rule(project, task_key, metric, op, threshold, notify_to, enabled)"
                " VALUES (?,?,?,?,?,?,?)",
                (project, task_key, metric, op, threshold, notify_to, int(enabled)))
            return int(cur.lastrowid)

    def list_alert_rules(self, project: str | None = None) -> list[dict]:
        sql = "SELECT * FROM alert_rule WHERE 1=1"
        args: list = []
        if project:
            sql += " AND project=?"
            args.append(project)
        sql += " ORDER BY id ASC"
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["enabled"] = bool(d["enabled"])
            out.append(d)
        return out

    def delete_alert_rule(self, rule_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM alert_rule WHERE id=?", (rule_id,))
        return cur.rowcount > 0

    def record_alert_event(self, rule: dict, value: float) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO alert_event(rule_id, project, task_key, metric, value, ts) VALUES (?,?,?,?,?,?)",
                (rule["id"], rule["project"], rule["task_key"], rule["metric"], value, _utcnow()))
            return int(cur.lastrowid)

    def set_alert_sent(self, event_id: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE alert_event SET sent=1 WHERE id=?", (event_id,))

    def list_alert_events(self, project: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM alert_event WHERE 1=1"
        args: list = []
        if project:
            sql += " AND project=?"
            args.append(project)
        sql += f" ORDER BY id DESC LIMIT {int(limit)}"
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]