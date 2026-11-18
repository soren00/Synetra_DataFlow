"""流水线 API（4.1~4.4）：步骤清单、保存校验、节点预览、运行步级指标。"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

from authhelpers import act_as

CFG = {
    "project": "proj-a", "name": "s1",
    "source_conn_key": "src", "target_conn_key": "tgt",
    "source_object": "orders", "target_object": "dw_orders",
    "mode": "upsert", "primary_keys": ["id"],
    "incremental": {"field": "updated_at", "watermark_field": "max_updated_at"},
    "fields": [{"kind": "direct", "source": "id", "target": "id"}],
}

NODE_SRC = {"id": "src", "type": "source_table", "config": {"conn_key": "src", "object": "orders"}}
NODE_PICK = {"id": "pick", "type": "select",
             "config": {"rules": [{"kind": "direct", "source": "id", "target": "id"}]}}
NODE_DST = {"id": "dst", "type": "target_table",
            "config": {"conn_key": "tgt", "object": "dw_orders", "mode": "append"}}


def _client():
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    os.environ["SYNETRA_DATA_DIR"] = tmp.name
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import app
    from app.store import Store

    store = Store(get_settings().data_dir)
    store.upsert_user("dev", "developer")
    client = TestClient(app)
    act_as(client, "dev")
    return client, store, tmp


def test_steps_endpoint_lists_five_categories():
    client, _, tmp = _client()
    try:
        r = client.get("/api/pipeline/steps")
        assert r.status_code == 200, r.text
        steps = r.json()["steps"]
        assert {s["category"] for s in steps} == {"source", "merge", "row", "batch", "target"}
        union = next(s for s in steps if s["type"] == "union")
        assert union["min_in"] == 2
    finally:
        tmp.cleanup()


def test_save_accepts_pipeline_and_persists_it():
    client, store, tmp = _client()
    try:
        cfg = dict(CFG, pipeline={"nodes": [NODE_SRC, NODE_PICK, NODE_DST],
                                  "edges": [{"source": "src", "target": "pick"},
                                            {"source": "pick", "target": "dst"}]})
        r = client.post("/api/tasks", json={"project": "proj-a", "name": "s1", "config": cfg})
        assert r.status_code == 200, r.text
        key = r.json()["task_key"]
        saved = store.get_task(key)
        assert saved.pipeline is not None
        assert [n.id for n in saved.pipeline.nodes] == ["src", "pick", "dst"]
    finally:
        tmp.cleanup()


def test_legacy_config_returns_compiled_pipeline():
    client, _, tmp = _client()
    try:
        r = client.post("/api/tasks", json={"project": "proj-a", "name": "s1", "config": CFG})
        assert r.status_code == 200, r.text
        key = r.json()["task_key"]

        got = client.get(f"/api/tasks/{key}/pipeline")
        assert got.status_code == 200, got.text
        body = got.json()
        assert [n["type"] for n in body["pipeline"]["nodes"]] == [
            "source_table", "select", "target_table"]
        assert body["problems"] == []
        assert body["summary"]["batch_size"] > 0
    finally:
        tmp.cleanup()


def test_invalid_graph_rejected_with_located_reason():
    client, _, tmp = _client()
    try:
        # 成环：dst → pick
        cfg = dict(CFG, pipeline={
            "nodes": [NODE_SRC, NODE_PICK, NODE_DST],
            "edges": [{"source": "src", "target": "pick"},
                      {"source": "pick", "target": "dst"},
                      {"source": "dst", "target": "pick"}],
        })
        r = client.post("/api/tasks", json={"project": "proj-a", "name": "s1", "config": cfg})
        assert r.status_code == 422, r.text
        detail = str(r.json()["detail"])
        assert "环路" in detail or "环" in detail
    finally:
        tmp.cleanup()


def test_node_preview_returns_sample_and_does_not_write():
    client, _, tmp = _client()
    try:
        r = client.post("/api/tasks", json={"project": "proj-a", "name": "s1", "config": CFG})
        key = r.json()["task_key"]

        # 旧配置等价编译后节点标识为 source/transform/target
        body = {"sample": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}], "limit": 5}
        got = client.post(f"/api/tasks/{key}/nodes/transform/preview", json=body)
        assert got.status_code == 200, got.text
        payload = got.json()
        assert payload["node_id"] == "transform"
        assert [row["id"] for row in payload["sample"]] == [1, 2]
        metrics = {m["node_id"]: m for m in payload["metrics"]}
        assert metrics["transform"]["out_rows"] == 2
    finally:
        tmp.cleanup()


def test_node_preview_unknown_node_404():
    client, _, tmp = _client()
    try:
        r = client.post("/api/tasks", json={"project": "proj-a", "name": "s1", "config": CFG})
        key = r.json()["task_key"]
        got = client.post(f"/api/tasks/{key}/nodes/nope/preview", json={"sample": []})
        assert got.status_code == 404
    finally:
        tmp.cleanup()


def test_run_steps_endpoint():
    client, store, tmp = _client()
    try:
        r = client.post("/api/tasks", json={"project": "proj-a", "name": "s1", "config": CFG})
        key = r.json()["task_key"]
        store.start_run("run-1", key, "proj-a", "manual", 1)
        store.add_run_steps("run-1", [
            {"node_id": "src", "type": "source_table", "in_rows": 0, "out_rows": 10,
             "status": "success"},
            {"node_id": "pick", "type": "select", "in_rows": 10, "out_rows": 8,
             "rejected_rows": 2, "status": "success"},
        ])
        got = client.get("/api/runs/run-1/steps")
        assert got.status_code == 200, got.text
        steps = got.json()["steps"]
        assert [s["node_id"] for s in steps] == ["src", "pick"]
        assert steps[1]["rejected_rows"] == 2
    finally:
        tmp.cleanup()


def test_union_schema_endpoint_derives_alignment():
    """5.6 合并节点列对齐：接口返回入边列集合、对齐结果与类型，不写目标。"""
    client, _, tmp = _client()
    try:
        union_pipeline = {
            "nodes": [
                {"id": "a", "type": "source_table", "config": {"conn_key": "src", "object": "t1"}},
                {"id": "b", "type": "source_table", "config": {"conn_key": "src", "object": "t2"}},
                {"id": "u", "type": "union", "config": {}},
                {"id": "w", "type": "target_table",
                 "config": {"conn_key": "tgt", "object": "dw", "mode": "append"}},
            ],
            "edges": [{"source": "a", "target": "u"}, {"source": "b", "target": "u"},
                      {"source": "u", "target": "w"}],
        }
        cfg = dict(CFG, pipeline=union_pipeline)
        r = client.post("/api/tasks", json={"project": "proj-a", "name": "u1", "config": cfg})
        assert r.status_code == 200, r.text
        key = r.json()["task_key"]

        got = client.post(f"/api/tasks/{key}/nodes/u/schema",
                          json={"sample": [{"id": 1, "name": "x"}, {"qty": "2"}], "limit": 10})
        assert got.status_code == 200, got.text
        body = got.json()
        names = [c["name"] for c in body["columns"]]
        assert names == ["id", "name", "qty"]
        by_name = {c["name"]: c for c in body["columns"]}
        assert by_name["qty"]["type"] == "str"
        assert [s["node_id"] for s in body["sources"]] == ["a", "b"]
    finally:
        tmp.cleanup()
