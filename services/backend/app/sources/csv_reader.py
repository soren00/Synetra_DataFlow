"""CSV 数据源：读取表结构（列名 + 推断类型 + 行数 + 预览）。

任务 2.4：指向 CSV 后可读取表结构。
"""

from __future__ import annotations

import csv
import os


def _infer_type(sample: list[str]) -> str:
    """根据列样本推断类型：全数字 -> number；全布尔 -> boolean；否则 string。"""
    s = [(v or "").strip() for v in sample]
    s = [v for v in s if v]
    if not s:
        return "unknown"
    if all(_is_number(v) for v in s):
        return "number"
    if all(_is_boolean(v) for v in s):
        return "boolean"
    return "string"


def _is_number(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def _is_boolean(v: str) -> bool:
    return v.lower() in {"true", "false", "y", "n"}


def read_csv_structure(path: str, n_sample: int = 20, max_char: int = 512) -> dict:
    """读取 CSV 表结构。

    path 为本地文件或 http(s) URL（http 用 url 惰性读取）。
    返回：文件名、列[{name,type}]、数据行数、前几行预览。
    """
    if not os.path.exists(path) and not path.startswith(("http://", "https://")):
        raise FileNotFoundError(f"CSV 文件不存在: {path}")

    if path.startswith(("http://", "https://")):
        import io
        import urllib.request

        with urllib.request.urlopen(path, timeout=10) as r:
            text = r.read(max_char > 0 and 1024000 or None).decode("utf-8-sig", "replace")
        fh = io.StringIO(text)
    else:
        fh = open(path, "r", encoding="utf-8-sig")  # noqa: SIM115 - 生命周期受 with 约束

    with fh:
        sample = fh.read(max_char or 0)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample[:4096])
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(fh, dialect))
    header = rows[0] if rows else []
    data_rows = rows[1:]
    total = len(data_rows)
    sampled = data_rows[:n_sample]

    columns = []
    for i, name in enumerate(header):
        col_vals = [r[i] for r in sampled if i < len(r)]
        columns.append({"name": name.strip(), "type": _infer_type(col_vals)})

    return {
        "path": path,
        "header_count": len(header),
        "row_count": total,
        "columns": columns,
        "preview": data_rows[:5],
    }