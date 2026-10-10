"""元数据目录与血缘存储（任务 10）。

- 表结构字典：connections 的表/列（schema.table.column -> type/nullable/primary）
- 血缘：任务级边（source -> target），字段级与否由 detail 表示；手写路径仅表级。

与 Store 复用同一元数据库：``SYNETRA_METADATA_DB_DSN`` 指向 MySQL 时写入 MySQL，
为空时回退 ``data_dir/synetra.db``。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.db import Database

_SCHEMA = """
CREATE TABLE IF NOT EXISTS table_metadata (
    conn_key TEXT NOT NULL,
    schema_name TEXT NOT NULL DEFAULT '',
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    column_type TEXT NOT NULL,
    nullable INTEGER NOT NULL DEFAULT 1,
    is_primary INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (conn_key, schema_name, table_name, column_name)
);
CREATE TABLE IF NOT EXISTS lineage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    field TEXT,                 -- 非空=字段级；NULL=仅表级
    kind TEXT NOT NULL DEFAULT 'field',
    UNIQUE (task_key, source, target, field)
);
"""

# MySQL 版表结构（与 _SCHEMA 等价）：键列改用 VARCHAR 并控制长度，
# 使 utf8mb4 下联合唯一键（task_key+source+target+field）不超 3072 字节上限。
_SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS table_metadata (
    conn_key VARCHAR(128) NOT NULL,
    schema_name VARCHAR(128) NOT NULL DEFAULT '',
    table_name VARCHAR(128) NOT NULL,
    column_name VARCHAR(128) NOT NULL,
    column_type VARCHAR(255) NOT NULL,
    nullable INT NOT NULL DEFAULT 1,
    is_primary INT NOT NULL DEFAULT 0,
    PRIMARY KEY (conn_key, schema_name, table_name, column_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS lineage (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_key VARCHAR(128) NOT NULL,
    source VARCHAR(255) NOT NULL,
    target VARCHAR(255) NOT NULL,
    field VARCHAR(100) NULL,
    kind VARCHAR(32) NOT NULL DEFAULT 'field',
    UNIQUE KEY uq_lineage (task_key, source, target, field)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# 进程级「建表已完成」标记（键：MySQL DSN 或 SQLite 文件路径）
_INIT_LOCK = threading.Lock()
_INITIALIZED: set[str] = set()


class Catalog:
    def __init__(self, data_dir: str | Path, dsn: str | None = None):
        data_dir = Path(data_dir)
        self.db_path = data_dir / "synetra.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if dsn is None:
            dsn = get_settings().metadata_db_dsn
        self.db = Database(dsn, sqlite_path=self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        """幂等建表；按后端做进程级一次性初始化，避免每次构造重复执行 DDL。"""
        target = f"catalog:{self.db.dsn or f'sqlite:{self.db_path}'}"
        with _INIT_LOCK:
            if target in _INITIALIZED:
                return
            if self.db.is_mysql:
                self.db.ensure_database()
            schema = _SCHEMA_MYSQL if self.db.is_mysql else _SCHEMA
            with self.db.connect() as c:
                c.executescript(schema)
            _INITIALIZED.add(target)

    # ---- 表结构字典（10.1/10.2） ----

    def save_table_schema(self, conn_key: str, table: str, columns: list[dict],
                          schema_name: str = "") -> int:
        with self.db.connect() as c:
            c.execute("DELETE FROM table_metadata WHERE conn_key=? AND schema_name=? AND table_name=?",
                      (conn_key, schema_name, table))
            for col in columns:
                c.execute(
                    "INSERT INTO table_metadata"
                    "(conn_key, schema_name, table_name, column_name, column_type, nullable, is_primary)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (conn_key, schema_name, table, col["name"], col.get("ctype", "TEXT"),
                     int(col.get("nullable", True)), int(col.get("primary", False))),
                )
        return len(columns)

    def list_tables(self, conn_key: str, schema_name: str = "") -> list[dict]:
        with self.db.connect() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT DISTINCT table_name, COUNT(*) AS column_count FROM table_metadata "
                "WHERE conn_key=? AND schema_name=? GROUP BY table_name ORDER BY table_name",
                (conn_key, schema_name),
            ).fetchall()
        return [{"table": r["table_name"], "column_count": r["column_count"]} for r in rows]

    def describe_table(self, conn_key: str, table: str, schema_name: str = "") -> list[dict]:
        with self.db.connect() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT column_name, column_type, nullable, is_primary FROM table_metadata "
                "WHERE conn_key=? AND schema_name=? AND table_name=? ORDER BY is_primary DESC, column_name",
                (conn_key, schema_name, table),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- 血缘（10.3/10.4） ----

    def store_lineage(self, task_key: str, edges: list[dict]) -> int:
        """edges: [{source, target, field|None}]；同一任务重复保存为覆盖。"""
        with self.db.connect() as c:
            c.execute("DELETE FROM lineage WHERE task_key=?", (task_key,))
            for e in edges:
                field = e.get("field")
                kind = "field" if field is not None else "table"
                c.execute(
                    "INSERT OR IGNORE INTO lineage(task_key, source, target, field, kind)"
                    " VALUES (?,?,?,?,?)",
                    (task_key, e["source"], e["target"], field, kind),
                )
        return len(edges)

    def lineage_for_task(self, task_key: str) -> list[dict]:
        with self.db.connect() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT source, target, field, kind FROM lineage WHERE task_key=? ORDER BY id",
                (task_key,),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_lineage(self, source: str | None = None, target: str | None = None) -> list[dict]:
        sql, args = "SELECT task_key, source, target, field, kind FROM lineage WHERE 1=1", []
        if source:
            sql += " AND source=?"
            args.append(source)
        if target:
            sql += " AND target=?"
            args.append(target)
        with self.db.connect() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # ---- 统计（血缘视图用） ----

    def table_lineage_summary(self, task_key: str) -> dict:
        edges = self.lineage_for_task(task_key)
        field_level = bool(edges) and all(e["kind"] == "field" for e in edges)
        return {
            "task_key": task_key,
            "field_level_available": field_level,
            "edges": edges,
            "recorded_at": _utcnow(),
        }


def field_lineage_from_config(cfg: dict) -> list[dict]:
    """从任务配置静态推导字段级血缘（10.3）：source_object.字段 -> target_object.字段。"""
    source = cfg.get("source_object") or "?"
    target = cfg.get("target_object") or "?"
    edges: list[dict] = []
    for f in cfg.get("fields") or []:
        src_field = f.get("source")
        tgt_field = f.get("target") or src_field
        if not src_field or not tgt_field:
            continue
        edges.append({"source": f"{source}.{src_field}", "target": f"{target}.{tgt_field}",
                      "field": tgt_field})
    # 无字段级信息时为表级
    if not edges:
        edges.append({"source": source, "target": target, "field": None})
    return edges


def table_lineage_from_task(cfg: dict) -> list[dict]:
    """手写路径的表级血缘（10.4）：仅表级，字段级不可用。"""
    source = cfg.get("source_object") or "?"
    target = cfg.get("target_object") or "?"
    return [{"source": source, "target": target, "field": None}]