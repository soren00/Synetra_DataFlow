"""合并节点的列对齐与类型推导（画布核对用）。

推导方式：让每条入边按正常执行路径产出至多 `limit` 行（源端分批读取后即截断，
不会全表扫描），再用与运行时同一套类型提升规则（bool < int < float < str）计算
合并后的结果列。因此「画布上看到的对齐结果」与「运行时实际合并结果」不会漂移。
"""

from __future__ import annotations

from typing import Any

from app.etl_sdk.pipeline.engine import PipelineRunError, _kind, run_pipeline
from app.models.pipeline import PipelineConfig, StepType

_KIND_CN = {0: "bool", 1: "int", 2: "float", 3: "str"}


def _columns_of(rows: list[dict]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        for key in row:
            if key not in seen:
                seen.append(key)
    return seen


def _promote(rows: list[dict], columns: list[str]) -> dict[str, str]:
    kinds: dict[str, int] = {}
    for row in rows:
        for col in columns:
            value = row.get(col)
            if value is None:
                continue
            kinds[col] = max(kinds.get(col, 0), _kind(value))
    return {col: _KIND_CN[kinds.get(col, 3)] for col in columns}


def derive_union_schema(
    pipe: PipelineConfig,
    node_id: str,
    *,
    sample: list[dict] | None = None,
    store: Any = None,
    connector_factory: Any = None,
    task_config: dict | None = None,
    limit: int = 200,
) -> dict:
    """推导合并节点的入边列集合与对齐结果。

    返回：
      - sources: 每条入边的节点标识、列集合与行数；
      - columns: 对齐后的结果列（并集顺序 = 入边先后 + 列出现顺序），
        带提升后的类型、来自哪些入边、哪些入边缺失该列；
      - notes: 推导过程的提示（如某入边无样本、执行失败）。
    """
    node = pipe.node(node_id)
    if node.type is not StepType.UNION:
        raise ValueError(f"节点 {node_id} 不是合并节点")
    parents = pipe.upstream_ids(node_id)
    if len(parents) < 2:
        raise ValueError("合并节点至少需要两条入边")

    sources: list[dict] = []
    notes: list[str] = []
    ordered_columns: list[str] = []
    rows_by_parent: dict[str, list[dict]] = {}

    for parent in parents:
        rows: list[dict] = []
        try:
            outcome = run_pipeline(
                pipe, task_config=task_config, dry_run=True,
                sample=sample, store=store, connector_factory=connector_factory,
                sample_node=parent, sample_limit=max(1, int(limit)),
                collect_samples=True, limit_rows=max(1, int(limit)),
            )
            rows = list(outcome.samples.get(parent, []))
        except (PipelineRunError, ValueError) as e:  # noqa: PERF203 - 单条入边失败不阻断其它入边
            notes.append(f"入边 {parent} 推导失败：{e}")
        if not rows:
            notes.append(f"入边 {parent} 没有样本数据，列集合可能不完整")
        rows_by_parent[parent] = rows
        cols = _columns_of(rows)
        sources.append({"node_id": parent, "columns": cols, "rows": len(rows)})
        for col in cols:
            if col not in ordered_columns:
                ordered_columns.append(col)

    configured = [str(c) for c in ((node.config or {}).get("columns") or []) if str(c)]
    result_columns = configured or ordered_columns
    raw_aliases = (node.config or {}).get("aliases")
    aliases = ({str(k): str(v) for k, v in raw_aliases.items() if str(v or "").strip()}
               if isinstance(raw_aliases, dict) else {})

    columns: list[dict] = []
    for col in result_columns:
        kinds: dict[str, int] = {}
        present: list[str] = []
        missing: list[str] = []
        for parent in parents:
            rows = rows_by_parent.get(parent) or []
            if any(col in row for row in rows):
                present.append(parent)
                for row in rows:
                    value = row.get(col)
                    if value is not None:
                        kinds[col] = max(kinds.get(col, 0), _kind(value))
            else:
                missing.append(parent)
        columns.append({
            "name": col,
            "target": aliases.get(col) or col,
            "type": _KIND_CN[kinds.get(col, 3)],
            "from": present,
            "missing": missing,
        })

    targets = [c["target"] for c in columns]
    for dup in sorted({t for t in targets if targets.count(t) > 1}):
        notes.append(f"结果列名重复：{dup}（后出现的列会覆盖先出现的列）")

    return {
        "node_id": node_id,
        "sources": sources,
        "columns": columns,
        "configured": bool(configured),
        "notes": notes,
    }
