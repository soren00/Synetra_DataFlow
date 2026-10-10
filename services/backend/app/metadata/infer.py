"""元数据发现与字段推断（任务 10.1/10.2）。"""

from __future__ import annotations


def infer_columns_from_rows(rows: list[dict]) -> list[dict]:
    """从 REST/API 样例或任意行集推断字段结构与类型（10.2）。"""
    if not rows:
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    columns: list[dict] = []
    for k in keys:
        sample_values = [r.get(k) for r in rows if r.get(k) is not None]
        ctype = _infer_type(sample_values)
        columns.append({"name": k, "ctype": ctype, "nullable": True})
    return columns


def _infer_type(values: list) -> str:
    if not values:
        return "string"
    numerics = 0
    bool_like = 0
    for v in values:
        if isinstance(v, bool):
            bool_like += 1
        elif isinstance(v, (int, float)):
            numerics += 1
        elif isinstance(v, str):
            if v.strip().lower() in ("true", "false"):
                bool_like += 1
            else:
                try:
                    float(v)
                    numerics += 1
                except ValueError:
                    pass
        else:
            return "object"
    if bool_like == len(values):
        return "boolean"
    if numerics == len(values):
        return "number"
    if any(isinstance(v, (int, float)) for v in values):
        return "mixed"
    return "string"