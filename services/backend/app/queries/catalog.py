"""关系库库表与视图实时枚举（只读查询工作台导航）。

- 直接读取目标库的系统目录（MySQL `information_schema.TABLES` /
  PostgreSQL `information_schema.tables`）；不复用本地元数据字典
  （`app.metadata.catalog` 可能未同步或已过期，不可作为导航依据）；
- 仅返回 schema / 名称 / 类型等轻量列，并设结果条数上限；
- 同时按方言生成基础只读查询语句（schema 限定 + 标识符引用 + 行数上限），
  供前端「点击表名预填」直接使用，避免前端出现第二套引用规则。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.connection import ConnectionType

# 单次枚举返回的对象条数上限（超出截断并标记 truncated）
MAX_OBJECTS = 5000
# 预填语句的默认行数上限
SNIPPET_LIMIT = 100
_CONNECT_TIMEOUT = 5

# PostgreSQL 需排除的系统 schema
_SYSTEM_SCHEMAS: frozenset[str] = frozenset({"pg_catalog", "information_schema", "pg_toast"})


class CatalogNotSupported(Exception):
    """连接类型不支持实时库表枚举。"""


@dataclass
class CatalogResult:
    default_schema: str
    schemas: list[str] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    truncated: bool = False


def quote_ident(ctype: ConnectionType, ident: str) -> str:
    """按目标库方言引用标识符（MySQL 反引号 / PostgreSQL 双引号）。"""
    if ctype is ConnectionType.MYSQL:
        return "`" + ident.replace("`", "``") + "`"
    return '"' + ident.replace('"', '""') + '"'


def build_snippet(ctype: ConnectionType, schema: str, name: str) -> str:
    """生成基础只读查询语句（schema 限定 + 行数上限），可直接提交执行。"""
    if schema:
        target = f"{quote_ident(ctype, schema)}.{quote_ident(ctype, name)}"
    else:
        target = quote_ident(ctype, name)
    return f"SELECT * FROM {target} LIMIT {SNIPPET_LIMIT};"


def _kind_of(table_type: str) -> str:
    return "view" if (table_type or "").upper().endswith("VIEW") else "table"


def _is_system_schema(name: str) -> bool:
    return name in _SYSTEM_SCHEMAS or name.startswith("pg_")


def list_objects(
    ctype: ConnectionType,
    meta: dict[str, str],
    secret: str | None,
    schema: str | None = None,
    kind: str = "all",
    q: str = "",
) -> CatalogResult:
    """枚举目标库的 schema 与表/视图清单；`kind` 仅取 table/view，`q` 按名称过滤。"""
    if ctype not in (ConnectionType.MYSQL, ConnectionType.POSTGRESQL):
        raise CatalogNotSupported(f"不支持的库表枚举类型: {ctype.value}")

    if ctype is ConnectionType.MYSQL:
        default_schema, schemas, rows = _fetch_mysql(meta, secret, schema, meta.get("db") or "")
    else:
        default_schema, schemas, rows = _fetch_postgres(meta, secret, schema)

    truncated = len(rows) > MAX_OBJECTS
    rows = rows[:MAX_OBJECTS]

    items: list[dict] = []
    for row_schema, row_name, row_type in rows:
        items.append({
            "schema": row_schema,
            "name": row_name,
            "kind": _kind_of(row_type),
            "snippet": build_snippet(ctype, row_schema, row_name),
        })

    if kind in ("table", "view"):
        items = [it for it in items if it["kind"] == kind]

    keyword = (q or "").strip().lower()
    if keyword:
        items = [it for it in items if keyword in it["name"].lower()]

    return CatalogResult(
        default_schema=default_schema,
        schemas=schemas,
        items=items,
        truncated=truncated,
    )


def list_schemas(ctype: ConnectionType, meta: dict[str, str], secret: str | None) -> list[str]:
    """仅取连接可选的 schema 清单（复用 list_objects，保证与枚举口径一致）。"""
    return list_objects(ctype, meta, secret).schemas


def _fetch_mysql(
    meta: dict[str, str], secret: str | None, schema: str | None, default_db: str,
) -> tuple[str, list[str], list[tuple]]:
    import pymysql

    target = schema or default_db
    if not target:
        raise CatalogNotSupported("MySQL 连接缺少库名（meta.db），无法枚举表与视图")

    conn = pymysql.connect(
        host=meta.get("host", "127.0.0.1"),
        port=int(meta.get("port", "3306")),
        user=meta.get("user", ""),
        password=secret or "",
        database=default_db or None,
        connect_timeout=_CONNECT_TIMEOUT,
        read_timeout=_CONNECT_TIMEOUT,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES"
                " WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME LIMIT %s",
                (target, MAX_OBJECTS + 1),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return target, [target], rows


def _fetch_postgres(
    meta: dict[str, str], secret: str | None, schema: str | None,
) -> tuple[str, list[str], list[tuple]]:
    import psycopg  # type: ignore

    conn = psycopg.connect(
        host=meta.get("host", "127.0.0.1"),
        port=int(meta.get("port", "5432")),
        user=meta.get("user", ""),
        password=secret or "",
        dbname=meta.get("db"),
        connect_timeout=_CONNECT_TIMEOUT,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT table_schema FROM information_schema.tables"
                " ORDER BY table_schema"
            )
            schemas = [r[0] for r in cur.fetchall() if not _is_system_schema(r[0])]

            if schema:
                target = schema
            elif "public" in schemas:
                target = "public"
            else:
                target = schemas[0] if schemas else "public"

            cur.execute(
                "SELECT table_schema, table_name, table_type FROM information_schema.tables"
                " WHERE table_schema = %s ORDER BY table_name LIMIT %s",
                (target, MAX_OBJECTS + 1),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return target, schemas, rows
