"""AI 生成表达式：白名单校验、受限修复轮、上下文不含样例数据行、审计留痕。"""

from __future__ import annotations

import json
import os
import tempfile

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.llm.provider import LLMProvider, model_info, register_provider
from app.store import Store

#: 反例：SQL 方言写法（会被白名单拒绝）
BAD = "CONCAT(LEFT(phone, 3), '****', RIGHT(phone, 4))"
#: 正例：切片写法
GOOD = "concat(phone[0:3], '****', phone[7:])"


def _client():
    # ignore_cleanup_errors：Windows + IDE 的删除拦截会让 sqlite 临时目录清理失败，
    # 与用例断言无关，不应让用例变红。
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    os.environ["SYNETRA_DATA_DIR"] = tmp.name
    get_settings.cache_clear()
    from app.main import app
    from app.store import Store as _Store

    s = _Store(get_settings().data_dir)
    s.upsert_user("admin", "admin", password="admin")
    s.create_user("dev", "developer", password="dev")
    return TestClient(app), s, tmp


class FakeProvider(LLMProvider):
    """按序返回固定输出的假供应商；记录每轮消息与 system。"""

    name = "deepseek"
    model_name = "fake-chat"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.histories: list[list[dict]] = []
        self.systems: list[str | None] = []

    def converse(self, messages: list[dict], *, system: str | None = None) -> str:
        self.histories.append([dict(m) for m in messages])
        self.systems.append(system)
        return self.replies.pop(0) if self.replies else "{}"

    def list_models(self):
        return [model_info("fake-chat", "fake", ["chat"])]


def _events(store: Store) -> list[dict]:
    return [json.loads(e["detail_json"]) for e in store.list_events(limit=50)
            if e["action"] == "llm_expression_call"]


def _post(client, **body):
    client.headers["X-Synetra-User"] = "dev"
    return client.post("/api/llm/expression", json=body)


# ---- 生成与校验 ----

def test_generates_compliant_expression_in_one_call():
    client, _, tmp = _client()
    try:
        with client:
            p = FakeProvider([GOOD])
            register_provider("deepseek", p)
            r = _post(client, prompt="手机号中间四位打码",
                      source_field="phone", target_field="phone_masked",
                      columns=["phone", "name"])

            assert r.status_code == 200
            body = r.json()
            assert body["ok"] is True
            assert body["expression"] == GOOD
            assert body["issues"] == []
            assert len(p.histories) == 1  # 合规不多打一轮
    finally:
        client.close(); tmp.cleanup()


def test_repairs_once_when_candidate_violates_whitelist():
    client, _, tmp = _client()
    try:
        with client:
            p = FakeProvider([BAD, GOOD])
            register_provider("deepseek", p)
            r = _post(client, prompt="手机号打码", source_field="phone",
                      columns=["phone"])

            assert r.status_code == 200
            assert r.json()["ok"] is True
            assert r.json()["expression"] == GOOD
            assert r.json()["attempts"] == 2

            # 修复轮在同一上下文里追加：回喂候选与违规原因，并带上技能包写法要点
            repair_turn = p.histories[1][-1]["content"]
            assert BAD in repair_turn
            assert "白名单" in repair_turn
            assert len(p.histories[1]) == 3  # user(需求) + assistant(候选) + user(修复)
            system = p.systems[1] or ""
            assert "切片" in system and "可用函数" in system
    finally:
        client.close(); tmp.cleanup()


def test_repair_is_capped_and_reports_reason_without_filling_candidate():
    """达上限仍违规：仅调用两次、返回可读原因，且不回填候选（不覆盖用户输入）。"""
    client, _, tmp = _client()
    try:
        with client:
            p = FakeProvider([BAD, BAD])
            register_provider("deepseek", p)
            r = _post(client, prompt="手机号打码", source_field="phone", columns=["phone"])

            assert r.status_code == 200
            body = r.json()
            assert body["ok"] is False
            assert body["expression"] == ""
            assert body["issues"] and "函数不在白名单" in body["issues"][0]
            assert len(p.histories) == 2  # 上限 1 次修复，不无界重试
    finally:
        client.close(); tmp.cleanup()


def test_tolerates_fenced_and_prefixed_output():
    client, _, tmp = _client()
    try:
        with client:
            register_provider("deepseek", FakeProvider([f"```python\nexpression = {GOOD}\n```"]))
            r = _post(client, prompt="打码", source_field="phone", columns=["phone"])
            assert r.json()["expression"] == GOOD
    finally:
        client.close(); tmp.cleanup()


# ---- 上下文脱敏 ----

def test_context_carries_column_names_but_no_sample_rows():
    client, _, tmp = _client()
    try:
        with client:
            p = FakeProvider([GOOD])
            register_provider("deepseek", p)
            _post(client, prompt="手机号中间四位打码", source_field="phone",
                  target_field="phone_masked", columns=["phone", "name"])

            user_msg = p.histories[0][0]["content"]
            assert "phone" in user_msg and "name" in user_msg      # 列名可用
            assert "13800138001" not in user_msg                    # 样例行数据不出平台
            assert "13800138001" not in (p.systems[0] or "")
    finally:
        client.close(); tmp.cleanup()


# ---- 审计与错误处理 ----

def test_audit_records_model_and_skill_fingerprints():
    client, s, tmp = _client()
    try:
        with client:
            register_provider("deepseek", FakeProvider([GOOD]))
            _post(client, prompt="打码", source_field="phone", columns=["phone"])
            events = _events(s)
            assert len(events) == 1
            assert events[0]["ok"] is True
            assert events[0]["model"] == "fake-chat"
            assert any(fp.startswith("expression@") for fp in events[0]["skills"])
    finally:
        client.close(); tmp.cleanup()


def test_generated_expression_does_not_bypass_save_validation():
    """AI 生成只是建议：把它改成违规写法后保存仍被既有校验链路拒绝。"""
    client, _, tmp = _client()
    try:
        with client:
            p = FakeProvider([GOOD])
            register_provider("deepseek", p)
            client.headers["X-Synetra-User"] = "dev"

            r = _post(client, prompt="手机号打码", source_field="phone", columns=["phone"])
            generated = r.json()["expression"]

            cfg = {
                "project": "p", "name": "t",
                "source_conn_key": "src", "target_conn_key": "tgt",
                "source_object": "o", "target_object": "d",
                "mode": "append", "primary_keys": [],
                "incremental": {"field": "", "watermark_field": "", "first_run_policy": "all"},
                "fields": [
                    {"source": "phone", "target": "phone_masked", "kind": "expression",
                     "expression": generated.replace("concat", "CONCAT")},  # 手工改成违规写法
                ],
            }
            saved = client.post("/api/tasks", json={"project": "p", "name": "t", "config": cfg})
            assert saved.status_code == 422
            assert "白名单" in saved.json()["detail"]  # 拒绝原因来自同一校验器
            assert client.get("/api/tasks", params={"project": "p"}).json() == []
    finally:
        client.close(); tmp.cleanup()


def test_blank_prompt_rejected():
    client, _, tmp = _client()
    try:
        with client:
            register_provider("deepseek", FakeProvider([GOOD]))
            assert _post(client, prompt="   ").status_code == 422
    finally:
        client.close(); tmp.cleanup()


def test_requires_developer_role():
    client, s, tmp = _client()
    try:
        with client:
            register_provider("deepseek", FakeProvider([GOOD]))
            s.create_user("reader", "readonly", password="ro")
            client.headers["X-Synetra-User"] = "reader"
            assert client.post("/api/llm/expression",
                               json={"prompt": "打码"}).status_code == 403
    finally:
        client.close(); tmp.cleanup()


def test_not_configured_returns_guidance():
    client, _, tmp = _client()
    try:
        with client:
            # 未注册 provider → 503 引导性错误
            from app.llm import provider as provider_mod

            provider_mod._PROVIDERS.pop("deepseek", None)
            r = _post(client, prompt="打码")
            assert r.status_code == 503
            assert "未就绪" in r.json()["detail"]
    finally:
        client.close(); tmp.cleanup()
