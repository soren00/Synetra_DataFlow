"""流水线解释执行引擎。

按拓扑序逐节点执行，行以批次在节点间流动：

- 行级步骤逐批处理，不缓存全量；
- 整批步骤（排序/去重/分组聚合）在内存中排序分块，超过阈值时把有序块溢出到
  本地临时目录，再做多路归并（结果与纯内存路径逐行一致）；
- 目标步骤按 `commit_size` 分批提交，失败时已提交批次不回滚（由 upsert 幂等兜底）。

每个节点记录输入/输出/拒绝行数与耗时，作为步级指标返回；
dry-run 与节点级试运行复用同一引擎，只是不写目标。
"""

from __future__ import annotations

import csv
import heapq
import json
import pickle
import shutil
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from app.core.config import get_settings
from app.expr.whitelist import apply_expression
from app.models.pipeline import PipelineConfig, PipelineNode, StepType
from app.models.task_config import FieldRule
from app.transform import apply_rule

from .registry import get_spec

_BATCH_ROW_BYTES = 1024  # spill 阈值的行数换算基准（约 1KB/行）


# ---------------------------------------------------------------- 结果与错误


@dataclass
class NodeMetric:
    node_id: str
    type: str
    name: str = ""
    in_rows: int = 0
    out_rows: int = 0
    rejected_rows: int = 0
    elapsed_ms: float = 0.0
    status: str = "pending"
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "type": self.type,
            "name": self.name,
            "in_rows": self.in_rows,
            "out_rows": self.out_rows,
            "rejected_rows": self.rejected_rows,
            "elapsed_ms": self.elapsed_ms,
            "status": self.status,
            "error": self.error,
        }


@dataclass
class RunOutcome:
    rows: int = 0
    watermark: str | None = None
    metrics: list[dict] = field(default_factory=list)
    samples: dict[str, list[dict]] = field(default_factory=dict)
    spilled: bool = False


class PipelineRunError(RuntimeError):
    """步骤执行失败：携带失败节点与已完成的步级指标。"""

    def __init__(self, node_id: str, message: str, outcome: RunOutcome):
        super().__init__(f"步骤 {node_id} 失败: {message}")
        self.node_id = node_id
        self.error = message
        self.outcome = outcome


# ---------------------------------------------------------------- 执行上下文


class _Context:
    def __init__(
        self,
        pipe: PipelineConfig,
        *,
        dry_run: bool,
        watermark: str,
        sample: list[dict] | None,
        store: Any = None,
        connector_factory: Callable[[str], Any] | None = None,
        spill_dir: Path | None = None,
        sample_node: str | None = None,
        sample_limit: int = 100,
        task_config: dict | None = None,
        collect_samples: bool = False,
        limit_rows: int = 0,
    ):
        self.pipe = pipe
        self.dry_run = dry_run
        self.watermark = watermark or ""
        self.sample = sample
        self.task_config = task_config or {}
        self.store = store
        self.connector_factory = connector_factory
        self.sample_node = sample_node
        self.sample_limit = max(1, int(sample_limit))
        #: 是否保留每个节点的样例行（失败快照与节点预览用）；上限 sample_limit/节点
        self.collect_samples = collect_samples or bool(sample_node)
        #: 单次执行最多处理的源行数（0=不限）；仅用于结构推导/预览这类探针场景
        self.limit_rows = max(0, int(limit_rows))
        self.spilled = False
        self.rows_written = 0
        self.max_watermark: str | None = None
        self.samples: dict[str, list[dict]] = {}
        self.metrics: dict[str, NodeMetric] = {
            n.id: NodeMetric(node_id=n.id, type=n.type.value, name=n.name or n.type.value)
            for n in pipe.nodes
        }
        self.scope: set[str] | None = None
        if sample_node:
            self.scope = pipe.ancestors(sample_node) | {sample_node}
        self.spill_dir = spill_dir
        self._connectors: dict[str, Any] = {}
        self._seq: dict[str, int] = {}

    # ---- 连接解析 ----

    def connector(self, conn_key: str):
        if conn_key in self._connectors:
            return self._connectors[conn_key]
        if self.connector_factory is not None:
            conn = self.connector_factory(conn_key)
        else:
            from app.etl_sdk.relational import get_connector
            from app.store import Store

            store = self.store or Store(get_settings().data_dir)
            meta = store.get_connection(conn_key)
            try:
                secret = store.get_secret(conn_key).decode("utf-8")
            except KeyError:
                secret = None
            conn = get_connector(meta.ctype, meta.meta, secret)
        self._connectors[conn_key] = conn
        return conn

    # ---- 水位 ----

    def track_watermark(self, value: Any) -> None:
        if value is None:
            return
        text = str(value)
        if self.max_watermark is None or text > self.max_watermark:
            self.max_watermark = text

    def spill_threshold(self) -> int:
        if self.pipe.spill_rows > 0:
            return self.pipe.spill_rows
        mb = max(1, int(self.pipe.spill_threshold_mb))
        return max(1, mb * 1024 * 1024 // _BATCH_ROW_BYTES)

    def tmp_dir(self) -> Path | None:
        return self.spill_dir


# ---------------------------------------------------------------- 排序与溢出


class _SortVal:
    """可比较包装：None 视为最小，跨类型比较退化为字符串比较。"""

    __slots__ = ("desc", "v")

    def __init__(self, v: Any, desc: bool):
        self.v = v
        self.desc = desc

    def __lt__(self, other: _SortVal) -> bool:
        a, b = self.v, other.v
        if a is None or b is None:
            if a is None and b is None:
                return False
            # 升序时空值排最前，降序时排最后
            return (not self.desc) if a is None else self.desc
        if a == b:
            return False
        try:
            return (a > b) if self.desc else (a < b)
        except TypeError:
            sa, sb = str(a), str(b)
            return (sa > sb) if self.desc else (sa < sb)


def _sort_key_fn(keys: list[dict]) -> Callable[[dict], tuple]:
    specs = [(str(k.get("field", "")), bool(k.get("desc"))) for k in keys]

    def key(row: dict) -> tuple:
        return tuple(_SortVal(row.get(f), d) for f, d in specs)

    return key


def _pickle_chunks(rows: Iterable[dict], key_fn, *, max_rows: int,
                   tmp_dir: Path) -> tuple[list[Path], list[dict], bool]:
    """把输入切块排序并落盘；返回（块文件, 未落盘剩余, 是否发生溢出）。"""
    files: list[Path] = []
    chunk: list[dict] = []
    spilled = False
    for row in rows:
        chunk.append(row)
        if max_rows > 0 and len(chunk) >= max_rows:
            chunk.sort(key=key_fn)
            path = tmp_dir / f"chunk_{len(files)}.pkl"
            with open(path, "wb") as f:
                pickle.dump(chunk, f)
            files.append(path)
            chunk = []
            spilled = True
    if files:
        if chunk:
            chunk.sort(key=key_fn)
            path = tmp_dir / f"chunk_{len(files)}.pkl"
            with open(path, "wb") as f:
                pickle.dump(chunk, f)
            files.append(path)
            chunk = []
        return files, chunk, spilled
    chunk.sort(key=key_fn)
    return files, chunk, spilled


def _read_chunk(path: Path) -> Iterator[dict]:
    with open(path, "rb") as f:
        data = pickle.load(f)
    yield from data


def _external_sorted(rows: Iterable[dict], key_fn, ctx: _Context) -> Iterator[dict]:
    """外部排序：内存内分块排序 + 块文件多路归并。"""
    threshold = ctx.spill_threshold()
    files, remainder, _spilled = _pickle_chunks(
        rows, key_fn, max_rows=threshold, tmp_dir=ctx.tmp_dir() or Path("."))
    if not files:
        yield from remainder
        return
    ctx.spilled = True
    iters = [_read_chunk(p) for p in files]
    yield from heapq.merge(*iters, key=key_fn)


# ---------------------------------------------------------------- 类型归一


def _kind(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return 1
    if isinstance(value, float):
        return 2
    return 3


def _coerce(value: Any, kind: int) -> Any:
    if value is None:
        return None
    if kind == 3:
        return value if isinstance(value, str) else str(value)
    if kind == 0:
        return bool(value)
    if kind == 1:
        try:
            return int(value)
        except (TypeError, ValueError):
            return str(value)
    if kind == 2:
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)
    return value


# ---------------------------------------------------------------- 源


def _param_placeholder(conn: Any, name: str = "wm") -> str:
    ctype = getattr(conn, "ctype", None)
    value = getattr(ctype, "value", ctype)
    if value == "oracle":
        return f":{name}"
    if value == "sqlserver":
        return "?"
    return "%s"


def _source_rows(ctx: _Context, node: PipelineNode) -> Iterator[dict]:
    cfg = node.config or {}
    if node.type is StepType.SOURCE_CSV:
        yield from _read_csv(str(cfg.get("path", "")), str(cfg.get("delimiter", ",") or ","))
        return
    if node.type is StepType.SOURCE_API:
        yield from _read_api(ctx, cfg)
        return

    conn = ctx.connector(str(cfg.get("conn_key", "")))
    if node.type is StepType.SOURCE_SQL:
        sql = str(cfg.get("sql", ""))
        params: list = []
    else:
        sql = f"SELECT * FROM {cfg.get('object', '')}"
        params = []
        wm_field = str(cfg.get("watermark_field") or "").strip()
        if wm_field and ctx.watermark:
            sql += f" WHERE {wm_field} > {_param_placeholder(conn)}"
            params.append(ctx.watermark)

    batch_size = max(1, int(ctx.pipe.batch_size))
    if hasattr(conn, "extract_batches"):
        for batch in conn.extract_batches(sql, params or None, batch_size):
            yield from batch
    elif params and hasattr(conn, "extract_with_params"):
        yield from conn.extract_with_params(sql, params)
    else:
        yield from conn.extract(sql)


def _read_csv(path: str, delimiter: str) -> Iterator[dict]:
    if not path:
        raise ValueError("CSV 源缺少文件路径")
    if path.startswith(("http://", "https://")):
        import io
        import urllib.request

        with urllib.request.urlopen(path, timeout=30) as r:
            text = r.read().decode("utf-8-sig", "replace")
        fh: Any = io.StringIO(text)
    else:
        fh = open(path, "r", encoding="utf-8-sig", newline="")  # noqa: SIM115 - with 内消费
    try:
        reader = csv.DictReader(fh, delimiter=delimiter or ",")
        for row in reader:
            yield dict(row)
    finally:
        fh.close()


def _read_api(ctx: _Context, cfg: dict) -> Iterator[dict]:
    from app.sources.rest import request_json

    conn_key = str(cfg.get("conn_key", ""))
    meta: dict = {}
    secret = None
    if ctx.connector_factory is None:
        from app.store import Store

        store = ctx.store or Store(get_settings().data_dir)
        record = store.get_connection(conn_key)
        meta = dict(record.meta or {})
        try:
            secret = store.get_secret(conn_key).decode("utf-8")
        except KeyError:
            secret = None
    payload = request_json(
        str(meta.get("method", "GET") or "GET"),
        urljoin(str(meta.get("url", "")), str(cfg.get("path", "") or "")),
        secret=secret,
        auth_scheme=str(meta.get("auth", "none") or "none"),
    )
    data: Any = payload
    for part in str(cfg.get("records_path", "") or "").split("."):
        if not part:
            continue
        if isinstance(data, dict):
            data = data.get(part)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise TypeError("API 源返回的不是记录数组")
    for item in data:
        if isinstance(item, dict):
            yield item


# ---------------------------------------------------------------- 合并


def _probe(iterator: Iterator[dict], limit: int) -> tuple[list[dict], Iterator[dict]]:
    buffered: list[dict] = []
    for row in iterator:
        buffered.append(row)
        if len(buffered) >= limit:
            break
    return buffered, iterator


def _run_union(ctx: _Context, node: PipelineNode, upstream: list[Iterator[dict]],
               metric: NodeMetric) -> Iterator[dict]:
    cfg = node.config or {}
    configured = [str(c) for c in (cfg.get("columns") or []) if str(c).strip()]
    raw_aliases = cfg.get("aliases")
    aliases = ({str(k): str(v) for k, v in raw_aliases.items() if str(v or "").strip()}
               if isinstance(raw_aliases, dict) else {})
    probe_n = 100
    probed: list[tuple[list[dict], Iterator[dict]]] = []
    columns: list[str] = list(configured)
    kinds: dict[str, int] = {}

    for it in upstream:
        buffered, rest = _probe(it, probe_n)
        probed.append((buffered, rest))
        for row in buffered:
            for key, value in row.items():
                if key not in columns and not configured:
                    columns.append(key)
                if value is not None:
                    kinds[key] = max(kinds.get(key, 0), _kind(value))

    def emit(row: dict) -> dict:
        out = {}
        for col in columns:
            out[aliases.get(col) or col] = _coerce(row.get(col), kinds.get(col, 3))
        return out

    for buffered, rest in probed:
        for row in buffered:
            metric.in_rows += 1
            metric.out_rows += 1
            yield emit(row)
        for row in rest:
            metric.in_rows += 1
            metric.out_rows += 1
            yield emit(row)


# ---------------------------------------------------------------- 行级步骤


def _row_ops(ctx: _Context, node: PipelineNode) -> Callable[[dict], dict | None]:
    cfg = node.config or {}
    stype = node.type

    if stype is StepType.SELECT:
        rules = [FieldRule.model_validate(r) for r in (cfg.get("rules") or [])]
        if not rules:
            return lambda row: row

        def _select(row: dict) -> dict:
            out: dict = {}
            for rule in rules:
                out[rule.target or rule.source] = apply_rule(rule, row)
            return out

        return _select

    if stype is StepType.FILTER:
        expr = str(cfg.get("expression", ""))

        def _filter(row: dict) -> dict | None:
            value = apply_expression(expr, {k: v for k, v in row.items() if str(k).isidentifier()})
            return row if value else None

        return _filter

    if stype is StepType.CAST:
        field = str(cfg.get("field", ""))
        target = str(cfg.get("target") or field)
        cast_to = cfg.get("cast_to")

        def _cast(row: dict) -> dict:
            out = dict(row)
            out[target] = _cast_value(row.get(field), cast_to)
            return out

        return _cast

    if stype is StepType.EXPRESSION:
        target = str(cfg.get("target", ""))
        expr = str(cfg.get("expression", ""))

        def _expr(row: dict) -> dict:
            out = dict(row)
            ns = {k: v for k, v in row.items() if str(k).isidentifier()}
            out[target] = apply_expression(expr, ns)
            return out

        return _expr

    if stype is StepType.VALUE_MAP:
        field = str(cfg.get("field", ""))
        target = str(cfg.get("target") or field)
        mapping = {str(k): v for k, v in (cfg.get("mapping") or {}).items()}
        default = cfg.get("default")

        def _map(row: dict) -> dict:
            out = dict(row)
            raw = row.get(field)
            key = "" if raw is None else str(raw)
            out[target] = mapping.get(key, default)
            return out

        return _map

    if stype is StepType.NULL_FILL:
        field = str(cfg.get("field", ""))
        target = str(cfg.get("target") or field)
        default = cfg.get("default")

        def _fill(row: dict) -> dict:
            out = dict(row)
            value = row.get(field)
            out[target] = default if value is None or value == "" else value
            return out

        return _fill

    if stype is StepType.STRING_OPS:
        field = str(cfg.get("field", ""))
        target = str(cfg.get("target") or field)
        op = str(cfg.get("op", ""))
        args = list(cfg.get("args") or [])

        def _string(row: dict) -> dict:
            out = dict(row)
            out[target] = _apply_string_op(row.get(field), op, args)
            return out

        return _string

    if stype is StepType.CONSTANT:
        target = str(cfg.get("target", ""))
        value = cfg.get("value")

        def _const(row: dict) -> dict:
            out = dict(row)
            out[target] = value
            return out

        return _const

    if stype is StepType.SEQUENCE:
        target = str(cfg.get("target", ""))
        start = int(cfg.get("start", 1) or 1)
        step = int(cfg.get("step", 1) or 1)

        def _seq(row: dict) -> dict:
            cur = ctx._seq.get(node.id, start)
            ctx._seq[node.id] = cur + step
            out = dict(row)
            out[target] = cur
            return out

        return _seq

    return lambda row: row


def _cast_value(value: Any, cast_to: Any) -> Any:
    if cast_to == "bool_int":
        return 1 if value else 0
    if cast_to == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if cast_to == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if cast_to == "str":
        try:
            return str(value)
        except (TypeError, ValueError):
            return None
    if cast_to == "bool":
        try:
            return bool(value)
        except (TypeError, ValueError):
            return None
    return value


def _apply_string_op(value: Any, op: str, args: list) -> Any:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if op == "trim":
        return text.strip()
    if op == "lower":
        return text.lower()
    if op == "upper":
        return text.upper()
    if op == "replace":
        old = str(args[0]) if len(args) > 0 else ""
        new = str(args[1]) if len(args) > 1 else ""
        return text.replace(old, new)
    if op == "substr":
        start = int(args[0]) if len(args) > 0 else 0
        end = int(args[1]) if len(args) > 1 else None
        return text[start:end]
    return text


# ---------------------------------------------------------------- 整批步骤


def _run_batch(ctx: _Context, node: PipelineNode, upstream: Iterator[dict],
               metric: NodeMetric) -> Iterator[dict]:
    cfg = node.config or {}

    def consume() -> Iterator[dict]:
        for row in upstream:
            metric.in_rows += 1
            yield row

    if node.type is StepType.SORT:
        keys = list(cfg.get("keys") or [])
        if not keys:
            raise ValueError("排序步骤缺少排序键")
        key_fn = _sort_key_fn(keys)
        for row in _external_sorted(consume(), key_fn, ctx):
            metric.out_rows += 1
            yield row
        return

    if node.type is StepType.UNIQUE:
        fields = [str(f) for f in (cfg.get("keys") or [])]

        def key_fn(row: dict) -> tuple:
            if fields:
                return tuple(_SortVal(row.get(f), False) for f in fields)
            return tuple(_SortVal(row.get(k), False) for k in sorted(row))

        seen: set[tuple] = set()
        for row in _external_sorted(consume(), key_fn, ctx):
            token = (tuple(str(row.get(f)) for f in fields) if fields
                     else tuple(str(row.get(k)) for k in sorted(row)))
            if token in seen:
                metric.rejected_rows += 1
                continue
            seen.add(token)
            metric.out_rows += 1
            yield row
        return

    if node.type is StepType.AGGREGATE:
        group_by = [str(f) for f in (cfg.get("group_by") or [])]
        aggs = [a for a in (cfg.get("aggregations") or []) if a]

        def key_fn(row: dict) -> tuple:
            return tuple(_SortVal(row.get(f), False) for f in group_by)

        current_key: tuple | None = None
        acc: dict = {}
        count = 0

        def start(row: dict) -> dict:
            out = {f: row.get(f) for f in group_by}
            for a in aggs:
                target = str(a.get("target") or f"{a.get('func')}_{a.get('field', '')}")
                out[target] = None
            return out

        def accumulate(out: dict, row: dict) -> None:
            nonlocal count
            count += 1
            for a in aggs:
                func = str(a.get("func", "")).lower()
                field = str(a.get("field", "*"))
                target = str(a.get("target") or f"{func}_{field}")
                value = row.get(field) if field != "*" else 1
                cur = out.get(target)
                if func == "count":
                    out[target] = (cur or 0) + 1
                elif func == "sum":
                    out[target] = (cur or 0) + (value if isinstance(value, (int, float)) else 0)
                elif func == "min":
                    out[target] = value if cur is None else (value if value is not None and value < cur else cur)
                elif func == "max":
                    out[target] = value if cur is None else (value if value is not None and value > cur else cur)
                elif func == "first":
                    out[target] = value if cur is None else cur
                elif func == "last":
                    out[target] = value
                elif func == "avg":
                    prev = out.get(target) or (0, 0)
                    if isinstance(prev, tuple):
                        s, c = prev
                        s += value if isinstance(value, (int, float)) else 0
                        c += 1
                        out[target] = (s, c)

        for row in _external_sorted(consume(), key_fn, ctx):
            token = tuple(str(row.get(f)) for f in group_by)
            if current_key is None:
                current_key = token
                acc = start(row)
                count = 0
            elif token != current_key:
                metric.out_rows += 1
                yield _finalize_agg(acc)
                current_key = token
                acc = start(row)
                count = 0
            accumulate(acc, row)
        if current_key is not None:
            metric.out_rows += 1
            yield _finalize_agg(acc)
        return

    raise TypeError(f"不支持的整批步骤: {node.type}")


def _finalize_agg(out: dict) -> dict:
    for key, value in list(out.items()):
        if isinstance(value, tuple) and len(value) == 2:
            s, c = value
            out[key] = (s / c) if c else None
    return out


# ---------------------------------------------------------------- 目标


def _run_target(ctx: _Context, node: PipelineNode, upstream: Iterator[dict],
                metric: NodeMetric) -> None:
    """排空上游并写入目标（或在 dry-run 下只计数）；本步骤不产出下游行。"""
    cfg = node.config or {}
    commit = max(1, int(ctx.pipe.commit_size))
    if node.type is StepType.TARGET_CSV:
        path = str(cfg.get("path", ""))
        if not path:
            raise ValueError("CSV 目标缺少输出路径")
        if ctx.dry_run:
            for _ in upstream:
                metric.in_rows += 1
                metric.out_rows += 1
                ctx.rows_written += 1
            return
        first = True
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer: csv.DictWriter | None = None
            batch: list[dict] = []

            def flush() -> None:
                nonlocal writer, first, batch
                if not batch:
                    return
                if writer is None:
                    writer = csv.DictWriter(f, fieldnames=list(batch[0].keys()))
                    writer.writeheader()
                writer.writerows(batch)
                ctx.rows_written += len(batch)
                batch = []

            for row in upstream:
                metric.in_rows += 1
                batch.append(row)
                if len(batch) >= commit:
                    flush()
            flush()
        return

    table = str(cfg.get("object", ""))
    mode = str(cfg.get("mode", "append") or "append")
    primary_keys = [str(k) for k in (cfg.get("primary_keys") or [])]

    # dry-run：只计数、不解析连接也不写目标（即使连接不存在也应通过）
    if ctx.dry_run:
        for _ in upstream:
            metric.in_rows += 1
            metric.out_rows += 1
            ctx.rows_written += 1
        return

    conn = ctx.connector(str(cfg.get("conn_key", "")))

    def batches() -> Iterator[list[dict]]:
        batch: list[dict] = []
        for row in upstream:
            metric.in_rows += 1
            batch.append(row)
            if len(batch) >= commit:
                yield batch
                batch = []
        if batch:
            yield batch

    if hasattr(conn, "load_batches"):
        summary = conn.load_batches(table, batches(), mode, primary_keys,
                                     columns=None, commit_size=commit)
        ctx.rows_written += int(getattr(summary, "rows", 0))
        metric.out_rows = int(getattr(summary, "rows", 0))
        return

    all_rows: list[dict] = []
    for batch in batches():
        all_rows.extend(batch)
        ctx.rows_written += len(batch)
        metric.out_rows += len(batch)
    if all_rows:
        conn.load(table, all_rows, mode, primary_keys)


# ---------------------------------------------------------------- 主循环


def _iter_node(ctx: _Context, node_id: str) -> Iterator[dict]:
    node = ctx.pipe.node(node_id)
    metric = ctx.metrics[node_id]
    metric.status = "running"
    start = time.perf_counter()
    upstream_edges = [e for e in ctx.pipe.incoming(node_id)
                      if ctx.scope is None or e.source in ctx.scope]
    upstream = [_iter_node(ctx, e.source) for e in upstream_edges]

    def produce() -> Iterator[dict]:
        if node.type.value.startswith("source_"):
            if ctx.dry_run and ctx.sample is not None:
                for row in ctx.sample:
                    metric.out_rows += 1
                    yield row
                return
            track = str((node.config or {}).get("track_watermark") or "")
            count = 0
            for row in _source_rows(ctx, node):
                metric.out_rows += 1
                if track:
                    ctx.track_watermark(row.get(track))
                yield row
                count += 1
                if ctx.limit_rows and count >= ctx.limit_rows:
                    break
            return
        if node.type is StepType.UNION:
            yield from _run_union(ctx, node, upstream, metric)
            return
        if node.type in (StepType.SORT, StepType.UNIQUE, StepType.AGGREGATE):
            if not upstream:
                raise ValueError("整批步骤缺少输入")
            yield from _run_batch(ctx, node, upstream[0], metric)
            return
        if node.type.value.startswith("target_"):
            if not upstream:
                raise ValueError("目标步骤缺少输入")
            _run_target(ctx, node, upstream[0], metric)
            return
        if not upstream:
            raise ValueError("步骤缺少输入")
        transform = _row_ops(ctx, node)
        for row in upstream[0]:
            metric.in_rows += 1
            mapped = transform(row)
            if mapped is None:
                metric.rejected_rows += 1
                continue
            metric.out_rows += 1
            yield mapped

    try:
        for row in produce():
            if ctx.collect_samples:
                bucket = ctx.samples.setdefault(node_id, [])
                if len(bucket) < ctx.sample_limit:
                    bucket.append(dict(row))
            yield row
        metric.status = "success"
    except Exception as e:
        metric.status = "failed"
        metric.error = str(e)
        raise
    finally:
        metric.elapsed_ms = round((time.perf_counter() - start) * 1000, 1)


def run_pipeline(
    pipeline: PipelineConfig | dict,
    *,
    task_config: dict | None = None,
    dry_run: bool = False,
    watermark: str = "",
    sample: list[dict] | None = None,
    store: Any = None,
    connector_factory: Callable[[str], Any] | None = None,
    spill_root: str | Path | None = None,
    sample_node: str | None = None,
    sample_limit: int = 100,
    run_id: str | None = None,
    collect_samples: bool = False,
    snapshot_root: str | Path | None = None,
    snapshot_keep: int = 20,
    snapshot_nodes: list[str] | None = None,
    limit_rows: int = 0,
) -> RunOutcome:
    """执行流水线。

    - 正常执行：排空全部目标节点，返回写入行数与步级指标；
    - `dry_run=True`：目标只计数不写入；若同时提供 `sample`，各源直接产出该样本；
    - `sample_node`：只执行到该节点（含其全部祖先），返回该节点样本，不写目标。
    """
    pipe = pipeline if isinstance(pipeline, PipelineConfig) else PipelineConfig.model_validate(pipeline)
    problems = pipe.validate_structure()
    if problems:
        raise ValueError("; ".join(problems))

    settings = get_settings()
    root = Path(spill_root) if spill_root else Path(settings.data_dir) / "spill"
    tmp = root / (run_id or uuid.uuid4().hex)
    tmp.mkdir(parents=True, exist_ok=True)

    ctx = _Context(
        pipe,
        dry_run=dry_run,
        watermark=watermark,
        sample=sample,
        store=store,
        connector_factory=connector_factory,
        spill_dir=tmp,
        sample_node=sample_node,
        sample_limit=sample_limit,
        task_config=task_config,
        collect_samples=collect_samples,
        limit_rows=limit_rows,
    )

    outcome = RunOutcome()
    try:
        if sample_node:
            # 试运行：只排空目标节点的输出；样本由 _iter_node 在采样开关下收集
            for _ in _iter_node(ctx, sample_node):
                pass
        else:
            targets = [n for n in pipe.target_nodes()]
            if not targets:
                raise ValueError("流水线缺少目标节点")
            for target in targets:
                for _ in _iter_node(ctx, target.id):
                    pass
        outcome.rows = ctx.rows_written
        outcome.watermark = ctx.max_watermark
        outcome.spilled = ctx.spilled
        outcome.samples = ctx.samples
        if run_id and snapshot_nodes:
            write_snapshots(ctx.samples, run_id,
                            snapshot_root or (Path(settings.data_dir) / "runs"),
                            keep=snapshot_keep, nodes=snapshot_nodes)
    except Exception as e:
        outcome.rows = ctx.rows_written
        outcome.watermark = None
        outcome.spilled = ctx.spilled
        outcome.samples = ctx.samples
        failed = next((m for m in ctx.metrics.values() if m.status == "failed"), None)
        node_id = failed.node_id if failed else ""
        outcome.metrics = [m.to_dict() for m in ctx.metrics.values()]
        # 失败自动保留失败步骤样本（本地运行目录，不写目标库）
        if run_id and node_id:
            write_snapshots(ctx.samples, run_id,
                            snapshot_root or (Path(get_settings().data_dir) / "runs"),
                            keep=snapshot_keep, nodes=[node_id] if node_id in ctx.samples else [])
        raise PipelineRunError(node_id, str(e), outcome) from e
    finally:
        outcome.metrics = [m.to_dict() for m in ctx.metrics.values()]
        shutil.rmtree(tmp, ignore_errors=True)

    return outcome


def write_snapshots(samples: dict[str, list[dict]], run_id: str,
                    root: str | Path, *, keep: int = 20,
                    nodes: list[str] | None = None) -> list[str]:
    """把步级样例行写到本地运行目录（不进目标库）；返回写出的文件路径。"""
    if not run_id or not samples:
        return []
    root_path = Path(root)
    run_dir = root_path / run_id
    written: list[str] = []
    for node_id, rows in samples.items():
        if nodes is not None and node_id not in nodes:
            continue
        if not rows:
            continue
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"{node_id}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows)
        written.append(str(path))
    _prune_snapshots(root_path, keep)
    return written


def _prune_snapshots(root: Path, keep: int) -> None:
    if keep <= 0 or not root.exists():
        return
    dirs = sorted((p for p in root.iterdir() if p.is_dir()),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dirs[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def cleanup_stale_spill(spill_root: str | Path, keep_seconds: int = 6 * 3600) -> int:
    """清理异常退出遗留的溢出目录（启动/每次运行前调用）。"""
    root = Path(spill_root)
    if not root.exists():
        return 0
    now = time.time()
    removed = 0
    for p in root.iterdir():
        try:
            if now - p.stat().st_mtime > keep_seconds:
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
        except OSError:  # pragma: no cover - 并发删除等竞态
            continue
    return removed


def pipeline_summary(pipe: PipelineConfig) -> dict:
    """供 API 返回的图摘要（节点数、阻塞步骤、批次参数）。"""
    return {
        "nodes": len(pipe.nodes),
        "edges": len(pipe.edges),
        "blocking_steps": [n.id for n in pipe.nodes
                           if get_spec(n.type).blocking],
        "batch_size": pipe.batch_size,
        "commit_size": pipe.commit_size,
    }


__all__ = [
    "NodeMetric",
    "PipelineRunError",
    "RunOutcome",
    "cleanup_stale_spill",
    "pipeline_summary",
    "run_pipeline",
]
