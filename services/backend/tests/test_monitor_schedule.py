"""任务 8.x / 9.x：调度执行、运行历史、实时日志、指标、对账与阈值告警。"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.task_config import TaskConfig
from app.monitor import AlertEvaluator, Reconciler, sum_checksum
from app.schedule import (
    new_run_id,
    run_with_retry,
)
from app.store import Store


def _client():
    tmp = tempfile.TemporaryDirectory()
    os.environ["SYNETRA_DATA_DIR"] = tmp.name
    os.environ["SYNETRA_SCRIPTS_ROOT"] = tmp.name
    os.environ["SYNETRA_RETRY_TIMES"] = "0"  # 测试避免真实退避等待
    os.environ["SYNETRA_SMTP_HOST"] = ""  # 不真实发信
    get_settings.cache_clear()
    from app.main import app
    from app.store import Store as S

    S(get_settings().data_dir).upsert_user("admin", "admin")
    client = TestClient(app)
    client.headers["X-Synetra-User"] = "admin"
    return client, tmp


def _create_task(store: Store) -> str:
    cfg = TaskConfig(project="proj-x", name="sync-orders", source_conn_key="src",
                     target_conn_key="tgt", source_object="orders", target_object="orders_dw",
                     schedule="0 2 * * *")
    return store.save_task(cfg)


def test_next_cron_time_computes():
    # 外部调度引擎集成已移除；本地调度使用 5 字段 next_cron_time 计算下次运行时间。
    from app.schedule import next_cron_time

    assert next_cron_time("0 2 * * *") is not None


# ---- 8.3 失败重试 ----

def test_run_with_retry_success():
    store = Store(tempfile.mkdtemp())
    run_id = new_run_id()
    store.start_run(run_id, "T", "p", "manual")
    outcome = run_with_retry(store, run_id, lambda: {"status": "success", "rows": 5},
                             retry_times=2, retry_interval_s=0)
    assert outcome == "success"
    assert store.get_run(run_id)["rows_total"] == 5


def test_run_with_retry_fails_after_attempts():
    store = Store(tempfile.mkdtemp())
    run_id = new_run_id()
    store.start_run(run_id, "T", "p", "manual")
    outcome = run_with_retry(store, run_id,
                             lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                             retry_times=1, retry_interval_s=0)
    assert outcome == "failed"
    run = store.get_run(run_id)
    assert run["status"] == "failed" and run["error"] == "boom"


# ---- 9.1 运行历史 / 9.2 日志 ----

def test_run_lifecycle_and_logs():
    client, tmp = _client()
    try:
        store = Store(get_settings().data_dir)
        key = _create_task(store)
        run_id = new_run_id()
        store.start_run(run_id, key, "proj-x", "manual")
        store.append_run_log(run_id, "开始", level="info")
        store.finish_run(run_id, status="success", rows_total=12)
        runs = client.get("/api/runs").json()
        assert any(r["run_id"] == run_id for r in runs["runs"])
        logs = client.get(f"/api/runs/{run_id}/logs").json()
        assert any("开始" in lg["line"] for lg in logs["logs"])
    finally:
        client.close(); tmp.cleanup()


def test_run_not_found_404():
    client, tmp = _client()
    try:
        assert client.get("/api/runs/nope").status_code == 404
    finally:
        client.close(); tmp.cleanup()


def test_list_runs_pagination_and_status_filter():
    client, tmp = _client()
    try:
        store = Store(get_settings().data_dir)
        for i in range(25):
            rid = f"run-{i:02d}"
            store.start_run(rid, "T", "proj-x", "manual")
            store.finish_run(rid, status="success" if i % 2 else "failed", rows_total=2)

        first = client.get("/api/runs", params={"page": 1, "page_size": 10}).json()
        assert first["total"] == 25 and first["pages"] == 3 and first["page_size"] == 10
        assert len(first["runs"]) == 10
        assert first["runs"][0]["run_id"] == "run-24"  # 最新在前
        assert first["rows_sum"] == 50

        assert len(client.get("/api/runs", params={"page": 3, "page_size": 10}).json()["runs"]) == 5
        # 页码越界由服务端回夹到最后一页
        assert client.get("/api/runs", params={"page": 99, "page_size": 10}).json()["page"] == 3
        # 页大小只允许 10/20/50
        assert client.get("/api/runs", params={"page_size": 7}).json()["page_size"] == 10

        succ = client.get("/api/runs", params={"status": "success", "page_size": 10}).json()
        assert succ["total"] == 12
        assert all(r["status"] == "success" for r in succ["runs"])
        # 分段计数忽略状态筛选，覆盖全量
        assert succ["status_counts"] == {"success": 12, "failed": 13}
        # 非法状态由后端拒绝
        assert client.get("/api/runs", params={"status": "bogus"}).status_code == 422
    finally:
        client.close(); tmp.cleanup()


# ---- 9.3 指标 ----

def test_metrics_record_and_rows():
    client, tmp = _client()
    try:
        store = Store(get_settings().data_dir)
        run_id = new_run_id()
        store.start_run(run_id, "T", "p", "manual")
        r = client.post(f"/api/runs/{run_id}/metrics",
                        json={"cpu_percent": 12.5, "memory_mb": 256, "data_rows": 9})
        assert r.status_code == 200
        assert any(m["data_rows"] == 9 for m in client.get("/api/metrics").json())
    finally:
        client.close(); tmp.cleanup()


# ---- 9.4 对账 ----

def test_reconciler_consistent_and_diff():
    src = [{"id": 1, "n": "a"}, {"id": 2, "n": "b"}]
    assert Reconciler().reconcile("T", "p", src, [{"id": 2, "n": "b"}, {"id": 1, "n": "a"}])["consistent"] is True
    res = Reconciler().reconcile("T", "p", src, [{"id": 2, "n": "b"}])
    assert res["consistent"] is False
    assert res["details"]["conclusion"] == "不一致"
    assert any(d["in"] == "source_only" for d in res["details"]["diff_sample"])
    assert sum_checksum([{"a": 1}, {"a": 1}]) == sum_checksum([{"a": 1}, {"a": 1}])


def test_reconcile_endpoint_persist():
    client, tmp = _client()
    try:
        r = client.post("/api/reconcile",
                        json={"task_key": "T", "project": "p",
                              "source_rows": [{"id": 1}], "target_rows": [{"id": 1}]})
        assert r.status_code == 200
        body = r.json()
        assert body["consistent"] is True
        recs = client.get("/api/reconciliations").json()
        assert any(x["task_key"] == "T" for x in recs)
    finally:
        client.close(); tmp.cleanup()


# ---- 9.5 阈值告警 ----

def test_alert_rule_check_triggers_and_records():
    client, tmp = _client()
    try:
        r = client.post("/api/alerts",
                        json={"task_key": "T", "metric": "data_rows", "op": ">",
                              "threshold": 10})
        assert r.status_code == 200
        rid = r.json()["id"]
        # 未命中
        chk = client.post(f"/api/alerts/{rid}/check", json={"value": 5}).json()
        assert chk["triggered"] is False
        # 命中（SMTP 未配置 → disabled，不真实发信但仍记录事件）
        hit = client.post(f"/api/alerts/{rid}/check", json={"value": 99}).json()
        assert hit["triggered"] is True
        assert any(e["rule_id"] == rid for e in client.get("/api/alerts/events").json())
    finally:
        client.close(); tmp.cleanup()


def test_alert_evaluator_ops_executed():
    store = Store(tempfile.mkdtemp())
    ev = AlertEvaluator(store)
    assert ev.evaluate_rule({"op": ">", "threshold": 5}, 6) is True
    assert ev.evaluate_rule({"op": "<=", "threshold": 5}, 5) is True
    assert ev.evaluate_rule({"op": "==", "threshold": 5}, 6) is False
    # enabled=False 的规则在求值-通知层不触发
    assert ev.evaluate_and_notify({"id": 1, "op": ">", "threshold": 5,
                                   "enabled": False, "project": "p", "task_key": "T",
                                   "metric": "m", "notify_to": ""}, 6) is None
    # 命中且 enabled → 记录事件
    rule = {"id": 1, "op": ">", "threshold": 5, "enabled": True, "project": "p",
            "task_key": "T", "metric": "data_rows", "notify_to": ""}
    assert ev.evaluate_and_notify(rule, 99) is not None


# ---- 8.x 手动触发执行 ----

def test_run_manual_task_without_script_returns_status():
    client, tmp = _client()
    try:
        store = Store(get_settings().data_dir)
        key = _create_task(store)
        r = client.post(f"/api/tasks/{key}/run")
        assert r.status_code == 200
        assert r.json()["status"] in ("failed", "success")
        assert r.json()["trigger_via"] == "schedule"  # 任务带 schedule
    finally:
        client.close(); tmp.cleanup()


def test_schedule_registration_returns_binding():
    client, tmp = _client()
    try:
        store = Store(get_settings().data_dir)
        key = _create_task(store)
        r = client.post(f"/api/tasks/{key}/schedule", json={"schedule": "0 3 * * *"})
        assert r.status_code == 200
        assert r.json()["scheduled"] is True
        st = client.get(f"/api/tasks/{key}/schedule").json()
        assert st["schedule"] == "0 3 * * *"
    finally:
        client.close(); tmp.cleanup()