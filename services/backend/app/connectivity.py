"""数据源连通性测试（任务 2.2）。

对常见关系库执行连接测试，返回 (ok: bool, detail: str)。
- 已安装相应驱动的类型：真实建连并可选执行 SELECT 1。
- 未安装驱动的类型：返回明确的失败原因（不抛未定义异常）。

连接串组成自 ConnectionOut.meta（host/port/db/user...）与解密的 secret（密码）。
"""

from __future__ import annotations

from app.models.connection import ConnectionType


class ConnectivityError(RuntimeError):
    """带可读信息的连接失败。"""


def _as_port(value: str | None, default: int) -> int:
    """安全解析端口：非数字/空串回退到默认端口，避免 int() 抛 ValueError 造成 500。"""
    try:
        return int(value) if value else default
    except (TypeError, ValueError):
        return default


def _std(method):
    """生成公有测试函数签名，便于路由复用。"""

    def run(meta: dict[str, str], secret: str | None) -> tuple[bool, str]:
        return method(meta, secret)

    run.__name__ = method.__name__
    return run


def _mysql(meta: dict[str, str], secret: str | None) -> tuple[bool, str]:
    try:
        import pymysql
    except ImportError:
        return False, "驱动 pymysql 未安装"
    params = {
        "host": meta.get("host", "127.0.0.1"),
        "port": _as_port(meta.get("port"), 3306),
        "user": meta.get("user", ""),
        "password": secret or "",
        "database": meta.get("db") or None,
        "connect_timeout": 5,
    }
    try:
        conn = pymysql.connect(**params)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        conn.close()
        return True, "连接成功"
    except Exception as e:  # noqa: BLE001 - 将底层异常转为可读原因
        return False, f"连接失败: {e}"


def _postgresql(meta: dict[str, str], secret: str | None) -> tuple[bool, str]:
    try:
        import psycopg  # type: ignore
    except ImportError:
        return False, "驱动 psycopg 未安装"
    try:
        conn = psycopg.connect(
            host=meta.get("host", "127.0.0.1"),
            port=_as_port(meta.get("port"), 5432),
            user=meta.get("user", ""),
            password=secret or "",
            dbname=meta.get("db"),
            connect_timeout=5,
        )
        conn.close()
        return True, "连接成功"
    except Exception as e:  # noqa: BLE001 - 将底层异常转为可读原因
        return False, f"连接失败: {e}"


def _sqlserver(meta: dict[str, str], secret: str | None) -> tuple[bool, str]:
    try:
        import pyodbc  # type: ignore
    except ImportError:
        return False, "驱动 pyodbc 未安装（需 ODBC Driver for SQL Server）"
    try:
        dsn = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={meta.get('host')},{meta.get('port','1433')};DATABASE={meta.get('db')};UID={meta.get('user')};PWD={secret or ''};TrustServerCertificate=yes"
        conn = pyodbc.connect(dsn, timeout=5)
        conn.close()
        return True, "连接成功"
    except Exception as e:  # noqa: BLE001 - 将底层异常转为可读原因
        return False, f"连接失败: {e}"


def _oracle(meta: dict[str, str], secret: str | None) -> tuple[bool, str]:
    try:
        import oracledb  # type: ignore
    except ImportError:
        return False, "驱动 oracledb 未安装"
    try:
        conn = oracledb.connect(
            user=meta.get("user", ""),
            password=secret or "",
            dsn=f"{meta.get('host')}:{meta.get('port','1521')}/{meta.get('service', meta.get('db'))}",
            timeout=5,
        )
        conn.close()
        return True, "连接成功"
    except Exception as e:  # noqa: BLE001 - 将底层异常转为可读原因
        return False, f"连接失败: {e}"


def _api(meta: dict[str, str], secret: str | None) -> tuple[bool, str]:
    """REST/API 连接：发起一次轻量请求验证可连通（源/目标类型共用）。"""
    from app.sources.rest import fetch_sample

    try:
        fetch_sample(
            meta.get("method", "GET"),
            meta.get("url", ""),
            params={"$limit": "1"},
            secret=secret,
            auth_scheme=meta.get("auth", "none"),
            limit=1,
        )
        return True, "连接成功"
    except Exception as e:  # noqa: BLE001 - 将底层异常转为可读原因
        return False, f"连接失败: {e}"


def _object_storage(meta: dict[str, str], secret: str | None) -> tuple[bool, str]:
    """对象存储（S3 兼容/MinIO）连接：校验已安装驱动并尝试列出 bucket。"""
    try:
        import boto3  # type: ignore
    except ImportError:
        return False, "驱动 boto3 未安装"
    try:
        client = boto3.client(
            "s3",
            endpoint_url=meta.get("endpoint"),
            region_name=meta.get("region", "us-east-1"),
            aws_access_key_id=meta.get("access_key", ""),
            aws_secret_access_key=secret or "",
        )
        client.head_bucket(Bucket=meta.get("bucket", ""))
        return True, "连接成功"
    except Exception as e:  # noqa: BLE001 - 将底层异常转为可读原因
        return False, f"连接失败: {e}"


def _hive(meta: dict[str, str], secret: str | None) -> tuple[bool, str]:
    """Hive 连接：通过 pyhive 尝试建立会话。"""
    try:
        from pyhive import hive  # type: ignore
    except ImportError:
        return False, "驱动 pyhive 未安装（需 PyHive + sasl）"
    try:
        conn = hive.Connection(
            host=meta.get("host", "127.0.0.1"),
            port=_as_port(meta.get("port"), 10000),
            username=meta.get("user", ""),
            password=secret or "",
            database=meta.get("db") or "default",
            timeout=5,
        )
        conn.close()
        return True, "连接成功"
    except Exception as e:  # noqa: BLE001 - 将底层异常转为可读原因
        return False, f"连接失败: {e}"


def _clickhouse(meta: dict[str, str], secret: str | None) -> tuple[bool, str]:
    """ClickHouse 连接：通过 clickhouse_connect 尝试查询 SELECT 1。"""
    try:
        import clickhouse_connect  # type: ignore
    except ImportError:
        return False, "驱动 clickhouse_connect 未安装"
    try:
        client = clickhouse_connect.get_client(
            host=meta.get("host", "127.0.0.1"),
            port=_as_port(meta.get("port"), 8123),
            username=meta.get("user", "default"),
            password=secret or "",
            database=meta.get("db") or "default",
        )
        client.command("SELECT 1")
        return True, "连接成功"
    except Exception as e:  # noqa: BLE001 - 将底层异常转为可读原因
        return False, f"连接失败: {e}"


_HANDLERS: dict[ConnectionType, object] = {
    ConnectionType.MYSQL: _mysql,
    ConnectionType.POSTGRESQL: _postgresql,
    ConnectionType.SQLSERVER: _sqlserver,
    ConnectionType.ORACLE: _oracle,
    ConnectionType.API: _api,
    ConnectionType.OBJECT_STORAGE: _object_storage,
    ConnectionType.HIVE: _hive,
    ConnectionType.CLICKHOUSE: _clickhouse,
}


def check_connection(ctype: ConnectionType, meta: dict[str, str], secret: str | None) -> tuple[bool, str]:
    """执行连通性测试。未知/未实现类型返回明确原因。"""
    handler = _HANDLERS.get(ctype)
    if handler is None:
        return False, f"类型 {ctype.value} 的连通性测试尚未实现"
    return handler(meta, secret)


def target_push_idempotency(ctype: ConnectionType) -> str | None:
    """目标类型的推送幂等说明（任务 2.6）：真正保证幂等才返回说明，否则提示由端点保证。"""
    return {
        ConnectionType.API: "推送幂等由目标端点保证：请确认 API 端支持幂等键，重试时不会产生重复数据。",
        ConnectionType.CSV: "以 CSV 落盘追加，同一任务重跑可能产生重复行，请结合主键去重。",
        ConnectionType.OBJECT_STORAGE: "以对象写入，建议启用版本控制与上传前条件校验以保证重试幂等。",
    }.get(ctype)