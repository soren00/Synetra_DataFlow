"""T2-T6：LLM 配置闭环 + 两轮 RAG 草稿 + 三级降级链 + 草稿校验与佐证。"""

from __future__ import annotations

import json
import os
import tempfile

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.llm.provider import (
    LLMProvider,
    ProviderRequestError,
    get_provider,
    model_info,
    register_provider,
)
from app.models.connection import (
    ConnectionCreate,
    ConnectionDirection,
    ConnectionType,
)
from app.queries.executor import QueryResult
from app.store import Store


def _client():
    tmp = tempfile.TemporaryDirectory()
    os.environ["SYNETRA_DATA_DIR"] = tmp.name
    get_settings.cache_clear()
    from app.main import app
    from app.store import Store as _Store

    s = _Store(get_settings().data_dir)
    s.upsert_user("admin", "admin", password="admin")
    s.create_user("dev", "developer", password="dev")
    client = TestClient(app)
    return client, s, tmp


def _seed_conns(store: Store, data_dir: str):
    """创建源/目标连接并写入 Catalog 表结构；返回 (SRC, TGT)。"""
    src = store.create_connection(ConnectionCreate(
        project="demo", name="src", ctype="mysql",
        direction=ConnectionDirection.SOURCE,
        meta={"host": "127.0.0.1", "port": "3306", "db": "x", "user": "u"},
        secret="p"))
    tgt = store.create_connection(ConnectionCreate(
        project="demo", name="tgt", ctype="mysql",
        direction=ConnectionDirection.TARGET, meta={}, secret=None))
    from app.metadata import Catalog

    cat = Catalog(data_dir)
    cat.save_table_schema(src.conn_key, "orders", [
        {"name": "id", "ctype": "int", "primary": True},
        {"name": "status", "ctype": "varchar"},
    ])
    cat.save_table_schema(tgt.conn_key, "orders_dw", [
        {"name": "id", "ctype": "int", "primary": True},
    ])
    return src.conn_key, tgt.conn_key


class FakeProvider(LLMProvider):
    """按序返回固定 JSON 的假供应商；可注入非法输出验证自动重试。"""

    name = "deepseek"
    model_name = "fake-chat"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[str] = []
        self.histories: list[list[dict]] = []
        self.systems: list[str | None] = []

    def converse(self, messages: list[dict], *, system: str | None = None) -> str:
        self.histories.append([dict(m) for m in messages])
        self.systems.append(system)
        self.calls.append(messages[-1]["content"] if messages else "")
        return self.replies.pop(0) if self.replies else "{}"

    def list_models(self):
        return [model_info("fake-chat", "fake", ["chat"])]


def _skeleton_json(src: str, tgt: str) -> str:
    return json.dumps({
        "source_conn_key": src, "source_object": "orders",
        "target_conn_key": tgt, "target_object": "orders_dw",
        "mode": "append", "primary_keys": ["id"],
        "incremental_field": "updated_at", "watermark_field": "updated_at",
    })


def _full_json(src: str, tgt: str, *, fake_target_col: bool = True) -> str:
    fields = [{"source": "id", "target": "id", "kind": "direct"}]
    if fake_target_col:
        fields.append({"source": "status", "target": "status", "kind": "direct"})
    return json.dumps({
        "name": "订单增量同步", "source_conn_key": src, "source_object": "orders",
        "target_conn_key": tgt, "target_object": "orders_dw",
        "mode": "append", "primary_keys": ["id"],
        "incremental": {"field": "updated_at", "watermark_field": "updated_at",
                        "first_run_policy": "all"},
        "fields": fields, "schedule": "",
    })


# ---- T2：配置闭环（Vault 加解密往返 / 掩码 / 吊销 / 非 admin 403） ----

def test_config_put_get_mask_and_no_plaintext():
    client, s, tmp = _client()
    try:
        with client:
            client.headers["X-Synetra-User"] = "admin"
            r = client.put("/api/llm/config", json={
                "provider": "deepseek", "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat", "api_key": "sk-test-12345678"})
            assert r.status_code == 200
            masked = r.json()["api_key_masked"]
            assert masked.endswith("5678") and "sk-test" not in masked
            g = client.get("/api/llm/config").json()
            assert "sk-test" not in json.dumps(g)
            # vault 解密往返：密文可还原明文
            row = s.get_llm_config_row()
            assert row["api_key_cipher"]
            assert s.vault.decrypt(row["api_key_cipher"]).decode() == "sk-test-12345678"
    finally:
        client.close(); tmp.cleanup()


def test_config_revoke_then_generate_503():
    client, s, tmp = _client()
    try:
        with client:
            client.headers["X-Synetra-User"] = "admin"
            client.put("/api/llm/config", json={"api_key": "sk-test-12345678"})
            r = client.delete("/api/llm/config/key")
            assert r.status_code == 200 and r.json()["revoked"] is True
            g = client.get("/api/llm/config").json()
            assert g["has_api_key"] is False
            # 吊销后生成 → 503 引导
            from app.models.task_config import TaskConfig

            key = s.save_task(TaskConfig(project="p", name="t",
                                         source_conn_key="src", target_conn_key="tgt"))
            r2 = client.post("/api/scripts/generate", json={"task_key": key})
            assert r2.status_code == 503 and "配置" in r2.json()["detail"]
    finally:
        client.close(); tmp.cleanup()


def test_config_non_admin_403():
    client, _s, tmp = _client()
    try:
        with client:
            client.headers["X-Synetra-User"] = "dev"
            assert client.get("/api/llm/config").status_code == 403
            assert client.put("/api/llm/config", json={}).status_code == 403
            assert client.post("/api/llm/config/test", json={}).status_code == 403
            assert client.delete("/api/llm/config/key").status_code == 403
    finally:
        client.close(); tmp.cleanup()


def test_config_test_invalid_key_returns_reason():
    client, _s, tmp = _client()
    try:
        with client:
            client.headers["X-Synetra-User"] = "admin"
            r = client.post("/api/llm/config/test", json={
                "base_url": "http://127.0.0.1:1", "api_key": "bad-key"})
            assert r.status_code == 200
            body = r.json()
            assert body["ok"] is False and body["reason"]
    finally:
        client.close(); tmp.cleanup()


# ---- T4：两轮 RAG + 单轮修订 + 非法输出重试 + 审计 ----

def test_draft_two_rounds_with_catalog_and_warnings():
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            register_provider("deepseek", FakeProvider([_skeleton_json(src, tgt), _full_json(src, tgt)]))
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={
                "description": "每天从 orders 增量抽已支付订单到 orders_dw",
                "project": "demo",
                "connections": [{"conn_key": src, "name": "src", "ctype": "mysql", "project": "demo"}]})
            assert r.status_code == 200
            body = r.json()
            assert body["schema_source"] == "catalog" and body["inferred_fields"] is False
            assert body["draft"]["mode"] == "append"
            assert body["draft"]["incremental"]["field"] == "updated_at"
            assert body["warnings"] == ["目标字段「status」不在目标表 orders_dw 已知列内"]
            assert body["sample"]["available"] is False  # 本地无 MySQL → 预览失败不阻断
            # 修订轮：单轮
            register_provider("deepseek", FakeProvider([json.dumps({
                "name": "订单全量同步", "source_conn_key": src, "source_object": "orders",
                "target_conn_key": tgt, "target_object": "orders_dw",
                "mode": "append", "primary_keys": [],
                "incremental": {"field": "", "watermark_field": "", "first_run_policy": "all"},
                "fields": [{"source": "id", "target": "id", "kind": "direct"}],
                "schedule": ""})]))
            r2 = client.post("/api/llm/tasks/draft/revise", json={
                "draft": body["draft"], "instruction": "改成全量", "project": "demo"})
            assert r2.status_code == 200
            b2 = r2.json()
            assert b2["draft"]["mode"] == "append" and b2["draft"]["incremental"]["field"] == ""
            assert len(b2["draft"]["fields"]) == 1  # 字段映射保持稳定（仅删除多余字段）
    finally:
        client.close(); tmp.cleanup()


def test_draft_user_picked_connections_override_skeleton():
    """用户下拉指定的源/目标连接优先于 LLM 骨架输出；project 可选。"""
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            # 骨架轮故意输出错误连接 → 应被请求中用户指定值强制覆盖
            register_provider("deepseek", FakeProvider([
                _skeleton_json("WRONG_SRC", "WRONG_TGT"), _full_json(src, tgt)]))
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={
                "description": "同步订单",
                "connections": [
                    {"conn_key": src, "name": "src", "ctype": "mysql", "project": "demo"},
                    {"conn_key": tgt, "name": "tgt", "ctype": "mysql", "project": "demo"}],
                "source_conn_key": src, "target_conn_key": tgt})  # project 缺省=可选
            assert r.status_code == 200
            body = r.json()
            assert body["draft"]["source_conn_key"] == src
            assert body["draft"]["target_conn_key"] == tgt
            assert body["draft"]["project"] == ""
    finally:
        client.close(); tmp.cleanup()


def test_draft_provider_request_error_no_retry_502():
    """请求层错误（如 DeepSeek 400）不重试，直接透出 502 与可读原因。"""
    client, _s, tmp = _client()
    try:
        with client:
            register_provider("deepseek", FakeProvider(["ignored"]))
            p = get_provider("deepseek")
            orig = p.converse
            p.converse = lambda messages, *, system=None: (_ for _ in ()).throw(
                ProviderRequestError("请求被拒绝（HTTP 400）：Model Not Exist"))
            try:
                client.headers["X-Synetra-User"] = "dev"
                r = client.post("/api/llm/tasks/draft", json={"description": "同步", "project": "demo"})
                assert r.status_code == 502
                assert "Model Not Exist" in r.json()["detail"]
            finally:
                p.converse = orig
    finally:
        client.close(); tmp.cleanup()


def test_draft_invalid_json_auto_retry_then_success():
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            p = FakeProvider(["not json at all", _skeleton_json(src, tgt), _full_json(src, tgt)])
            register_provider("deepseek", p)
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={"description": "同步", "project": "demo"})
            assert r.status_code == 200
            assert len(p.calls) == 3  # first 失败 → first 重试 → second
    finally:
        client.close(); tmp.cleanup()


def test_draft_all_invalid_returns_422():
    client, _s, tmp = _client()
    try:
        with client:
            p = FakeProvider(["bad", "bad2"])
            register_provider("deepseek", p)
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={"description": "同步", "project": "demo"})
            assert r.status_code == 422
            assert len(p.calls) == 2  # 第一轮耗尽重试即中止
            assert "first" in r.json()["detail"]
    finally:
        client.close(); tmp.cleanup()


def test_draft_audit_events_logged():
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            register_provider("deepseek", FakeProvider([
                "bad", _skeleton_json(src, tgt), _full_json(src, tgt)]))
            client.headers["X-Synetra-User"] = "dev"
            client.post("/api/llm/tasks/draft", json={"description": "同步", "project": "demo"})
            events = [e for e in s.list_events(limit=50) if e["action"] == "llm_draft_call"]
            assert len(events) == 3
            d = [json.loads(e["detail_json"]) for e in events]
            # list_events 按 id DESC（最新在前）：[second 成功, first 重试成功, first 失败]
            assert [x["ok"] for x in d] == [True, True, False]
            assert d[2]["round"] == "first" and d[0]["round"] == "second"
            assert d[0]["model"] == "fake-chat" and d[0]["provider"] == "deepseek"
            assert all(e["actor"] == "dev" for e in events)
            # 多轮审计：带会话标识与轮次序号；并记录本次注入的技能包指纹
            assert d[0]["turn"] == 2 and d[2]["turn"] == 1
            assert d[0]["conversation_id"] == d[2]["conversation_id"]
            assert any(s.startswith("expression@") for s in d[0]["skills"])
    finally:
        client.close(); tmp.cleanup()


# ---- T6：虚构连接拒绝 ----

def test_draft_fake_conn_rejected():
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            bad_full = json.loads(_full_json(src, tgt))
            bad_full["source_conn_key"] = "NOPE"
            register_provider("deepseek", FakeProvider([_skeleton_json(src, tgt), json.dumps(bad_full)]))
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={"description": "同步", "project": "demo"})
            assert r.status_code == 422
            assert "不存在" in r.json()["detail"]
    finally:
        client.close(); tmp.cleanup()


# ---- T5：三级降级链 ----

def test_schema_tier_catalog_no_extra_query():
    from app.llm import task_draft

    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            calls = []
            orig = task_draft.run_select

            def spy(*a, **k):
                calls.append(a)
                return orig(*a, **k)

            task_draft.run_select = spy
            try:
                register_provider("deepseek", FakeProvider([_skeleton_json(src, tgt), _full_json(src, tgt)]))
                client.headers["X-Synetra-User"] = "dev"
                r = client.post("/api/llm/tasks/draft", json={"description": "同步", "project": "demo"})
                assert r.status_code == 200
                assert r.json()["schema_source"] == "catalog"
                # Catalog 命中时不走 information_schema 查询；仅有样例预览的 SELECT
                sqls = [a[3] for a in calls]
                assert not any("information_schema" in sql for sql in sqls)
                assert sqls == ["SELECT * FROM orders LIMIT 10"]
            finally:
                task_draft.run_select = orig
    finally:
        client.close(); tmp.cleanup()


def test_schema_tier_information_schema_via_run_select(monkeypatch):
    from app.llm import task_draft

    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            # 清掉 Catalog 里的源表 → 走 ②
            from app.metadata import Catalog

            Catalog(tmp.name).save_table_schema(src, "orders", [])
            captured = {"sqls": []}

            def fake_run_select(ctype, meta, secret, sql, limit=200, offset=0, timeout_ms=5000):
                captured["sqls"].append(sql)
                return QueryResult(columns=["column_name", "data_type"],
                                   rows=[["id", "int"], ["status", "varchar"]],
                                   count=2, truncated=False)

            monkeypatch.setattr(task_draft, "run_select", fake_run_select)
            register_provider("deepseek", FakeProvider([_skeleton_json(src, tgt), _full_json(src, tgt)]))
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={"description": "同步", "project": "demo"})
            assert r.status_code == 200
            body = r.json()
            assert body["schema_source"] == "information_schema" and body["inferred_fields"] is False
            assert any("information_schema.columns" in sql for sql in captured["sqls"])
            assert any(sql.startswith("SELECT * FROM orders") for sql in captured["sqls"])
    finally:
        client.close(); tmp.cleanup()


def test_schema_tier_inferred_when_unreachable():
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            # 清掉 Catalog 源表；连接指向不可达地址 → ② 失败 → ③ 推断
            from app.metadata import Catalog

            Catalog(tmp.name).save_table_schema(src, "orders", [])
            s.update_connection(src, ConnectionCreate(
                project="demo", name="src", ctype="mysql",
                direction=ConnectionDirection.SOURCE,
                meta={"host": "192.0.2.1", "port": "3306", "db": "x", "user": "u"},
                secret="p"))
            register_provider("deepseek", FakeProvider([_skeleton_json(src, tgt), _full_json(src, tgt)]))
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={"description": "同步", "project": "demo"})
            assert r.status_code == 200
            body = r.json()
            assert body["schema_source"] == "inferred" and body["inferred_fields"] is True
            assert any("推断" in w for w in body["warnings"])
            assert body["sample"]["available"] is False
    finally:
        client.close(); tmp.cleanup()


def test_information_schema_sql_escapes_quotes():
    from app.llm.task_draft import _information_schema_sql

    sql = _information_schema_sql(ConnectionType.MYSQL, "orders")
    assert "information_schema.columns" in sql and "DATABASE()" in sql
    sql2 = _information_schema_sql(ConnectionType.MYSQL, "db.o'rders")
    assert "o''rders" in sql2 and "table_schema = 'db'" in sql2
    assert _information_schema_sql(ConnectionType.API, "t") is None


# ---- 多轮会话：轮次历史契约 / 追加 / 裁剪 ----

def test_session_system_injects_expression_skill_once():
    """技能包作为会话级 system 注入一次，且不进入轮次历史（不随轮次重复膨胀）。"""
    from app.llm.task_draft import session_system

    system = session_system()
    assert "# 技能包：expression" in system
    assert "受支持函数清单" in system and "禁止项清单" in system
    assert "选择判据" in system

    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            p = FakeProvider([_skeleton_json(src, tgt), _full_json(src, tgt)])
            register_provider("deepseek", p)
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={"description": "同步", "project": "demo"})
            history = r.json()["history"]
            # 历史里不含技能包正文（只在 system 里出现一次）
            assert all("受支持函数清单" not in m["content"] for m in history)
            assert all("受支持函数清单" in (sys or "") for sys in p.systems)
    finally:
        client.close(); tmp.cleanup()


def test_session_history_contract_rejects_illegal_shapes():
    import pytest

    from app.llm.session import InvalidHistory, validate_history

    for bad in (
        "not-a-list",
        [{"role": "system", "content": "x"}],      # system 由服务端重建，回传即非法
        [{"role": "assistant", "content": "x"}],   # 首条必须是 user
        [{"role": "user"}],                        # 缺 content
        [{"role": "user", "content": 1}],          # content 非字符串
        [{"role": "tool", "content": "x"}],        # 角色不在白名单
    ):
        with pytest.raises(InvalidHistory):
            validate_history(bad)
    assert validate_history(None) == [] and validate_history([]) == []


def test_session_history_trim_keeps_user_first():
    from app.llm.session import append_turn, trim_history

    history: list[dict] = []
    for i in range(11):  # 11 轮 = 22 条，超出 20 条上限
        history = append_turn(history, "user", f"u{i}")
        history = append_turn(history, "assistant", f"a{i}")
    kept, trimmed = trim_history(history)
    assert trimmed is True and len(kept) == 20 and kept[0]["role"] == "user"

    # 字符上限触发裁剪（保留最近内容）
    kept2, trimmed2 = trim_history(history, max_turns=100, max_chars=6)
    assert trimmed2 is True and sum(len(m["content"]) for m in kept2) <= 6


def test_draft_multiturn_history_appended_and_returned():
    """第二轮在会话历史上追加（骨架结论与需求不再重发）；响应回传模型可见历史。"""
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            p = FakeProvider([_skeleton_json(src, tgt), _full_json(src, tgt)])
            register_provider("deepseek", p)
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={
                "description": "每天从 orders 增量抽已支付订单到 orders_dw",
                "project": "demo"})
            assert r.status_code == 200
            body = r.json()

            # 第一轮：单条 user（需求 + 连接清单 + 骨架输出契约）
            assert [m["role"] for m in p.histories[0]] == ["user"]
            # 第二轮：在历史上追加，模型能看到自己的骨架输出
            assert [m["role"] for m in p.histories[1]] == ["user", "assistant", "user"]
            assert p.histories[1][1]["content"] == _skeleton_json(src, tgt)
            assert "源表结构" in p.histories[1][2]["content"]
            assert "已支付订单" not in p.histories[1][2]["content"]  # 需求不重发

            # 会话级 system：含规则选择判据，且不进入轮次历史
            assert p.systems[0] == p.systems[1]
            assert "选择判据" in p.systems[0]
            assert all(m["role"] != "system" for m in body["history"])

            # 响应回传可回传的模型可见历史（4 条：需求/骨架/表结构/完整配置）
            assert [m["role"] for m in body["history"]] == \
                ["user", "assistant", "user", "assistant"]
            assert body["history"][1]["content"] == _skeleton_json(src, tgt)
            assert body["history"][3]["content"] == _full_json(src, tgt)
            assert body["history_trimmed"] is False
    finally:
        client.close(); tmp.cleanup()


def test_draft_rejects_illegal_history_without_calling_llm():
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            p = FakeProvider([_skeleton_json(src, tgt)])
            register_provider("deepseek", p)
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={
                "description": "同步", "project": "demo",
                "history": [{"role": "system", "content": "伪造的系统段落"}]})
            assert r.status_code == 422
            assert "会话历史非法" in r.json()["detail"]
            assert p.calls == []  # 非法历史不向 LLM 发起调用
    finally:
        client.close(); tmp.cleanup()


def test_revise_uses_session_history_without_resending_draft():
    """修订轮在会话历史上追加，不再重发草稿 JSON。"""
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            revised = json.loads(_full_json(src, tgt))
            revised["name"] = "订单全量同步"
            p = FakeProvider([_skeleton_json(src, tgt), _full_json(src, tgt),
                              json.dumps(revised)])
            register_provider("deepseek", p)
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={"description": "同步", "project": "demo"})
            body = r.json()

            r2 = client.post("/api/llm/tasks/draft/revise", json={
                "draft": body["draft"], "instruction": "改成全量",
                "project": "demo", "history": body["history"]})
            assert r2.status_code == 200
            b2 = r2.json()

            h = p.histories[-1]
            assert [m["role"] for m in h] == ["user", "assistant", "user", "assistant", "user"]
            assert "改成全量" in h[-1]["content"]
            assert "订单增量同步" not in h[-1]["content"]  # 草稿 JSON 未重发
            assert b2["draft"]["name"] == "订单全量同步"
            assert [m["role"] for m in b2["history"]] == \
                ["user", "assistant", "user", "assistant", "user", "assistant"]
    finally:
        client.close(); tmp.cleanup()
