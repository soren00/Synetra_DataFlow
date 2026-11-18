"""AI 草稿对齐流水线契约（4.7）：多步/多源产出 nodes+edges，单源仍走等价三节点。"""

from __future__ import annotations

import json
import tempfile

import pytest

from app.etl_sdk.pipeline import compile_task_pipeline
from app.etl_sdk.pipeline.validate import validate_pipeline
from app.llm.provider import LLMProvider
from app.llm.task_draft import (
    DraftGenerationError,
    expression_issues,
    generate_draft,
    session_system,
)
from app.models.connection import ConnectionCreate, ConnectionDirection, ConnectionType
from app.models.task_config import TaskConfig
from app.store import Store
from app.vault import SecretVault

_KEEP: list = []


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """草稿生成会读取 settings.data_dir（Catalog 降级链），必须与其它用例隔离。"""
    from app.core.config import get_settings

    monkeypatch.setenv("SYNETRA_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeProvider(LLMProvider):
    name = "deepseek"
    model_name = "fake-chat"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[str] = []

    def converse(self, messages, *, system=None) -> str:
        self.calls.append(messages[-1]["content"] if messages else "")
        return self.replies.pop(0) if self.replies else "{}"

    def list_models(self):
        return []


def _store_with_conns():
    tmp = tempfile.TemporaryDirectory()
    _KEEP.append(tmp)
    store = Store(tmp.name, SecretVault(tmp.name))
    src1 = store.create_connection(ConnectionCreate(
        project="demo", name="crm", ctype=ConnectionType.MYSQL,
        direction=ConnectionDirection.SOURCE,
        meta={"host": "127.0.0.1", "port": "3306", "db": "x", "user": "u"},
        secret="p"))
    src2 = store.create_connection(ConnectionCreate(
        project="demo", name="erp", ctype=ConnectionType.MYSQL,
        direction=ConnectionDirection.SOURCE,
        meta={"host": "127.0.0.1", "port": "3306", "db": "y", "user": "u"},
        secret="p"))
    tgt = store.create_connection(ConnectionCreate(
        project="demo", name="dw", ctype=ConnectionType.MYSQL,
        direction=ConnectionDirection.TARGET, meta={}, secret=None))
    return store, src1.conn_key, src2.conn_key, tgt.conn_key


def _skeleton(src: str, tgt: str) -> str:
    return json.dumps({
        "source_conn_key": src, "source_object": "crm.users",
        "target_conn_key": tgt, "target_object": "dw.users",
        "mode": "upsert", "primary_keys": ["id"],
        "incremental_field": "", "watermark_field": "",
    }, ensure_ascii=False)


def _multi_source_full(src1: str, src2: str, tgt: str) -> str:
    return json.dumps({
        "name": "两源合并去重",
        "source_conn_key": src1, "source_object": "crm.users",
        "target_conn_key": tgt, "target_object": "dw.users",
        "mode": "upsert", "primary_keys": ["id"],
        "incremental": {"field": "", "watermark_field": "", "first_run_policy": "all"},
        "fields": [{"source": "id", "target": "id", "kind": "direct"}],
        "schedule": "",
        "pipeline": {
            "nodes": [
                {"id": "a", "type": "source_table", "config": {"conn_key": src1, "object": "crm.users"}},
                {"id": "b", "type": "source_table", "config": {"conn_key": src2, "object": "erp.users"}},
                {"id": "u", "type": "union", "config": {}},
                {"id": "pick", "type": "select", "config": {"rules": [
                    {"kind": "direct", "source": "id", "target": "id"},
                    {"kind": "expression", "source": "name", "target": "name_up",
                     "expression": "upper(name)"},
                ]}},
                {"id": "f", "type": "filter", "config": {"expression": "id > 0"}},
                {"id": "d", "type": "unique", "config": {"keys": ["id"]}},
                {"id": "w", "type": "target_table",
                 "config": {"conn_key": tgt, "object": "dw.users",
                            "mode": "upsert", "primary_keys": ["id"]}},
            ],
            "edges": [
                {"source": "a", "target": "u"}, {"source": "b", "target": "u"},
                {"source": "u", "target": "pick"}, {"source": "pick", "target": "f"},
                {"source": "f", "target": "d"}, {"source": "d", "target": "w"},
            ],
        },
    }, ensure_ascii=False)


def _single_source_full(src: str, tgt: str) -> str:
    return json.dumps({
        "name": "单源映射",
        "source_conn_key": src, "source_object": "crm.users",
        "target_conn_key": tgt, "target_object": "dw.users",
        "mode": "append", "primary_keys": [],
        "incremental": {"field": "updated_at", "watermark_field": "updated_at",
                        "first_run_policy": "all"},
        "fields": [{"source": "id", "target": "id", "kind": "direct"}],
        "schedule": "",
    }, ensure_ascii=False)


def _conns(src1, src2, tgt):
    return [
        {"conn_key": src1, "name": "crm", "ctype": "mysql"},
        {"conn_key": src2, "name": "erp", "ctype": "mysql"},
        {"conn_key": tgt, "name": "dw", "ctype": "mysql"},
    ]


def test_multi_source_draft_produces_pipeline():
    store, src1, src2, tgt = _store_with_conns()
    provider = FakeProvider([_skeleton(src1, tgt), _multi_source_full(src1, src2, tgt)])

    result = generate_draft(store, provider, "把 crm 和 erp 的用户合并去重后写入 dw",
                            "demo", _conns(src1, src2, tgt), "dev")

    pipe_raw = result.draft.get("pipeline")
    assert pipe_raw, "多源需求必须产出 pipeline"
    assert len(pipe_raw["nodes"]) == 7 and len(pipe_raw["edges"]) == 6

    cfg = TaskConfig.model_validate(result.draft)
    pipe = compile_task_pipeline(cfg)
    assert [n.id for n in pipe.nodes][:2] == ["a", "b"]
    assert validate_pipeline(pipe) == []

    # 顶层连接按首尾节点回填，旧表单/脚本生成链路仍可用
    assert result.draft["source_conn_key"] == src1
    assert result.draft["target_conn_key"] == tgt
    assert result.draft["target_object"] == "dw.users"


def test_single_source_draft_still_compiles_three_nodes():
    store, src1, _src2, tgt = _store_with_conns()
    provider = FakeProvider([_skeleton(src1, tgt), _single_source_full(src1, tgt)])

    result = generate_draft(store, provider, "每天把 users 同步到 dw",
                            "demo", _conns(src1, src1, tgt), "dev")
    assert not result.draft.get("pipeline")

    pipe = compile_task_pipeline(TaskConfig.model_validate(result.draft))
    assert [n.type.value for n in pipe.nodes] == [
        "source_table", "select", "target_table"]


def test_draft_rejects_unknown_connection_in_pipeline():
    store, src1, src2, tgt = _store_with_conns()
    payload = json.loads(_multi_source_full(src1, src2, tgt))
    payload["pipeline"]["nodes"][1]["config"]["conn_key"] = "ghost-conn"
    provider = FakeProvider([_skeleton(src1, tgt), json.dumps(payload, ensure_ascii=False)])

    with pytest.raises(DraftGenerationError) as e:
        generate_draft(store, provider, "多源合并", "demo",
                       _conns(src1, src2, tgt), "dev")
    assert "连接不存在" in str(e.value) and "ghost-conn" in str(e.value)


def test_draft_rejects_cyclic_pipeline():
    store, src1, src2, tgt = _store_with_conns()
    payload = json.loads(_multi_source_full(src1, src2, tgt))
    payload["pipeline"]["edges"].append({"source": "d", "target": "pick"})  # 成环
    bad = json.dumps(payload, ensure_ascii=False)
    provider = FakeProvider([_skeleton(src1, tgt), bad, bad])

    with pytest.raises(DraftGenerationError) as e:
        generate_draft(store, provider, "多源合并", "demo",
                       _conns(src1, src2, tgt), "dev")
    assert "流水线图非法" in str(e.value) or "环路" in str(e.value)


def test_pipeline_select_expressions_are_validated():
    """表达式校验覆盖流水线 select 节点，而不是只看顶层 fields。"""
    raw = {"pipeline": {"nodes": [
        {"id": "pick", "type": "select", "config": {"rules": [
            {"kind": "expression", "source": "name", "target": "up",
             "expression": "eval('1')"},
        ]}},
    ], "edges": []}}
    issues = expression_issues(raw)
    assert issues and issues[0]["target"] == "up"
    assert "白名单" in issues[0]["message"]


def test_session_system_declares_pipeline_contract():
    system = session_system()
    assert "pipeline" in system and "多个数据源合并" in system
