"""AI 生成与创建记录：成败都留痕、提示词链、脱敏、分页与筛选、失败记录不等同落库。"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

from app.core.config import get_settings

GOOD_FIELD = {"source": "email", "target": "email", "kind": "expression",
              "expression": "lower(email)"}
BAD_FIELD = {"source": "email", "target": "email", "kind": "expression",
             "expression": "COALESCE(email, 'x')"}


def _client():
    # ignore_cleanup_errors：Windows + IDE 删除拦截会让 sqlite 临时目录清理失败，与断言无关
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    os.environ["SYNETRA_DATA_DIR"] = tmp.name
    get_settings.cache_clear()
    from app.main import app
    from app.store import Store as _Store

    s = _Store(get_settings().data_dir)
    s.upsert_user("admin", "admin", password="admin")
    s.create_user("dev", "developer", password="dev")
    return TestClient(app), s, tmp


def _cfg(fields=None) -> dict:
    return {
        "project": "demo", "name": "ai-task",
        "source_conn_key": "src", "target_conn_key": "tgt",
        "source_object": "orders", "target_object": "orders_dw",
        "mode": "append", "primary_keys": [],
        "incremental": {"field": "", "watermark_field": "", "first_run_policy": "all"},
        "fields": fields if fields is not None else [GOOD_FIELD],
    }


def _trace(prompts=None, draft=None) -> dict:
    return {
        "prompts": prompts if prompts is not None else ["每天抽订单到 dw", "水位用 create_time"],
        "draft": draft if draft is not None else _cfg(),
        "provider": "deepseek", "model": "fake-chat",
        "skills": ["expression@abcd1234"], "conversation_id": "conv-1",
    }


def _post_task(client, *, trace=None, fields=None):
    client.headers["X-Synetra-User"] = "dev"
    body = {"project": "demo", "name": "ai-task", "config": _cfg(fields)}
    if trace is not None:
        body["ai_trace"] = trace
    return client.post("/api/tasks", json=body)


# ---- 6.1 留痕（成功 / 失败） ----

def test_successful_creation_is_recorded_with_prompt_chain():
    client, s, tmp = _client()
    try:
        with client:
            r = _post_task(client, trace=_trace(["首轮描述", "改成全量", "主键用 id"]))
            assert r.status_code == 200

            recs = s.list_generations()
            assert len(recs) == 1
            rec = recs[0]
            assert rec["status"] == "success"
            assert rec["task_key"] == r.json()["task_key"]
            assert rec["task_name"] == "ai-task"
            assert rec["project"] == "demo"
            assert rec["actor"] == "dev"
            assert rec["prompts"] == ["首轮描述", "改成全量", "主键用 id"]
            assert rec["model"] == "fake-chat"
            assert rec["conversation_id"] == "conv-1"
            assert rec["draft"]["source_object"] == "orders"
    finally:
        client.close(); tmp.cleanup()


def test_failed_creation_is_recorded_and_task_not_saved():
    client, s, tmp = _client()
    try:
        with client:
            r = _post_task(client, trace=_trace(), fields=[BAD_FIELD])
            assert r.status_code == 422

            recs = s.list_generations()
            assert len(recs) == 1
            assert recs[0]["status"] == "failed"
            assert recs[0]["task_key"] == ""
            assert "白名单" in recs[0]["error"]
            # 失败记录不等于任务落库
            assert s.list_tasks("demo") == []
    finally:
        client.close(); tmp.cleanup()


def test_manual_save_without_trace_writes_no_record():
    client, s, tmp = _client()
    try:
        with client:
            assert _post_task(client).status_code == 200
            assert s.list_generations() == []
    finally:
        client.close(); tmp.cleanup()


# ---- 6.1 脱敏 ----

def test_record_redacts_credentials():
    client, s, tmp = _client()
    try:
        with client:
            trace = _trace(
                prompts=["连 mysql://root:s3cretPw@db:3306/ods 抽数"],
                draft={"project": "demo", "password": "topsecret",
                       "source_object": "orders"},
            )
            assert _post_task(client, trace=trace).status_code == 200

            rec = s.list_generations()[0]
            assert "s3cretPw" not in rec["prompts"][0]
            assert "topsecret" not in str(rec["draft"])
            assert "SECRET" in rec["prompts"][0] or "***" in rec["prompts"][0]
    finally:
        client.close(); tmp.cleanup()


# ---- 6.2 分页与筛选 ----

def test_pagination_and_status_filter_follow_runs_contract():
    client, s, tmp = _client()
    try:
        with client:
            for i in range(25):
                _post_task(client, trace=_trace([f"第{i}次"]))
            assert len(s.list_generations(limit=100)) == 25

            client.headers["X-Synetra-User"] = "dev"
            first = client.get("/api/llm/generations", params={"page": 1, "page_size": 20}).json()
            assert first["total"] == 25
            assert first["pages"] == 2
            assert first["page_size"] == 20
            assert len(first["records"]) == 20

            second = client.get("/api/llm/generations", params={"page": 2, "page_size": 20}).json()
            assert len(second["records"]) == 5
            # 最新在前
            assert first["records"][0]["prompts"] == ["第24次"]

            # 非法的每页条数就近归一
            odd = client.get("/api/llm/generations", params={"page_size": 13}).json()
            assert odd["page_size"] == 10
            # 超出范围的页码被回夹
            clamped = client.get("/api/llm/generations", params={"page": 99, "page_size": 20}).json()
            assert clamped["page"] == 2
    finally:
        client.close(); tmp.cleanup()


def test_status_filter_and_invalid_status():
    client, _, tmp = _client()
    try:
        with client:
            _post_task(client, trace=_trace())
            _post_task(client, trace=_trace(), fields=[BAD_FIELD])

            client.headers["X-Synetra-User"] = "dev"
            ok = client.get("/api/llm/generations", params={"status": "success"}).json()
            assert [r["status"] for r in ok["records"]] == ["success"]
            assert ok["status_counts"] == {"success": 1, "failed": 1}

            bad = client.get("/api/llm/generations", params={"status": "running"})
            assert bad.status_code == 422
            assert "非法状态" in bad.json()["detail"]
    finally:
        client.close(); tmp.cleanup()


def test_records_require_developer_role():
    client, s, tmp = _client()
    try:
        with client:
            s.create_user("reader", "readonly", password="ro")
            client.headers["X-Synetra-User"] = "reader"
            assert client.get("/api/llm/generations").status_code == 403
    finally:
        client.close(); tmp.cleanup()


def test_store_generation_filters_are_project_scoped():
    _, s, tmp = _client()
    try:
        s.save_generation_record(actor="a", project="p1", status="success", prompts=["x"])
        s.save_generation_record(actor="a", project="p2", status="failed", prompts=["y"],
                                 error="boom")
        assert s.generation_totals(project="p1")["count"] == 1
        assert s.generation_totals(project="p2", status="failed")["count"] == 1
        assert s.generation_totals(project="p2", status="success")["count"] == 0
        assert s.generation_status_counts(project="p2") == {"failed": 1}
        assert s.get_generation(s.list_generations(project="p2")[0]["id"])["error"] == "boom"
    finally:
        tmp.cleanup()
