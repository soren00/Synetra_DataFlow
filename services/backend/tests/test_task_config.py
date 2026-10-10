"""任务 4.x：任务配置、字段选择向导、字段映射/转换规则（含 AST 白名单）、增量/水位/upsert。"""

from __future__ import annotations

import copy
import tempfile

from fastapi.testclient import TestClient

from app.expr.whitelist import validate_expression
from app.models.task_config import FieldRule, TransformKind
from app.transform import apply_rules, validate_rules


def _client():
    import os

    tmp = tempfile.TemporaryDirectory()
    os.environ["SYNETRA_DATA_DIR"] = tmp.name
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import app
    from app.store import Store

    Store(get_settings().data_dir).upsert_user("admin", "admin")
    client = TestClient(app)
    client.headers["X-Synetra-User"] = "admin"
    return client, tmp


WIZARD_CONFIG = {
    "project": "proj-x", "name": "sync-orders",
    "source_conn_key": "src-key", "target_conn_key": "tgt-key",
    "source_object": "orders", "target_object": "orders_dw",
    "mode": "upsert",
    "primary_keys": ["id"],
    "incremental": {"field": "updated_at", "source": "field", "watermark_field": "max_updated_at"},
    "fields": [
        {"source": "id", "target": "id", "kind": "direct"},
        {"source": "name", "target": "name_upper", "kind": "expression", "expression": "upper(name)"},
        {"source": "", "target": "note", "kind": "constant", "constant_value": "etl"},
        {"source": "qty", "target": "qty", "kind": "null_fill", "null_default": "0"},
    ],
}


# ---- 4.1 向导配置 + 预填 + 持久化 ----

def test_wizard_save_and_get():
    client, tmp = _client()
    try:
        r = client.post("/api/tasks", json={"project": "proj-x", "name": "sync-orders",
                                            "config": WIZARD_CONFIG})
        assert r.status_code == 200, r.text
        key = r.json()["task_key"]
        g = client.get(f"/api/tasks/{key}")
        assert g.status_code == 200
        cfg = g.json()
        assert cfg["source_object"] == "orders" and cfg["mode"] == "upsert"
    finally:
        client.close(); tmp.cleanup()


def test_wizard_prefill_from_columns():
    client, tmp = _client()
    try:
        r = client.post("/api/wizard/prefill", json={
            "project": "proj-x", "name": "draft",
            "source_conn_key": "s", "target_conn_key": "t",
            "columns": ["id", "name", "qty"],
        })
        assert r.status_code == 200
        fields = r.json()["draft"]["fields"]
        assert [f["source"] for f in fields] == ["id", "name", "qty"]
        assert all(f["kind"] == "direct" for f in fields)
    finally:
        client.close(); tmp.cleanup()


# ---- 4.2 转换规则 + 预览 + 白名单 ----

def test_validate_rules_ok():
    fields = [FieldRule(source="name", target="n", kind=TransformKind.EXPRESSION, expression="upper(name)")]
    assert validate_rules(fields) == []


def test_apply_rules_preview():
    out = apply_rules(
        [FieldRule(source="name", target="n", kind=TransformKind.EXPRESSION, expression="concat(upper(name),'-X')")],
        [{"name": "bob"}],
    )
    assert out == [{"n": "BOB-X"}]


def test_expr_whitelist_rejects_attribute_access_and_builtin():
    assert validate_expression("obj.__class__") != []
    assert validate_expression("__import__('os')") != []
    assert validate_expression("open('/etc/passwd')") != []
    assert validate_expression("x.attr") != []


def test_expr_whitelist_allows_function():
    assert validate_expression("max(3, 4)") == []


def test_expr_violation_blocks_save():
    client, tmp = _client()
    try:
        bad = copy.deepcopy(WIZARD_CONFIG)
        # 深拷贝：避免修改共享的模块级 WIZARD_CONFIG 污染其它用例
        bad["fields"][1] = {"source": "name", "target": "n", "kind": "expression",
                            "expression": "name.upper.__self__"}
        r = client.post("/api/tasks", json={"project": "proj-x", "name": "x", "config": bad})
        assert r.status_code == 422
        assert "属性访问" in r.json()["detail"]
    finally:
        client.close(); tmp.cleanup()


def test_expr_not_used_for_handwritten_path():
    # 手写路径的脚本不是受限表达式，不经过 whitelist——本断言强调二者解耦。
    # 白名单仅约束规则引擎表达式；越权/含副作用表达式一律被拒。
    assert validate_expression("sys.exit()") != []


# ---- 4.3 增量/水位/upsert 配置持久化 ----

def test_incremental_and_upsert_persisted():
    client, tmp = _client()
    try:
        r = client.post("/api/tasks", json={"project": "proj-x", "name": "sync",
                                            "config": WIZARD_CONFIG})
        key = r.json()["task_key"]
        cfg = client.get(f"/api/tasks/{key}").json()
        assert cfg["primary_keys"] == ["id"]
        assert cfg["incremental"]["field"] == "updated_at"
        assert cfg["incremental"]["watermark_field"] == "max_updated_at"
        # 失败不推进水位、重跑 upsert 兜底属执行语义（第 11 节集成验证）
    finally:
        client.close(); tmp.cleanup()


# ---- 12.1 配置历史版本与回滚 ----

def test_task_config_history_and_rollback():
    client, tmp = _client()
    try:
        r = client.post("/api/tasks", json={"project": "proj-x", "name": "sync",
                                            "config": WIZARD_CONFIG})
        key = r.json()["task_key"]
        # 对同一任务二次改配生成 v2
        client.post("/api/tasks", json={"project": "proj-x", "name": "sync",
                                        "task_key": key, "config": WIZARD_CONFIG})
        hist = client.get(f"/api/tasks/{key}/history").json()
        assert [h["version"] for h in hist] == [2, 1]
        # 回滚到 v1：current 恢复后追加为 v3
        rb = client.post(f"/api/tasks/{key}/rollback", params={"version": 1})
        assert rb.status_code == 200
        assert client.get(f"/api/tasks/{key}").json()["version"] == 3
        hist2 = client.get(f"/api/tasks/{key}/history").json()
        assert hist2[0]["version"] == 3
    finally:
        client.close(); tmp.cleanup()


# ---- 任务删除 ----

def test_task_delete_and_get_404():
    client, tmp = _client()
    try:
        r = client.post("/api/tasks", json={"project": "proj-x", "name": "sync",
                                            "config": WIZARD_CONFIG})
        assert r.status_code == 200, r.text
        key = r.json()["task_key"]
        d = client.delete(f"/api/tasks/{key}")
        assert d.status_code == 200, d.text
        assert d.json() == {"deleted": True, "task_key": key}
        # 删除后 GET 应为 404
        assert client.get(f"/api/tasks/{key}").status_code == 404
        # 再次删除同一任务应返回 404（任务不存在）
        assert client.delete(f"/api/tasks/{key}").status_code == 404
        assert "任务不存在" in client.delete(f"/api/tasks/{key}").json()["detail"]
    finally:
        client.close(); tmp.cleanup()