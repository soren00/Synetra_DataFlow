"""dry-run 期注入的轻量 etl_sdk 运行时（任务 5.3/5.4）。

当以 dry-run 执行生成/手写脚本时，把本运行时以 `etl_sdk` 模块注入被执行的脚本命名空间，
并置 `SYNETRA_DRY_RUN=1`：`writers` 只记录"本应写入"的统计，绝不发生真实目标写入。
本文件仅作验证语义的 stub，真实抽取/加载由 etl_sdk（任务 11/14）提供。
"""

from __future__ import annotations

import json
import os
from typing import ClassVar

from app.core.config import get_settings
from app.etl_sdk.relational import get_connector
from app.store import Store


def _is_dry_run() -> bool:
    return os.environ.get("SYNETRA_DRY_RUN") == "1"


class readers:
    """抽取接口（dry-run 内从 SYNETRA_SAMPLE_JSON 返回样例行）。"""

    @staticmethod
    def query(conn_key: str, sql: str, params: dict | None = None) -> list[dict]:
        if _is_dry_run():
            raw = os.environ.get("SYNETRA_SAMPLE_JSON", "[]")
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                return []
            return parsed if isinstance(parsed, list) else []
        # 真实运行期：按 conn_key 从 store 取连接配置与解密凭证，连源库执行 SQL
        store = Store(get_settings().data_dir)
        conn = store.get_connection(conn_key)
        secret = None
        try:
            secret = store.get_secret(conn_key).decode("utf-8")
        except KeyError:
            secret = None
        connector = get_connector(conn.ctype, conn.meta, secret)
        if params:
            sql_std = sql.replace(":wm", "?")
            return connector.extract_with_params(sql_std, list(params.values()))
        return connector.extract(sql)


class transforms:
    @staticmethod
    def apply(src_rows, config):
        # 契约占位；生成脚本通常直接内联转换逻辑
        return src_rows


class writers:
    """加载接口：dry-run 下仅累计统计，不写目标。"""

    # dry-run 下仅累计统计，不写目标；每次新执行由 new_module() 重置
    _dry_run_report: ClassVar[list[dict]] = []

    @classmethod
    def _reset(cls) -> None:
        cls._dry_run_report = []

    @classmethod
    def report(cls) -> list[dict]:
        return list(cls._dry_run_report)

    @classmethod
    def relational(cls, conn_key: str, table: str, mode: str = "append", primary_keys=None):
        return _RelationalWriter(cls, conn_key=conn_key, table=table, mode=mode,
                                 primary_keys=primary_keys or [])

    @classmethod
    def api(cls, conn_key: str, path: str):
        return _ApiWriter(cls, conn_key=conn_key, path=path)


class _BaseWriter:
    def __init__(self, registry, conn_key: str):
        self._registry = registry
        self.conn_key = conn_key

    def _record(self, rows, kind: str, **extra) -> dict:
        rec = {"kind": kind, "conn_key": self.conn_key, "rows": len(rows), **extra}
        self._registry._dry_run_report.append(rec)
        return rec


class _RelationalWriter(_BaseWriter):
    def __init__(self, registry, conn_key: str, table: str, mode: str, primary_keys: list):
        super().__init__(registry, conn_key)
        self.table = table
        self.mode = mode
        self.primary_keys = primary_keys

    def buffered(self, rows, watermark_field: str | None = None, columns: list | None = None):
        rec = self._record(rows, kind="relational_buffered", table=self.table,
                           mode=self.mode, primary_keys=self.primary_keys)
        if watermark_field:
            rec["watermark_field"] = watermark_field
        if _is_dry_run():
            return {"dry_run": True, "records": len(rows)}
        # 真实运行期：按 conn_key 取目标连接配置+解密凭证，走关系库加载
        summary = _real_load(self.conn_key, self.table, self.mode,
                             self.primary_keys, rows, watermark_field, columns)
        rec["rows"] = summary.rows
        return {"dry_run": False, "records": summary.rows}


class _ApiWriter(_BaseWriter):
    def __init__(self, registry, conn_key: str, path: str):
        super().__init__(registry, conn_key)
        self.path = path

    def push(self, rows):
        rec = self._record(rows, kind="api_push", path=self.path)
        rec["idempotency_note"] = "幂等由端点保证"
        if _is_dry_run():
            return {"dry_run": True, "records": len(rows)}
        raise NotImplementedError("真实 API 推送由 etl_sdk 提供（任务 14.3）")


def new_module() -> dict:
    """构造可注入被审计脚本命名空间的 etl_sdk 模块 dict。"""
    writers._reset()
    return {"readers": readers, "transforms": transforms, "writers": writers}


def _real_load(conn_key: str, table: str, mode: str,
               primary_keys: list[str], rows: list[dict],
               watermark_field: str | None = None,
               columns: list | None = None):
    """真实运行期：按 conn_key 解析目标连接配置与凭证并写入目标表。"""
    store = Store(get_settings().data_dir)
    conn = store.get_connection(conn_key)
    secret = None
    try:
        secret = store.get_secret(conn_key).decode("utf-8")
    except KeyError:
        secret = None
    connector = get_connector(conn.ctype, conn.meta, secret)
    return connector.load(table, rows, mode, primary_keys, watermark_field, columns)