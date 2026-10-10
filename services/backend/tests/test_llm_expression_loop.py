"""表达式校验回环（决策 9）：违规自动修复 / 轮次受限 / 合规不额外调用。

修复轮必须：复用与保存链路同一个校验器（validate_rules）、作为同一会话的后续轮次、
最多 1 次、失败不遮蔽原草稿。
"""

from __future__ import annotations

import json
import os
import tempfile

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.llm.provider import LLMProvider, model_info, register_provider
from app.models.connection import (
    ConnectionCreate,
    ConnectionDirection,
)
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
    return TestClient(app), s, tmp


def _seed_conns(store: Store, data_dir: str):
    src = store.create_connection(ConnectionCreate(
        project="demo", name="src", ctype="mysql",
        direction=ConnectionDirection.SOURCE,
        meta={"host": "127.0.0.1", "port": "3306", "db": "x", "user": "u"},
        secret="p"))
    tgt = store.create_connection(ConnectionCreate(
        project="demo", name="tgt", ctype="mysql",
        direction=ConnectionDirection.TARGET, meta={}, secret=None))
    from app.metadata import Catalog

    Catalog(data_dir).save_table_schema(src.conn_key, "users", [
        {"name": "id", "ctype": "int", "primary": True},
        {"name": "email", "ctype": "varchar"},
    ])
    return src.conn_key, tgt.conn_key


class FakeProvider(LLMProvider):
    """按序返回固定输出的假供应商；记录每轮对话历史与 system。"""

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


def _skeleton(src: str, tgt: str) -> str:
    return json.dumps({
        "source_conn_key": src, "source_object": "users",
        "target_conn_key": tgt, "target_object": "users_dw",
        "mode": "append", "primary_keys": ["id"],
        "incremental_field": "", "watermark_field": "",
    })


def _full(src: str, tgt: str, fields: list[dict]) -> str:
    return json.dumps({
        "project": "demo",
        "name": "用户同步", "source_conn_key": src, "source_object": "users",
        "target_conn_key": tgt, "target_object": "users_dw",
        "mode": "append", "primary_keys": ["id"],
        "incremental": {"field": "", "watermark_field": "", "first_run_policy": "all"},
        "fields": fields, "schedule": "",
    })


_BAD_FIELD = {"source": "email", "target": "email", "kind": "expression",
              "expression": "lower(email).strip()"}
_GOOD_FIELD = {"source": "email", "target": "email", "kind": "expression",
               "expression": "lower(email)"}


def _events(store: Store) -> list[dict]:
    return [json.loads(e["detail_json"]) for e in store.list_events(limit=50)
            if e["action"] == "llm_draft_call"]


def test_illegal_expression_is_repaired_in_same_conversation():
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            p = FakeProvider([_skeleton(src, tgt),
                              _full(src, tgt, [_BAD_FIELD]),
                              _full(src, tgt, [_GOOD_FIELD])])
            register_provider("deepseek", p)
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={"description": "同步用户", "project": "demo"})
            assert r.status_code == 200
            body = r.json()

            assert body["draft"]["fields"][0]["expression"] == "lower(email)"
            assert body["expression_issues"] == []
            assert len(p.calls) == 3  # 骨架轮 + 完整轮 + 修复轮

            # 修复轮在同一会话上追加，且提示含违规原因与权威规范要点
            repair_history = p.histories[-1]
            assert len(repair_history) == len(body["history"]) - 1  # 修复轮之后才有最终 assistant
            assert "表达式禁止属性访问" in p.calls[-1]
            assert "可用函数" in p.calls[-1]
            # 修复提示必须给出「怎么写」而不只是「有什么函数」：来自技能包 WRITING_TIPS
            assert "写法要点" in p.calls[-1]
            assert "切片" in p.calls[-1] and "小写" in p.calls[-1]
            assert "row[" in p.calls[-1]

            rounds = [d["round"] for d in _events(s)]
            assert "repair" in rounds
            repair = next(d for d in _events(s) if d["round"] == "repair")
            assert repair["ok"] is True and repair["turn"] == 3
    finally:
        client.close(); tmp.cleanup()


def test_repair_is_capped_and_reports_issues():
    """修复轮仍违规：不再继续调用，返回草稿并给出字段级告警。"""
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            still_bad = {"source": "email", "target": "email", "kind": "expression",
                         "expression": "coalesce(email, 'x')"}
            p = FakeProvider([_skeleton(src, tgt),
                              _full(src, tgt, [_BAD_FIELD]),
                              _full(src, tgt, [still_bad])])
            register_provider("deepseek", p)
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={"description": "同步用户", "project": "demo"})
            assert r.status_code == 200
            body = r.json()

            assert len(p.calls) == 3  # 上限 1 次修复：不无界重试
            issues = body["expression_issues"]
            assert len(issues) == 1
            assert issues[0]["field"] == "email" and issues[0]["target"] == "email"
            assert "函数不在白名单" in issues[0]["message"]
    finally:
        client.close(); tmp.cleanup()


def test_compliant_draft_triggers_no_extra_call():
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            p = FakeProvider([_skeleton(src, tgt), _full(src, tgt, [_GOOD_FIELD])])
            register_provider("deepseek", p)
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={"description": "同步用户", "project": "demo"})
            assert r.status_code == 200
            assert r.json()["expression_issues"] == []
            assert len(p.calls) == 2
            assert not any(d["round"] == "repair" for d in _events(s))
    finally:
        client.close(); tmp.cleanup()


def test_repair_output_invalid_keeps_original_draft():
    """修复轮输出非法 JSON（含自动重试仍失败）→ 保留原草稿并告警，不遮蔽结果。"""
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            p = FakeProvider([_skeleton(src, tgt),
                              _full(src, tgt, [_BAD_FIELD]),
                              "not json", "still not json"])
            register_provider("deepseek", p)
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={"description": "同步用户", "project": "demo"})
            assert r.status_code == 200
            body = r.json()
            assert body["draft"]["fields"][0]["expression"] == "lower(email).strip()"
            assert body["expression_issues"], "修复失败时必须以字段级告警呈现"
            assert len(p.calls) == 4  # 骨架 + 完整 + 修复(失败) + 修复重试(失败)
    finally:
        client.close(); tmp.cleanup()


def test_illegal_expression_not_persisted_and_fixable_in_session():
    """端到端：违规表达式配置被拒且不落库；会话内修订后合规草稿可正常保存。"""
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            client.headers["X-Synetra-User"] = "dev"
            before = len(client.get("/api/tasks").json())

            # ① 违规表达式直接保存 → 422 且不落库
            r = client.post("/api/tasks", json={
                "project": "demo", "name": "bad-task",
                "config": json.loads(_full(src, tgt, [_BAD_FIELD]))})
            assert r.status_code == 422
            assert "属性访问" in r.json()["detail"]
            assert len(client.get("/api/tasks").json()) == before

            # ② 会话内生成 + 修订 → 表达式合规
            p = FakeProvider([_skeleton(src, tgt),
                              _full(src, tgt, [_BAD_FIELD]),
                              _full(src, tgt, [_GOOD_FIELD])])
            register_provider("deepseek", p)
            r2 = client.post("/api/llm/tasks/draft", json={"description": "同步用户", "project": "demo"})
            assert r2.status_code == 200
            draft = r2.json()["draft"]
            assert r2.json()["expression_issues"] == []

            # ③ 合规草稿可保存（走既有保存链路）
            r3 = client.post("/api/tasks", json={"project": "demo", "name": "good-task",
                                                "config": draft})
            assert r3.status_code == 200
            assert len(client.get("/api/tasks").json()) == before + 1
    finally:
        client.close(); tmp.cleanup()


def test_revise_round_also_checks_expressions_in_session():
    """修订轮同样走校验回环，且修复轮接在会话历史末尾。"""
    client, s, tmp = _client()
    try:
        with client:
            src, tgt = _seed_conns(s, tmp.name)
            p = FakeProvider([_skeleton(src, tgt),
                              _full(src, tgt, [_GOOD_FIELD]),
                              _full(src, tgt, [_BAD_FIELD]),
                              _full(src, tgt, [_GOOD_FIELD])])
            register_provider("deepseek", p)
            client.headers["X-Synetra-User"] = "dev"
            r = client.post("/api/llm/tasks/draft", json={"description": "同步用户", "project": "demo"})
            body = r.json()

            r2 = client.post("/api/llm/tasks/draft/revise", json={
                "draft": body["draft"], "instruction": "邮箱去掉首尾空格再转小写",
                "project": "demo", "history": body["history"]})
            assert r2.status_code == 200
            b2 = r2.json()
            assert b2["expression_issues"] == []
            assert b2["draft"]["fields"][0]["expression"] == "lower(email)"

            rounds = [d["round"] for d in _events(s)]
            assert rounds.count("repair") == 1
            # 修复轮的轮次序号接在修订轮之后
            repair = next(d for d in _events(s) if d["round"] == "repair")
            assert repair["turn"] == 4
    finally:
        client.close(); tmp.cleanup()
