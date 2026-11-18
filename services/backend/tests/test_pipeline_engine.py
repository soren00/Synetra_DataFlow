"""执行引擎（2.1~2.9 + 1.3 编译等价）：行级/整批/合并语义、分批提交、溢出、dry-run、试运行、水位。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.etl_sdk.pipeline import (
    PipelineRunError,
    compile_fields_to_pipeline,
    derive_union_schema,
    run_pipeline,
)
from app.models.pipeline import PipelineConfig, PipelineEdge, PipelineNode, StepType
from app.models.task_config import TaskConfig
from app.transform import apply_rules


class FakeConn:
    """最小关系库连接替身：只实现引擎依赖的分批读/分批写。"""

    ctype = "sqlite"

    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows or []
        self.loaded: list[dict] = []
        self.batch_sizes: list[int] = []
        self.sql = ""
        self.params = None

    def extract_batches(self, sql, params=None, batch_size=5000):
        self.sql, self.params = sql, params
        size = max(1, int(batch_size))
        for i in range(0, len(self.rows), size):
            yield self.rows[i:i + size]

    def load_batches(self, table, batches, mode, primary_keys, columns=None,
                     commit_size=5000, watermark_field=None):
        total = 0
        for batch in batches:
            self.batch_sizes.append(len(batch))
            self.loaded.extend(batch)
            total += len(batch)
        return SimpleNamespace(rows=total, table=table, mode=mode)


def _node(node_id, kind, **cfg):
    return PipelineNode(id=node_id, type=kind, config=cfg)


def _chain(*nodes, **kw) -> PipelineConfig:
    edges = [PipelineEdge(source=nodes[i].id, target=nodes[i + 1].id)
             for i in range(len(nodes) - 1)]
    return PipelineConfig(nodes=list(nodes), edges=edges, **kw)


def _run(pipe, conns, **kw):
    return run_pipeline(pipe, connector_factory=lambda k: conns[k], **kw)


def _pipeline(rows, *mid_nodes, commit_size=1000, **cfg):
    src = _node("src", StepType.SOURCE_TABLE, conn_key="s", object="t1")
    dst = _node("dst", StepType.TARGET_TABLE, conn_key="g", object="t2", mode="append")
    pipe = _chain(src, *mid_nodes, dst, commit_size=commit_size, **cfg)
    return pipe, {"s": FakeConn(rows), "g": FakeConn()}


# ---------------------------------------------------------------- 行级步骤


def test_select_rules_map_like_legacy(tmp_path):
    rules = [
        {"kind": "direct", "source": "id", "target": "id"},
        {"kind": "constant", "source": "", "target": "src_sys", "constant_value": "crm"},
        {"kind": "null_fill", "source": "name", "target": "name", "null_default": "匿名"},
        {"kind": "cast", "source": "amount", "target": "amount", "cast_to": "int"},
        {"kind": "expression", "source": "name", "target": "name_up", "expression": "upper(name)"},
    ]
    rows = [{"id": 1, "name": "bob", "amount": "12"}, {"id": 2, "name": None, "amount": "x"}]
    pipe, conns = _pipeline(rows, _node("pick", StepType.SELECT, rules=rules))
    out = _run(pipe, conns, spill_root=tmp_path)

    assert out.rows == 2
    assert conns["g"].loaded[0] == {"id": 1, "src_sys": "crm", "name": "bob",
                                    "amount": 12, "name_up": "BOB"}
    assert conns["g"].loaded[1]["name"] == "匿名"
    assert conns["g"].loaded[1]["amount"] is None
    metrics = {m["node_id"]: m for m in out.metrics}
    assert metrics["pick"]["in_rows"] == 2 and metrics["pick"]["out_rows"] == 2


def test_row_steps_chain(tmp_path):
    rows = [{"id": 1, "status": "1", "name": " bob ", "amt": 3}]
    steps = [
        _node("trim", StepType.STRING_OPS, field="name", target="name", op="trim"),
        _node("map", StepType.VALUE_MAP, field="status", target="status_cn",
              mapping={"1": "启用"}, default="未知"),
        _node("cast", StepType.CAST, field="amt", target="amt", cast_to="float"),
        _node("keep", StepType.FILTER, expression="amt > 1"),
        _node("seq", StepType.SEQUENCE, target="row_no", start=10, step=5),
        _node("const", StepType.CONSTANT, target="tag", value="v1"),
    ]
    pipe, conns = _pipeline(rows, *steps)
    out = _run(pipe, conns, spill_root=tmp_path)
    assert out.rows == 1
    row = conns["g"].loaded[0]
    assert row["name"] == "bob"
    assert row["status_cn"] == "启用"
    assert row["amt"] == 3.0
    assert row["row_no"] == 10
    assert row["tag"] == "v1"


def test_filter_rejects_rows(tmp_path):
    rows = [{"v": 1}, {"v": 5}, {"v": 9}]
    pipe, conns = _pipeline(rows, _node("f", StepType.FILTER, expression="v >= 5"))
    out = _run(pipe, conns, spill_root=tmp_path)
    metrics = {m["node_id"]: m for m in out.metrics}
    assert metrics["f"]["rejected_rows"] == 1
    assert [r["v"] for r in conns["g"].loaded] == [5, 9]


def test_expression_step_adds_column(tmp_path):
    rows = [{"a": "x", "b": "y"}]
    pipe, conns = _pipeline(rows, _node("e", StepType.EXPRESSION, target="ab",
                                        expression="a + '-' + b"))
    _run(pipe, conns, spill_root=tmp_path)
    assert conns["g"].loaded[0]["ab"] == "x-y"


# ---------------------------------------------------------------- 整批步骤


def test_sort_ascending_and_descending(tmp_path):
    rows = [{"v": 3}, {"v": None}, {"v": 1}]
    pipe, conns = _pipeline(rows, _node("s", StepType.SORT, keys=[{"field": "v"}]))
    _run(pipe, conns, spill_root=tmp_path)
    assert [r["v"] for r in conns["g"].loaded] == [None, 1, 3]

    conns["g"].loaded.clear()
    pipe2, conns2 = _pipeline(rows, _node("s", StepType.SORT,
                                          keys=[{"field": "v", "desc": True}]))
    _run(pipe2, conns2, spill_root=tmp_path)
    assert [r["v"] for r in conns2["g"].loaded] == [3, 1, None]


def test_unique_keeps_first(tmp_path):
    rows = [{"k": "a", "v": 1}, {"k": "a", "v": 2}, {"k": "b", "v": 3}]
    pipe, conns = _pipeline(rows, _node("u", StepType.UNIQUE, keys=["k"]))
    _run(pipe, conns, spill_root=tmp_path)
    assert [(r["k"], r["v"]) for r in conns["g"].loaded] == [("a", 1), ("b", 3)]


def test_aggregate_group_by(tmp_path):
    rows = [
        {"dept": "a", "amt": 1}, {"dept": "a", "amt": 3},
        {"dept": "b", "amt": 5}, {"dept": None, "amt": 7},
    ]
    pipe, conns = _pipeline(
        rows,
        _node("agg", StepType.AGGREGATE, group_by=["dept"], aggregations=[
            {"func": "sum", "field": "amt", "target": "total"},
            {"func": "count", "field": "*", "target": "n"},
            {"func": "avg", "field": "amt", "target": "avg"},
            {"func": "max", "field": "amt", "target": "mx"},
        ]),
    )
    _run(pipe, conns, spill_root=tmp_path)
    by_dept = {r["dept"]: r for r in conns["g"].loaded}
    assert by_dept["a"]["total"] == 4 and by_dept["a"]["n"] == 2 and by_dept["a"]["avg"] == 2
    assert by_dept["b"]["total"] == 5 and by_dept["b"]["mx"] == 5
    assert by_dept[None]["total"] == 7


# ---------------------------------------------------------------- 多源合并


def test_union_aligns_columns_and_promotes_type(tmp_path):
    left = _node("l", StepType.SOURCE_TABLE, conn_key="s1", object="t1")
    right = _node("r", StepType.SOURCE_TABLE, conn_key="s2", object="t2")
    union = _node("u", StepType.UNION, columns=[])
    dst = _node("dst", StepType.TARGET_TABLE, conn_key="g", object="t3", mode="append")
    pipe = PipelineConfig(
        nodes=[left, right, union, dst],
        edges=[PipelineEdge(source="l", target="u"),
               PipelineEdge(source="r", target="u"),
               PipelineEdge(source="u", target="dst")],
    )
    conns = {
        "s1": FakeConn([{"a": 1, "b": "x"}]),
        "s2": FakeConn([{"a": "2", "c": "y"}]),
        "g": FakeConn(),
    }
    out = _run(pipe, conns, spill_root=tmp_path)
    assert out.rows == 2
    first, second = conns["g"].loaded
    assert set(first) == {"a", "b", "c"}
    # 同名类型冲突（int vs str）按提升规则归一为字符串
    assert first["a"] == "1" and second["a"] == "2"
    assert first["c"] is None and second["b"] is None


def test_union_renames_result_columns(tmp_path):
    """5.6 结果列重命名：aliases 只改输出键，按源列名取值与补空。"""
    left = _node("l", StepType.SOURCE_TABLE, conn_key="s1", object="t1")
    right = _node("r", StepType.SOURCE_TABLE, conn_key="s2", object="t2")
    union = _node("u", StepType.UNION, columns=["a", "b"],
                  aliases={"a": "id", "b": "label"})
    dst = _node("dst", StepType.TARGET_TABLE, conn_key="g", object="t3", mode="append")
    pipe = PipelineConfig(
        nodes=[left, right, union, dst],
        edges=[PipelineEdge(source="l", target="u"),
               PipelineEdge(source="r", target="u"),
               PipelineEdge(source="u", target="dst")],
    )
    conns = {
        "s1": FakeConn([{"a": 1, "b": "x"}]),
        "s2": FakeConn([{"a": "2", "b": "y"}]),
        "g": FakeConn(),
    }
    _run(pipe, conns, spill_root=tmp_path)
    first, second = conns["g"].loaded
    assert list(first) == ["id", "label"]
    assert first["id"] == "1" and second["id"] == "2"
    assert first["label"] == "x" and second["label"] == "y"


# ---------------------------------------------------------------- 分批 / 溢出 / dry-run


def test_commit_size_batches(tmp_path):
    rows = [{"v": i} for i in range(10)]
    pipe, conns = _pipeline(rows, _node("pick", StepType.SELECT, rules=[]), commit_size=3)
    out = _run(pipe, conns, spill_root=tmp_path)
    assert out.rows == 10
    assert conns["g"].batch_sizes == [3, 3, 3, 1]


def test_spill_produces_same_result_and_cleans_up(tmp_path):
    rows = [{"v": v} for v in [5, 1, 9, 3, 7, 2, 8]]
    pipe, conns = _pipeline(rows, _node("s", StepType.SORT, keys=[{"field": "v"}]),
                            spill_rows=2)
    out = _run(pipe, conns, spill_root=tmp_path)
    assert out.spilled is True
    assert [r["v"] for r in conns["g"].loaded] == [1, 2, 3, 5, 7, 8, 9]
    # 运行结束清理本次溢出目录
    assert list(tmp_path.glob("*")) == []


def test_dry_run_does_not_write(tmp_path):
    rows = [{"v": 1}, {"v": 2}]
    pipe, conns = _pipeline(rows, _node("pick", StepType.SELECT, rules=[]))
    out = _run(pipe, conns, spill_root=tmp_path, dry_run=True)
    assert out.rows == 2
    assert conns["g"].loaded == []


def test_dry_run_with_sample_skips_sources(tmp_path):
    pipe, conns = _pipeline([{"v": 99}], _node("pick", StepType.SELECT, rules=[]))
    out = _run(pipe, conns, spill_root=tmp_path, dry_run=True,
               sample=[{"v": 1}, {"v": 2}, {"v": 3}])
    assert out.rows == 3
    assert conns["g"].loaded == []


def test_node_preview_returns_sample_without_writing(tmp_path):
    rows = [{"v": i} for i in range(5)]
    pipe, conns = _pipeline(rows, _node("pick", StepType.SELECT, rules=[]))
    out = _run(pipe, conns, spill_root=tmp_path, sample_node="pick", sample_limit=2)
    assert len(out.samples["pick"]) == 2
    assert conns["g"].loaded == []
    metrics = {m["node_id"]: m for m in out.metrics}
    assert metrics["pick"]["out_rows"] == 5


def test_failed_step_located(tmp_path):
    """失败时错误信息带失败节点标识，且指标里该节点为 failed（3.4）。"""
    bad_src = _node("bad", StepType.SOURCE_CSV, path="")
    dst = _node("dst", StepType.TARGET_TABLE, conn_key="g", object="d")
    pipe = PipelineConfig(nodes=[bad_src, dst],
                          edges=[PipelineEdge(source="bad", target="dst")])
    with pytest.raises(PipelineRunError) as e:
        run_pipeline(pipe, connector_factory=lambda k: FakeConn(), spill_root=tmp_path)
    assert e.value.node_id == "bad"
    assert any(m["node_id"] == "bad" and m["status"] == "failed"
               for m in e.value.outcome.metrics)


# ---------------------------------------------------------------- 水位


def test_watermark_injected_and_tracked(tmp_path):
    src = _node("src", StepType.SOURCE_TABLE, conn_key="s", object="orders",
                watermark_field="updated_at", track_watermark="updated_at")
    dst = _node("dst", StepType.TARGET_TABLE, conn_key="g", object="d", mode="append")
    pipe = PipelineConfig(nodes=[src, dst],
                          edges=[PipelineEdge(source="src", target="dst")])
    conns = {"s": FakeConn([{"updated_at": "2024-01-03"}, {"updated_at": "2024-01-02"}],
                           ), "g": FakeConn()}
    out = _run(pipe, conns, spill_root=tmp_path, watermark="2024-01-02")
    assert "WHERE updated_at > %s" in conns["s"].sql
    assert conns["s"].params == ["2024-01-02"]
    assert out.watermark == "2024-01-03"


def test_no_watermark_means_full_scan(tmp_path):
    src = _node("src", StepType.SOURCE_TABLE, conn_key="s", object="orders",
                watermark_field="updated_at")
    dst = _node("dst", StepType.TARGET_TABLE, conn_key="g", object="d")
    pipe = PipelineConfig(nodes=[src, dst],
                          edges=[PipelineEdge(source="src", target="dst")])
    conns = {"s": FakeConn([{"updated_at": "2024-01-03"}]), "g": FakeConn()}
    _run(pipe, conns, spill_root=tmp_path)
    assert "WHERE" not in conns["s"].sql


# ---------------------------------------------------------------- 编译等价


def _legacy_cfg() -> TaskConfig:
    return TaskConfig.model_validate({
        "project": "p", "name": "t",
        "source_conn_key": "s", "target_conn_key": "g",
        "source_object": "orders", "target_object": "dw_orders",
        "mode": "upsert", "primary_keys": ["id"],
        "incremental": {"field": "updated_at", "watermark_field": "max_updated_at"},
        "fields": [
            {"kind": "direct", "source": "id", "target": "id"},
            {"kind": "constant", "source": "", "target": "src", "constant_value": "crm"},
            {"kind": "null_fill", "source": "name", "target": "name", "null_default": "-"},
            {"kind": "cast", "source": "amt", "target": "amt", "cast_to": "float"},
            {"kind": "expression", "source": "name", "target": "up", "expression": "upper(name)"},
        ],
    })


def test_compile_fields_to_three_nodes():
    pipe = compile_fields_to_pipeline(_legacy_cfg())
    assert [n.type for n in pipe.nodes] == [
        StepType.SOURCE_TABLE, StepType.SELECT, StepType.TARGET_TABLE]
    assert pipe.source_nodes()[0].config["object"] == "orders"
    assert pipe.source_nodes()[0].config["watermark_field"] == "updated_at"
    assert pipe.source_nodes()[0].config["track_watermark"] == "max_updated_at"
    assert pipe.target_nodes()[0].config["mode"] == "upsert"
    assert pipe.target_nodes()[0].config["primary_keys"] == ["id"]


def test_compiled_pipeline_matches_legacy_transform(tmp_path):
    cfg = _legacy_cfg()
    pipe = compile_fields_to_pipeline(cfg)
    rows = [
        {"id": 1, "name": "bob", "amt": "1.5"},
        {"id": 2, "name": None, "amt": "2"},
    ]
    conns = {"s": FakeConn(rows), "g": FakeConn()}
    out = _run(pipe, conns, spill_root=tmp_path)
    assert out.rows == 2
    assert conns["g"].loaded == apply_rules(cfg.fields, rows)


def test_derive_union_schema_reports_alignment(tmp_path):
    """合并节点列对齐推导（5.6）：并集列、类型提升、缺失列来源。"""
    left = _node("l", StepType.SOURCE_TABLE, conn_key="s1", object="t1")
    right = _node("r", StepType.SOURCE_TABLE, conn_key="s2", object="t2")
    union = _node("u", StepType.UNION, columns=[])
    dst = _node("dst", StepType.TARGET_TABLE, conn_key="g", object="t3")
    pipe = PipelineConfig(
        nodes=[left, right, union, dst],
        edges=[PipelineEdge(source="l", target="u"),
               PipelineEdge(source="r", target="u"),
               PipelineEdge(source="u", target="dst")],
    )
    conns = {
        "s1": FakeConn([{"a": 1, "b": "x"}]),
        "s2": FakeConn([{"a": "2", "c": "y"}]),
        "g": FakeConn(),
    }
    result = derive_union_schema(pipe, "u", connector_factory=lambda k: conns[k],
                                 limit=10, task_config={})

    by_name = {c["name"]: c for c in result["columns"]}
    assert list(by_name) == ["a", "b", "c"]
    assert by_name["a"]["type"] == "str"          # int 与 str 冲突 → 提升为字符串
    assert by_name["a"]["from"] == ["l", "r"]
    assert by_name["b"]["missing"] == ["r"]       # 右入边没有 b 列
    assert by_name["c"]["missing"] == ["l"]
    assert [s["node_id"] for s in result["sources"]] == ["l", "r"]
    assert conns["g"].loaded == []                 # 推导不写目标


def test_derive_union_schema_reports_aliases(tmp_path):
    """5.6 结果列重命名：对齐结果带输出列名，重名时给出提示。"""
    left = _node("l", StepType.SOURCE_TABLE, conn_key="s1", object="t1")
    right = _node("r", StepType.SOURCE_TABLE, conn_key="s2", object="t2")
    union = _node("u", StepType.UNION, columns=["a", "b"],
                  aliases={"a": "uid", "b": "uid"})
    dst = _node("dst", StepType.TARGET_TABLE, conn_key="g", object="t3")
    pipe = PipelineConfig(
        nodes=[left, right, union, dst],
        edges=[PipelineEdge(source="l", target="u"),
               PipelineEdge(source="r", target="u"),
               PipelineEdge(source="u", target="dst")],
    )
    conns = {
        "s1": FakeConn([{"a": 1, "b": "x"}]),
        "s2": FakeConn([{"a": "2", "b": "y"}]),
        "g": FakeConn(),
    }
    result = derive_union_schema(pipe, "u", connector_factory=lambda k: conns[k],
                                 limit=10, task_config={})
    by_name = {c["name"]: c for c in result["columns"]}
    assert by_name["a"]["target"] == "uid"
    assert any("结果列名重复" in n for n in result["notes"])
