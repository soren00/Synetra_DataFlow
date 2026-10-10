"""只读查询执行（任务 3.1）。

- guard 通过后才执行；
- 强制行数上限与超时（MySQL 用 MAX_EXECUTION_TIME 毫秒级限制）；
- 分页预览：把原始只读 SQL 包为子查询并 LIMIT/OFFSET，物理限制返回行数。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.connection import ConnectionType
from app.queries.guard import validate_read_only


class QueryRejected(Exception):
    pass


class QueryNotImplemented(Exception):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    count: int
    truncated: bool


def run_select(
    ctype: ConnectionType,
    meta: dict[str, str],
    secret: str | None,
    sql: str,
    limit: int = 200,
    offset: int = 0,
    timeout_ms: int = 5000,
) -> QueryResult:
    """校验 + 执行只读查询，返回限制内的列/行。"""
    bad = validate_read_only(sql)
    if bad:
        raise QueryRejected("; ".join(bad))

    # 去除尾部分号与空白，避免包装成子查询后残留分号导致语法错误
    sql = sql.rstrip().rstrip(";").rstrip()

    if ctype not in (ConnectionType.MYSQL, ConnectionType.POSTGRESQL):
        raise QueryNotImplemented(f"不支持的只读查询类型: {ctype.value}")

    if limit > 1000 or limit < 1:
        raise QueryRejected("limit 须在 1..1000")

    if ctype is ConnectionType.MYSQL:
        return _run_mysql(meta, secret, sql, limit, offset, timeout_ms)
    return _run_postgres(meta, secret, sql, limit, offset, timeout_ms)


def _run_mysql(meta, secret, sql, limit, offset, timeout_ms) -> QueryResult:
    import pymysql

    conn = pymysql.connect(
        host=meta.get("host", "127.0.0.1"),
        port=int(meta.get("port", "3306")),
        user=meta.get("user", ""),
        password=secret or "",
        database=meta.get("db") or None,
        connect_timeout=5,
        read_timeout=max(timeout_ms / 1000, 1),
    )
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # 语句级超时（毫秒）
            cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_ms)}")
            # 分页包装 + 显式上限，物理限制返回行数
            wrapped = f"SELECT * FROM (\n{sql}\n) AS __synetra_preview__ LIMIT %s OFFSET %s"
            cur.execute(wrapped, (int(limit), int(offset)))
            rows = cur.fetchall()
            # 还原会话超时
            cur.execute("SET SESSION MAX_EXECUTION_TIME = 0")
    finally:
        conn.close()
    columns = list(rows[0].keys()) if rows else []
    data = [[r.get(c) for c in columns] for r in rows]
    return QueryResult(
        columns=columns,
        rows=data,
        count=len(rows),
        truncated=len(rows) == int(limit),
    )


def _run_postgres(meta, secret, sql, limit, offset, timeout_ms) -> QueryResult:
    import psycopg  # type: ignore

    conn = psycopg.connect(
        host=meta.get("host", "127.0.0.1"),
        port=int(meta.get("port", "5432")),
        user=meta.get("user", ""),
        password=secret or "",
        dbname=meta.get("db"),
        connect_timeout=5,
    )
    try:
        wrapped = f"SELECT * FROM (\n{sql}\n) AS __synetra_preview__ LIMIT %s OFFSET %s"
        with conn.cursor() as cur:
            cur.execute(wrapped, (int(limit), int(offset)))
            cols = [d.name for d in cur.description] if cur.description else []
            rows = [list(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return QueryResult(columns=cols, rows=rows, count=len(rows), truncated=len(rows) == int(limit))