"""把本地 SQLite 元数据库（synetra.db）迁移到 MySQL 元数据库。

适用场景：项目从 SQLite 切换到 MySQL 元数据库（``SYNETRA_METADATA_DB_DSN``）后，
把存量连接/任务/历史/运行记录/血缘/审计等一次性搬过去，避免切换即「空库」。

特性：
- 表结构与列名两库一致，按 SQLite 实际列逐表复制（跳过 sqlite_sequence 等内部表）；
- 以源库为准（``ON DUPLICATE KEY UPDATE``）：可重复执行，冲突行按源库值覆盖；
- 只读源库，不改动也不删除源文件；建议迁移前先备份 synetra.db 与 vault.key。

用法：
    cd services/backend
    python scripts/migrate_sqlite_to_mysql.py                  # 用配置里的 DSN 与 data_dir
    python scripts/migrate_sqlite_to_mysql.py --sqlite path/to/synetra.db
    python scripts/migrate_sqlite_to_mysql.py --dry-run        # 只统计不写入

注意：secret / api_key_cipher 是 Fernet 密文，解密依赖 data_dir/vault.key，
迁移后请保证运行时仍使用同一把 vault 密钥（同一 SYNETRA_DATA_DIR 或 SYNETRA_VAULT_KEY）。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db import Database
from app.store import _SCHEMA_MYSQL

# SQLite 内部表，不迁移
_IGNORED_TABLES = {"sqlite_sequence"}


def _sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return sorted(n for (n,) in rows if n not in _IGNORED_TABLES)


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def migrate(sqlite_path: Path, dsn: str, dry_run: bool = False) -> int:
    if not sqlite_path.exists():
        print(f"[x] 源库不存在: {sqlite_path}")
        return 2

    db = Database(dsn)
    if not db.is_mysql:
        print("[x] 目标 DSN 未指向 MySQL，无需迁移。请在 .env 配置 SYNETRA_METADATA_DB_DSN。")
        return 2

    db.ensure_database()
    with db.connect() as target:
        # 幂等建表，保证目标库结构与源库一致
        target.executescript(_SCHEMA_MYSQL)

        source = sqlite3.connect(str(sqlite_path))
        source.row_factory = sqlite3.Row
        try:
            total_in = total_ok = 0
            for table in _sqlite_tables(source):
                cols = _sqlite_columns(source, table)
                if not cols:
                    continue
                rows = source.execute(f'SELECT * FROM "{table}"').fetchall()
                if not rows:
                    continue
                placeholders = ",".join("?" for _ in cols)
                col_sql = ",".join(f"`{c}`" for c in cols)
                # 以源库为准：冲突时用源值覆盖（幂等可重跑，重复执行结果一致）
                update_sql = ",".join(f"`{c}`=VALUES(`{c}`)" for c in cols)
                sql = (
                    f"INSERT INTO `{table}` ({col_sql}) VALUES ({placeholders})"
                    f" ON DUPLICATE KEY UPDATE {update_sql}"
                )
                inserted = 0
                for row in rows:
                    total_in += 1
                    if dry_run:
                        continue
                    cur = target.execute(sql, [row[c] for c in cols])
                    inserted += 1 if cur.rowcount > 0 else 0
                total_ok += inserted
                print(f"  {table:20s} 源 {len(rows):5d} 行 -> 写入 {inserted:5d} 行")
            print(
                f"[{'dry-run' if dry_run else 'ok'}] 源共 {total_in} 行，"
                f"实际写入 {total_ok} 行（冲突行按源库覆盖）"
            )
        finally:
            source.close()
    return 0


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="SQLite -> MySQL 元数据库迁移")
    parser.add_argument(
        "--sqlite",
        default=str(Path(settings.data_dir) / "synetra.db"),
        help="源 SQLite 元数据库路径（默认 data_dir/synetra.db）",
    )
    parser.add_argument("--dsn", default=settings.metadata_db_dsn, help="目标 MySQL DSN")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写入")
    args = parser.parse_args()

    print(f"源: {args.sqlite}")
    print(f"目标: {args.dsn}")
    return migrate(Path(args.sqlite), args.dsn, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
