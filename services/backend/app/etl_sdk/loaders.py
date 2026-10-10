"""多目标加载器（任务 14.3 / 14.4）。

- API loader：POST 推送，支持认证、超时、重试；幂等由端点保证（文档化）。
- 对象存储 loader：把行集写成 CSV 对象上传（S3/MinIO）。
- Hive loader：INSERT 落表。
- ClickHouse loader：本地缓冲写入。

驱动缺失返回清晰原因；可注入假客户端验证调用语义。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from app.sources.rest import request_json


class LoadError(RuntimeError):
    pass


@dataclass
class LoadResult:
    conn_key: str
    kind: str
    rows: int
    note: str


class ApiLoader:
    """API 目标：推送行集到端点。幂等由端点保证，失败不重试默认，可配置重试。"""

    def __init__(self, meta: dict, secret: str | None, *, retries: int = 0,
                 timeout: float = 30.0):
        self.meta = meta
        self.secret = secret
        self.retries = retries
        self.timeout = timeout

    def push(self, rows: list[dict]) -> LoadResult:
        url = self.meta.get("url", "")
        method = self.meta.get("method", "POST")
        auth = self.meta.get("auth", "none")
        payload = {"items": rows} if self.meta.get("wrap_array") else rows
        attempt = 0
        last_err = None
        while attempt <= self.retries:
            attempt += 1
            try:
                request_json(method, url, json_body=payload, secret=self.secret,
                             auth_scheme=auth, timeout=self.timeout)
                return LoadResult(conn_key="api", kind="api_push",
                                  rows=len(rows),
                                  note="推送成功（幂等由端点保证）")
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt > self.retries:
                    break
        raise LoadError(f"API 推送失败: {last_err}")


class ObjectStorageLoader:
    """对象存储目标：将行集写为 CSV 并上传。"""

    def __init__(self, meta: dict, secret: str | None):
        self.meta = meta
        self.secret = secret

    def push(self, rows: list[dict], key: str) -> LoadResult:
        try:
            import boto3  # type: ignore
        except ImportError:
            raise LoadError("驱动 boto3 未安装")
        buf = io.StringIO()
        cols = list(rows[0].keys()) if rows else []
        writer = csv.DictWriter(buf, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)
        client = boto3.client(
            "s3",
            endpoint_url=self.meta.get("endpoint"),
            region_name=self.meta.get("region", "us-east-1"),
            aws_access_key_id=self.meta.get("access_key", ""),
            aws_secret_access_key=self.secret or "",
        )
        try:
            client.put_object(Bucket=self.meta.get("bucket", ""), Key=key,
                              Body=buf.getvalue().encode("utf-8"))
        except Exception as e:
            raise LoadError(f"对象存储写入失败: {e}") from e
        return LoadResult(conn_key="object_storage", kind="object_upload",
                          rows=len(rows), note=f"以 CSV 上传 {key}")


class HiveLoader:
    def __init__(self, meta: dict, secret: str | None):
        self.meta = meta
        self.secret = secret

    def push(self, table: str, rows: list[dict]) -> LoadResult:
        try:
            from pyhive import hive  # type: ignore
        except ImportError:
            raise LoadError("驱动 pyhive 未安装（需 PyHive + sasl）")
        conn = hive.Connection(host=self.meta.get("host", "127.0.0.1"),
                               port=int(self.meta.get("port", "10000")),
                               username=self.meta.get("user", ""),
                               password=self.secret or "",
                               database=self.meta.get("db") or "default")
        try:
            cur = conn.cursor()
            cols = list(rows[0].keys()) if rows else []
            for r in rows:
                cur.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})",
                            [r[c] for c in cols])
            conn.commit()
        finally:
            conn.close()
        return LoadResult(conn_key="hive", kind="hive_insert", rows=len(rows),
                          note=f"写入 Hive 表 {table}")


class ClickHouseLoader:
    def __init__(self, meta: dict, secret: str | None):
        self.meta = meta
        self.secret = secret

    def push(self, table: str, rows: list[dict]) -> LoadResult:
        try:
            import clickhouse_connect  # type: ignore
        except ImportError:
            raise LoadError("驱动 clickhouse_connect 未安装")
        client = clickhouse_connect.get_client(
            host=self.meta.get("host", "127.0.0.1"),
            port=int(self.meta.get("port", "8123")),
            username=self.meta.get("user", "default"),
            password=self.secret or "",
            database=self.meta.get("db") or "default")
        try:
            client.insert(table, [list(r.values()) for r in rows],
                          column_names=list(rows[0].keys()) if rows else None)
        except Exception as e:
            raise LoadError(f"ClickHouse 写入失败: {e}") from e
        return LoadResult(conn_key="clickhouse", kind="clickhouse_insert",
                          rows=len(rows), note=f"写入 ClickHouse 表 {table}")


def load(conn_key_type, meta: dict, secret: str | None,
         rows: list[dict], *, object_key: str | None = None,
         table: str | None = None, retries: int = 0) -> LoadResult:
    ctype = conn_key_type
    kind = ctype.value if hasattr(ctype, "value") else str(ctype)
    if kind in ("api", "rest"):
        return ApiLoader(meta, secret, retries=retries).push(rows)
    if kind in ("object_storage", "s3"):
        return ObjectStorageLoader(meta, secret).push(rows, key=object_key or "stream.csv")
    if kind in ("hive",):
        return HiveLoader(meta, secret).push(table or "default_table", rows)
    if kind in ("clickhouse",):
        return ClickHouseLoader(meta, secret).push(table or "default_table", rows)
    raise LoadError(f"不支持的目标类型: {kind}")


__all__ = [
    "ApiLoader",
    "ClickHouseLoader",
    "HiveLoader",
    "LoadError",
    "LoadResult",
    "ObjectStorageLoader",
    "load",
]