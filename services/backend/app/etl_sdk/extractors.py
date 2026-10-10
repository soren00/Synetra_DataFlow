"""多源抽取器（任务 14.1 / 14.2）。

- CSV extractor：按配置读取 CSV 文件，返回行集。
- REST/API extractor：认证、分页/参数、拉取，返回行集。

提供 pull(front) 统一入口；驱动缺文件/缺依赖返回清晰原因。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass

from app.sources.rest import request_json


class ExtractError(RuntimeError):
    pass


@dataclass
class ExtractResult:
    conn_key: str
    kind: str
    rows: list[dict]
    total: int


class CsvExtractor:
    def __init__(self, meta: dict):
        self.meta = meta

    def pull(self) -> ExtractResult:
        path = self.meta.get("path", "")
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = [dict(r) for r in reader]
        except FileNotFoundError:
            raise ExtractError(f"CSV 文件不存在: {path}")
        except Exception as e:
            raise ExtractError(f"CSV 读取失败: {e}") from e
        return ExtractResult(conn_key="csv", kind="csv_extract",
                             rows=rows, total=len(rows))


class RestExtractor:
    def __init__(self, meta: dict, secret: str | None, *, pages: int = 1,
                 timeout: float = 30.0):
        self.meta = meta
        self.secret = secret
        self.pages = pages
        self.timeout = timeout

    def pull(self) -> ExtractResult:
        url = self.meta.get("url", "")
        method = self.meta.get("method", "GET")
        auth = self.meta.get("auth", "none")
        page_param = self.meta.get("page_param", "page")
        size_param = self.meta.get("size_param", "size")
        size = self.meta.get("page_size", 100)
        rows: list[dict] = []
        for page in range(1, self.pages + 1):
            params = {page_param: str(page), size_param: str(size)}
            try:
                data = request_json(method, url, params=params, secret=self.secret,
                                    auth_scheme=auth, timeout=self.timeout)
            except Exception as e:
                raise ExtractError(f"API 拉取失败: {e}") from e
            batch = data.get("items", data) if isinstance(data, dict) and "items" in data else data
            if isinstance(batch, dict):
                batch = [batch]
            rows.extend(list(batch))
            if not batch:
                break
        return ExtractResult(conn_key="rest", kind="rest_extract",
                             rows=rows, total=len(rows))


def pull(conn_key_type, meta: dict, secret: str | None = None, **kw) -> ExtractResult:
    kind = conn_key_type.value if hasattr(conn_key_type, "value") else str(conn_key_type)
    if kind in ("csv", "file"):
        return CsvExtractor(meta).pull()
    if kind in ("api", "rest"):
        return RestExtractor(meta, secret, pages=int(kw.get("pages", 1)),
                             timeout=float(kw.get("timeout", 30))).pull()
    raise ExtractError(f"不支持的源类型: {kind}")


__all__ = ["CsvExtractor", "ExtractError", "ExtractResult", "RestExtractor", "pull"]