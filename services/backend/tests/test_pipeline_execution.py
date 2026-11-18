"""执行正确性（3.1~3.4 + 4.4~4.6）：真实执行通道、水位推进、步级指标、失败样本、逐跳血缘。"""

from __future__ import annotations

import json

from app.etl_sdk.pipeline.engine import write_snapshots
from app.metadata.catalog import Catalog, field_lineage_from_config
from app.models.pipeline import PipelineConfig, PipelineEdge, PipelineNode, StepType
from app.models.task_config import TaskConfig
from app.scripting import run_task as run_task_mod
from app.scripts import ScriptStore
from app.store import Store

CFG = {
    "project": "p", "name": "t",
    "source_conn_key": "s", "target_conn_key": "g",
    "source_object": "orders", "target_object": "dw_orders",
    "mode": "upsert", "primary_keys": ["id"],
    "incremental": {"field": "updated_at", "watermark_field": "max_updated_at"},
    "fields": [{"kind": "direct", "source": "id", "target": "id"}],
}


class _FakeResult:
    def __init__(self, ok=True, rows=3, watermark="2024-05-01", stderr="", failed=None):
        self.ok = ok
        self._rows = rows
        self.watermark = watermark
        self.stderr = stderr
        self.failed_step = failed or {}
        self.step_metrics = [
            {"node_id": "source", "type": "source_table", "in_rows": 0, "out_rows": rows,
             "rejected_rows": 0, "elapsed_ms": 1.0, "status": "success", "error": ""},
            {"node_id": "transform", "type": "select", "in_rows": rows, "out_rows": rows,
             "rejected_rows": 0, "elapsed_ms": 0.5, "status": "success", "error": ""},
            {"node_id": "target", "type": "target_table", "in_rows": rows, "out_rows": rows,
             "rejected_rows": 0, "elapsed_ms": 0.8, "status": "success", "error": ""},
        ]

    def total_records(self) -> int:
        return self._rows


def _setup(tmp_path):
    store = Store(str(tmp_path))
    key = store.save_task(TaskConfig.model_validate(CFG))
    scripts = ScriptStore(str(tmp_path / "scripts"))
    scripts.replace_current(key, "# noop\n", generated_by="test")
    return store, scripts, key


def test_watermark_roundtrip(tmp_path):
    store = Store(str(tmp_path))
    assert store.get_watermark("p.t") == ""
    store.set_watermark("p.t", "2024-01-01")
    assert store.get_watermark("p.t") == "2024-01-01"
    store.set_watermark("p.t", "2024-02-01")
    assert store.get_watermark("p.t") == "2024-02-01"


def test_execute_task_advances_watermark_on_success(tmp_path, monkeypatch):
    store, scripts, key = _setup(tmp_path)
    store.start_run("r1", key, "p", "schedule", 1, exec_channel="real",
                    batch_size=5000, commit_size=5000)
    monkeypatch.setattr(run_task_mod, "run_real_script", lambda *a, **k: _FakeResult())
    out = run_task_mod.execute_task(store, scripts, key, "r1")

    assert out["status"] == "success" and out["rows"] == 3
    assert store.get_watermark(key) == "2024-05-01"
    # 步级指标落库
    assert len(store.list_run_steps("r1")) == 3
    row = store.list_runs()[0]
    assert row["exec_channel"] == "real" and row["batch_size"] == 5000


def test_execute_task_keeps_watermark_on_failure(tmp_path, monkeypatch):
    store, scripts, key = _setup(tmp_path)
    store.set_watermark(key, "2024-01-01")
    store.start_run("r2", key, "p", "manual", 1)
    monkeypatch.setattr(
        run_task_mod, "run_real_script",
        lambda *a, **k: _FakeResult(ok=False, rows=0, watermark=None,
                                    stderr="boom", failed={"node": "transform",
                                                           "error": "bad expr"}))
    out = run_task_mod.execute_task(store, scripts, key, "r2")

    assert out["status"] == "failed" and out["node"] == "transform"
    assert store.get_watermark(key) == "2024-01-01"


def test_execute_task_reports_missing_script(tmp_path):
    store = Store(str(tmp_path))
    key = store.save_task(TaskConfig.model_validate(CFG))
    scripts = ScriptStore(str(tmp_path / "scripts"))
    out = run_task_mod.execute_task(store, scripts, key, "r3")
    assert out["status"] == "failed" and "脚本" in out["error"]


def test_snapshot_written_and_pruned(tmp_path):
    samples = {"transform": [{"id": 1}, {"id": 2}]}
    root = tmp_path / "runs"
    written = write_snapshots(samples, "run-1", root, keep=1)
    assert written and (root / "run-1" / "transform.jsonl").exists()
    lines = (root / "run-1" / "transform.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x) for x in lines] == [{"id": 1}, {"id": 2}]

    # 保留份数=1：新运行目录写入后旧目录被清理
    (tmp_path / "runs" / "run-0").mkdir()
    write_snapshots(samples, "run-2", root, keep=1)
    assert not (root / "run-0").exists()


def test_pipeline_lineage_multi_hop_and_union(tmp_path):
    left = PipelineNode(id="l", type=StepType.SOURCE_TABLE,
                        config={"conn_key": "c1", "object": "crm.users"})
    right = PipelineNode(id="r", type=StepType.SOURCE_TABLE,
                         config={"conn_key": "c2", "object": "erp.users"})
    union = PipelineNode(id="u", type=StepType.UNION, config={})
    pick = PipelineNode(id="pick", type=StepType.SELECT, config={"rules": [
        {"kind": "direct", "source": "id", "target": "uid"},
        {"kind": "constant", "source": "", "target": "src", "constant_value": "x"},
    ]})
    dst = PipelineNode(id="dst", type=StepType.TARGET_TABLE,
                       config={"conn_key": "g", "object": "dw.users"})
    pipe = PipelineConfig(
        nodes=[left, right, union, pick, dst],
        edges=[PipelineEdge(source="l", target="u"), PipelineEdge(source="r", target="u"),
               PipelineEdge(source="u", target="pick"), PipelineEdge(source="pick", target="dst")],
    )
    cfg = dict(CFG, pipeline=pipe.model_dump(mode="json"))
    edges = field_lineage_from_config(cfg)

    sources = {e["source"] for e in edges}
    # 多源合并：同一目标字段记录两条上游来源
    assert {"crm.users.id", "erp.users.id"} == sources
    assert all(e["target"] == "dw.users.uid" for e in edges)
    # 常量列没有原始列来源，不伪造血缘
    assert all(e["field"] != "src" for e in edges)
    # 逐跳路径：源 → union → pick → target
    paths = [e["path"] for e in edges if e.get("path")]
    assert paths and next(hop[0] for hop in paths[0]) in {"l", "r"}
    assert [hop[0] for hop in paths[0]][-1] == "dst"

    cat = Catalog(str(tmp_path))
    cat.store_lineage("p.t", edges)
    stored = cat.lineage_for_task("p.t")
    assert len(stored) == 2
    assert stored[0]["path"]


def test_legacy_lineage_unchanged(tmp_path):
    """旧配置等价编译后，字段级血缘与旧语义一致，并附带逐跳路径。"""
    edges = field_lineage_from_config(dict(CFG))
    assert [(e["source"], e["target"], e["field"]) for e in edges] == [
        ("orders.id", "dw_orders.id", "id")]
    assert [hop[0] for hop in edges[0]["path"]] == ["source", "transform", "target"]
