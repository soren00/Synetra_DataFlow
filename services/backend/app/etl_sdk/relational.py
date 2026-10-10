"""关系库抽取与加载（任务 11.1）。

PostgreSQL / Oracle / SQL Server 全量、增量+水位、主键 upsert。
驱动缺失时返回清晰原因；可注入假连接验证语义（无需真机数据库）。

方言差异被封装为各连接器类：extract 返回行列表，load 按 mode 生成 INSERT/ON CONFLICT 或 MERGE。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.models.connection import ConnectionType


class ConnectorError(RuntimeError):
    pass


# ---- 自动建表：目标表缺失时按字段映射/样例值推断 DDL（尽力而为） ----

_DDL_TYPES = {
    "BIGINT": {"mysql": "BIGINT", "postgresql": "BIGINT", "sqlserver": "BIGINT", "oracle": "NUMBER(38)"},
    "INT": {"mysql": "INT", "postgresql": "INTEGER", "sqlserver": "INT", "oracle": "NUMBER(10)"},
    "DOUBLE": {"mysql": "DOUBLE", "postgresql": "DOUBLE PRECISION", "sqlserver": "FLOAT", "oracle": "BINARY_DOUBLE"},
    "BOOL": {"mysql": "TINYINT(1)", "postgresql": "BOOLEAN", "sqlserver": "BIT", "oracle": "NUMBER(1)"},
    "STRING": {"mysql": "VARCHAR(255)", "postgresql": "VARCHAR(255)", "sqlserver": "VARCHAR(255)", "oracle": "VARCHAR2(255)"},
    "TEXT": {"mysql": "TEXT", "postgresql": "TEXT", "sqlserver": "VARCHAR(MAX)", "oracle": "CLOB"},
    "TIMESTAMP": {"mysql": "DATETIME", "postgresql": "TIMESTAMP", "sqlserver": "DATETIME2", "oracle": "TIMESTAMP"},
}

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _base_type(cast_to: str | None, kind: str | None) -> str:
    """字段规则的 cast_to/kind → 通用基础类型（再经 _ddl_type 映射为方言类型）。"""
    if cast_to in ("int",):
        return "BIGINT"
    if cast_to in ("bool", "bool_int"):
        return "BOOL"
    if cast_to == "float":
        return "DOUBLE"
    if cast_to == "str":
        return "STRING"
    if kind in ("expression", "constant"):
        return "TEXT"
    return "STRING"


def _py_value_type(v) -> str:
    """样例值 → 通用基础类型（columns 未覆盖列时的兜底推断）。"""
    if v is None or isinstance(v, str):
        return "STRING"
    if isinstance(v, bool):
        return "BOOL"
    if isinstance(v, int):
        return "BIGINT"
    if isinstance(v, float):
        return "DOUBLE"
    if isinstance(v, (datetime, bytes, bytearray)):
        return "TIMESTAMP"
    return "TEXT"


def _rows(cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description] if cursor.description else []
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


@dataclass
class LoadSummary:
    conn_key: str
    table: str
    mode: str
    rows: int
    watermark_field: str | None = None


class _BaseRelational:
    ctype: ConnectionType = None  # type: ignore

    def __init__(self, meta: dict, secret: str | None):
        self.meta = meta
        self.secret = secret

    def connect(self):
        raise NotImplementedError

    def close(self) -> None:
        pass

    def extract(self, sql: str) -> list[dict]:
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            return _rows(cur)
        except Exception as e:
            raise ConnectorError(f"{self.ctype.value} 抽取失败: {e}") from e
        finally:
            try:
                self.close()
            except Exception:  # noqa: BLE001, S110 - 清理阶段失败不影响主结果
                pass

    def extract_with_params(self, sql: str, params: list) -> list[dict]:
        """执行带位置参数（? 占位）的 SQL 并返回行集。"""
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            return _rows(cur)
        except Exception as e:
            raise ConnectorError(f"{self.ctype.value} 带参抽取失败: {e}") from e
        finally:
            try:
                self.close()
            except Exception:  # noqa: BLE001, S110 - 清理阶段失败不影响主结果
                pass

    def load(self, table: str, rows: list[dict], mode: str,
             primary_keys: list[str], watermark_field: str | None = None,
             columns: list[dict] | None = None) -> LoadSummary:
        conn = self.connect()
        try:
            cur = conn.cursor()
            cols = list(rows[0].keys()) if rows else []
            if rows:
                # 目标表缺失时自动建表（按字段映射列定义 + 样例值兜底推断）
                self._ensure_table(cur, conn, table, columns, primary_keys, rows)
                if mode == "upsert":
                    self._upsert(cur, conn, table, cols, rows, primary_keys)
                else:
                    self._append(cur, conn, table, cols, rows)
                conn.commit()
            return LoadSummary(conn_key=self.ctype.value, table=table, mode=mode,
                               rows=len(rows), watermark_field=watermark_field)
        except Exception as e:
            raise ConnectorError(f"{self.ctype.value} 加载失败: {e}") from e
        finally:
            try:
                self.close()
            except Exception:  # noqa: BLE001, S110 - 清理阶段失败不影响主结果
                pass

    # ---- 自动建表（尽力而为：仅表缺失时 CREATE，已存在则跳过） ----

    def _q(self, ident: str) -> str:
        """标识符引用（默认双引号；MySQL/SQL Server 子类按方言覆盖）。"""
        return f'"{ident}"'

    def _probe_sql(self, table: str) -> str:
        return f"SELECT 1 FROM {self._q(table)} LIMIT 1"

    def _ddl_type(self, base: str) -> str:
        return _DDL_TYPES.get(base, _DDL_TYPES["STRING"]).get(self.ctype.value, "TEXT")

    def _table_exists(self, cur, table: str) -> bool:
        try:
            cur.execute(self._probe_sql(table))
            return True
        except Exception:  # noqa: BLE001 - 探测失败视为表不存在，交由建表决定
            return False

    def _check_ident(self, ident: str, what: str) -> None:
        if not _IDENT_RE.match(ident):
            raise ConnectorError(f"目标{what}名「{ident}」非法，无法自动建表")

    def _create_table(self, cur, conn, table: str, col_defs: list[str], pk_sql: str) -> None:
        ddl = f"CREATE TABLE IF NOT EXISTS {self._q(table)} ({', '.join(col_defs)}{pk_sql})"
        try:
            cur.execute(ddl)
            conn.commit()
        except Exception as e:
            raise ConnectorError(f"{self.ctype.value} 自动建表失败: {e}") from e

    def _ensure_table(self, cur, conn, table: str, columns: list[dict] | None,
                      primary_keys: list[str], rows: list[dict]) -> None:
        self._check_ident(table, "表")
        if self._table_exists(cur, table):
            return
        col_defs: list[str] = []
        seen: set[str] = set()
        for c in columns or []:
            name = (c.get("target") or c.get("source") or "").strip()
            if not name or name in seen:
                continue
            self._check_ident(name, "列")
            seen.add(name)
            col_defs.append(f"{self._q(name)} {self._ddl_type(_base_type(c.get('cast_to'), c.get('kind')))}")
        for row in rows:  # 兜底：columns 未覆盖的列按样例值推断
            for k, v in row.items():
                if not isinstance(k, str) or k in seen:
                    continue
                self._check_ident(k, "列")
                seen.add(k)
                col_defs.append(f"{self._q(k)} {self._ddl_type(_py_value_type(v))}")
        for pk in primary_keys:  # 主键列未在映射中时按 BIGINT 补列
            if pk not in seen:
                self._check_ident(pk, "主键")
                seen.add(pk)
                col_defs.append(f"{self._q(pk)} {self._ddl_type('BIGINT')}")
        if not col_defs:
            raise ConnectorError("目标表不存在且无可推断的列定义，无法自动建表；请先在目标库手动创建目标表")
        pk_sql = f", PRIMARY KEY ({', '.join(self._q(p) for p in primary_keys)})" if primary_keys else ""
        self._create_table(cur, conn, table, col_defs, pk_sql)

    def _append(self, cur, conn, table, cols, rows) -> None:
        raise NotImplementedError

    def _upsert(self, cur, conn, table, cols, rows, primary_keys) -> None:
        raise NotImplementedError


class PostgreSQLConnector(_BaseRelational):
    ctype = ConnectionType.POSTGRESQL

    def connect(self):
        try:
            import psycopg  # type: ignore
        except ImportError:
            raise ConnectorError("驱动 psycopg 未安装")
        return psycopg.connect(
            host=self.meta.get("host", "127.0.0.1"),
            port=int(self.meta.get("port", "5432")),
            user=self.meta.get("user", ""),
            password=self.secret or "",
            dbname=self.meta.get("db"),
            connect_timeout=10)

    def _append(self, cur, conn, table, cols, rows) -> None:
        ph = ",".join(["%s"] * len(cols))
        colsql = ",".join(cols)
        for r in rows:
            cur.execute(f"INSERT INTO {table} ({colsql}) VALUES ({ph})",
                        [r[c] for c in cols])

    def _upsert(self, cur, conn, table, cols, rows, primary_keys) -> None:
        ph = ",".join(["%s"] * len(cols))
        colsql = ",".join(cols)
        update_cols = [c for c in cols if c not in primary_keys]
        update_sql = ",".join(f"{c}=EXCLUDED.{c}" for c in update_cols) or colsql
        for r in rows:
            cur.execute(
                f"INSERT INTO {table} ({colsql}) VALUES ({ph}) "
                f"ON CONFLICT ({','.join(primary_keys)}) DO UPDATE SET {update_sql}",
                [r[c] for c in cols])


class OracleConnector(_BaseRelational):
    ctype = ConnectionType.ORACLE

    def _probe_sql(self, table: str) -> str:
        return f"SELECT 1 FROM {self._q(table)} WHERE ROWNUM = 1"

    def _create_table(self, cur, conn, table: str, col_defs: list[str], pk_sql: str) -> None:
        # Oracle 不支持 CREATE TABLE IF NOT EXISTS：先探测已由调用方做过，
        # 并发下出现 ORA-00955（重名）则视为已存在，静默跳过。
        ddl = f"CREATE TABLE {self._q(table)} ({', '.join(col_defs)}{pk_sql})"
        try:
            cur.execute(ddl)
            conn.commit()
        except Exception as e:
            if "ORA-00955" in str(e):
                return
            raise ConnectorError(f"{self.ctype.value} 自动建表失败: {e}") from e

    def connect(self):
        try:
            import oracledb  # type: ignore
        except ImportError:
            raise ConnectorError("驱动 oracledb 未安装")
        return oracledb.connect(
            user=self.meta.get("user", ""),
            password=self.secret or "",
            dsn=f"{self.meta.get('host')}:{self.meta.get('port', '1521')}/{self.meta.get('service', self.meta.get('db'))}",
            timeout=10)

    def _append(self, cur, conn, table, cols, rows) -> None:
        ph = ",".join([":" + c for c in cols])
        colsql = ",".join(cols)
        for r in rows:
            cur.execute(f"INSERT INTO {table} ({colsql}) VALUES ({ph})",
                        {c: r[c] for c in cols})

    def _upsert(self, cur, conn, table, cols, rows, primary_keys) -> None:
        select_sql = ",".join(f"{c} IN (:p_{c})" for c in primary_keys)
        for r in rows:
            update_cols = [c for c in cols if c not in primary_keys]
            update_sql = ",".join(f"{c}= :nv_{c}" for c in update_cols) or f"{cols[0]}=:nv_{cols[0]}"
            bind = {**{f"nv_{c}": r[c] for c in update_cols},
                    **{f"p_{c}": r[c] for c in primary_keys}}
            cur.execute(f"UPDATE {table} SET {update_sql} WHERE {select_sql}", bind)
            if cur.rowcount == 0:
                ph = ",".join([":" + c for c in cols])
                cur.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})",
                            {c: r[c] for c in cols})


class MySQLConnector(_BaseRelational):
    ctype = ConnectionType.MYSQL

    def _q(self, ident: str) -> str:
        return f"`{ident}`"

    def connect(self):
        try:
            import pymysql  # type: ignore
        except ImportError:
            raise ConnectorError("驱动 pymysql 未安装")
        return pymysql.connect(
            host=self.meta.get("host", "127.0.0.1"),
            port=int(self.meta.get("port", "3306")),
            user=self.meta.get("user", ""),
            password=self.secret or "",
            database=self.meta.get("db"),
            connect_timeout=10,
            charset=self.meta.get("charset", "utf8mb4"))

    def _append(self, cur, conn, table, cols, rows) -> None:
        ph = ",".join(["%s"] * len(cols))
        colsql = ",".join(cols)
        for r in rows:
            cur.execute(f"INSERT INTO {table} ({colsql}) VALUES ({ph})",
                        [r[c] for c in cols])

    def _upsert(self, cur, conn, table, cols, rows, primary_keys) -> None:
        ph = ",".join(["%s"] * len(cols))
        colsql = ",".join(cols)
        update_cols = [c for c in cols if c not in primary_keys]
        update_sql = ",".join(f"{c}=VALUES({c})" for c in update_cols) or colsql
        for r in rows:
            cur.execute(
                f"INSERT INTO {table} ({colsql}) VALUES ({ph}) "
                f"ON DUPLICATE KEY UPDATE {update_sql}",
                [r[c] for c in cols])


class SqlServerConnector(_BaseRelational):
    ctype = ConnectionType.SQLSERVER

    def _q(self, ident: str) -> str:
        return f"[{ident}]"

    def _probe_sql(self, table: str) -> str:
        return f"SELECT TOP 1 1 FROM {self._q(table)}"

    def connect(self):
        try:
            import pyodbc  # type: ignore
        except ImportError:
            raise ConnectorError("驱动 pyodbc 未安装（需 ODBC Driver for SQL Server）")
        dsn = (f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={self.meta.get('host')},"
               f"{self.meta.get('port', '1433')};DATABASE={self.meta.get('db')};"
               f"UID={self.meta.get('user')};PWD={self.secret or ''};TrustServerCertificate=yes")
        return pyodbc.connect(dsn, timeout=10)

    def _append(self, cur, conn, table, cols, rows) -> None:
        ph = ",".join(["?"] * len(cols))
        colsql = ",".join(cols)
        for r in rows:
            cur.execute(f"INSERT INTO {table} ({colsql}) VALUES ({ph})",
                        [r[c] for c in cols])

    def _upsert(self, cur, conn, table, cols, rows, primary_keys) -> None:
        # SQL Server 无标准 ON CONFLICT；以 MERGE 语义实现为逐行 DELETE+INSERT（按主键幂等覆盖）
        ph = ",".join(["?"] * len(cols))
        for r in rows:
            for pk in primary_keys:
                cur.execute(f"DELETE FROM {table} WHERE [{pk}]=?", (r[pk],))
            cur.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})",
                        [r[c] for c in cols])


_RELATIONAL: dict[ConnectionType, type] = {
    ConnectionType.POSTGRESQL: PostgreSQLConnector,
    ConnectionType.ORACLE: OracleConnector,
    ConnectionType.SQLSERVER: SqlServerConnector,
    ConnectionType.MYSQL: MySQLConnector,
}


def get_connector(ctype: ConnectionType, meta: dict, secret: str | None) -> _BaseRelational:
    cls = _RELATIONAL.get(ctype)
    if cls is None:
        raise ConnectorError(f"类型 {getattr(ctype, 'value', ctype)} 不是关系库连接器")
    return cls(meta, secret)


def extract(conn_key_type, meta: dict, secret: str | None, sql: str) -> list[dict]:
    """按类型抽取一行集（任务 11.1 全量；调用方据水位附 WHERE 即为增量）。"""
    conn = get_connector(conn_key_type, meta, secret)
    return conn.extract(sql)


__all__ = [
    "ConnectorError",
    "LoadSummary",
    "MySQLConnector",
    "OracleConnector",
    "PostgreSQLConnector",
    "SqlServerConnector",
    "extract",
    "get_connector",
]