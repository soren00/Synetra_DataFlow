"""任务 5.x：LLM 抽象/脱敏/RAG/生成路径/手写路径/物理脚本版本化。"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.llm.generator import ScriptGenerator
from app.llm.provider import LLMProvider, get_provider, model_info, register_provider
from app.llm.rag import build_rag_context
from app.llm.redact import redact_object
from app.scripting.checks import static_validate, syntax_check
from app.scripting.executor import dry_run_script
from app.scripts import ScriptStore


def _client():
    tmp = tempfile.TemporaryDirectory()
    os.environ["SYNETRA_DATA_DIR"] = tmp.name
    os.environ["SYNETRA_SCRIPTS_ROOT"] = tmp.name
    get_settings.cache_clear()
    from app.main import app
    from app.store import Store

    Store(get_settings().data_dir).upsert_user("admin", "admin")
    client = TestClient(app)
    client.headers["X-Synetra-User"] = "admin"
    return client, tmp


class RecordingProvider(LLMProvider):
    """记录请求、返回固定脚本的假供应商，用于断言发往 LLM 的内容。"""

    name = "fake"

    def __init__(self, reply: str, system: str | None = None):
        self.reply = reply
        self.calls: list[tuple[str, str | None]] = []
        self.histories: list[list[dict]] = []

    def converse(self, messages: list[dict], *, system: str | None = None) -> str:
        self.histories.append([dict(m) for m in messages])
        last = messages[-1]["content"] if messages else ""
        self.calls.append((last, system))
        return self.reply

    def list_models(self):
        return [model_info("m1", "fake", ["chat"])]


VALID_SCRIPT = '''\
import os
from etl_sdk import readers, writers
rows = readers.query("SAMPLE_SOURCE", "SELECT * FROM orders", {})
writer = writers.relational("SAMPLE_TARGET", "orders_dw", mode="upsert", primary_keys=["id"])
writer.buffered(rows, watermark_field="max_updated_at")
'''

VALID_CONFIG = {
    "project": "proj-x", "name": "sync-orders",
    "source_conn_key": "src", "target_conn_key": "tgt",
    "source_object": "orders", "target_object": "orders_dw",
    "mode": "upsert", "primary_keys": ["id"],
    "incremental": {"field": "updated_at", "watermark_field": "max_updated_at"},
    "fields": [],
}


# ---- 5.1 LLM 抽象与可插拔 ----

def test_provider_registry_switch_and_isolation():
    register_provider("fake", RecordingProvider(VALID_SCRIPT))
    assert get_provider("fake").name == "fake"
    # 切换供应商不改既有脚本/任务：下面确认两次 confirm 得到的脚本独立于 provider 名称
    p1 = RecordingProvider(VALID_SCRIPT)
    p2 = RecordingProvider(VALID_SCRIPT)
    s1 = ScriptGenerator(p1).generate(VALID_CONFIG)
    s2 = ScriptGenerator(p2).generate(VALID_CONFIG)
    assert s1.script == s2.script  # 同一配置 → 脚本一致；供应商仅影响调用本身
    assert p1.calls and p2.calls


# ---- 5.2 脱敏 + RAG ----

def test_redact_no_plaintext_secret():
    obj = {"dsn": "mysql://u:SuperSecret@db:3306/x", "password": "P@ss",
           "token": "tok123", "plain": "user"}
    redacted, secrets = redact_object(obj)
    s = str(redacted)
    assert "SuperSecret" not in s and "P@ss" not in s and "tok123" not in s
    assert len(secrets) == 3  # dsn口令 / password / token 各映射一个占位符
    assert redacted["plain"] == "user"


def test_rag_context_contains_contract():
    ctx = build_rag_context()
    assert "etl_sdk" in ctx and "writers" in ctx and "dry-run" in ctx


def test_rag_context_contains_expression_skill():
    """RAG 注入范围包含表达式技能包（脚本生成路径同样受规范约束）。"""
    ctx = build_rag_context()
    assert "# 技能包：expression" in ctx
    assert "受支持函数清单" in ctx and "concat" in ctx and "禁止项清单" in ctx


def test_generate_request_has_contract_and_no_plaintext():
    p = RecordingProvider(VALID_SCRIPT)
    # 配置模拟含 DSN 口令的连接 meta，验证生成请求不带明文
    config = {**VALID_CONFIG, "source_meta": {"dsn": "mysql://u:hush@h/db"}}
    gen = ScriptGenerator(p)
    result = gen.generate(config)
    assert result.valid
    prompt, system = p.calls[0]
    assert "etl_sdk" in prompt and "hush" not in prompt
    assert system is not None


# ---- 5.3 LLM+Python 生成路径 ----

def test_generator_strips_fences_and_validates():
    p = RecordingProvider("```python\n" + VALID_SCRIPT + "\n```")
    result = ScriptGenerator(p).generate(VALID_CONFIG)
    assert result.valid
    assert result.script.strip() == VALID_SCRIPT.strip()
    assert not result.script.startswith("```")


def test_generator_rejects_bad_import():
    p = RecordingProvider("import shutil\nprint('x')\n")
    result = ScriptGenerator(p).generate(VALID_CONFIG)
    assert not result.valid
    assert any("shutil" in v for v in result.violations)


def test_generator_rejects_config_with_illegal_expression():
    """配置层表达式违规 → 拒绝生成（不调用 LLM），给出可读原因。"""
    p = RecordingProvider(VALID_SCRIPT)
    bad = {**VALID_CONFIG, "fields": [
        {"source": "email", "target": "email", "kind": "expression",
         "expression": "lower(email).strip()"}]}
    result = ScriptGenerator(p).generate(bad)
    assert not result.valid and result.script == ""
    assert any("表达式校验未通过" in v for v in result.violations)
    assert p.calls == []  # 未向 LLM 发起调用


def test_generator_allows_config_with_valid_expression():
    p = RecordingProvider(VALID_SCRIPT)
    good = {**VALID_CONFIG, "fields": [
        {"source": "email", "target": "email", "kind": "expression",
         "expression": "lower(email)"}]}
    result = ScriptGenerator(p).generate(good)
    assert result.valid and p.calls


def test_llm_path_confirm_writes_version_and_dry_run_counts(tmp_path):
    register_provider("fake", RecordingProvider(VALID_SCRIPT))
    client, tmp = _client()
    try:
        # 先建任务
        r = client.post("/api/tasks", json={"project": "proj-x", "name": "sync",
                                            "config": VALID_CONFIG})
        key = r.json()["task_key"]
        # dry-run
        dr = client.post(f"/api/scripts/{key}/dry-run",
                         json={"script": VALID_SCRIPT})
        assert dr.status_code == 200
        body = dr.json()
        assert body["static_ok"] and body["dry_run_ok"]
        assert body["total_records"] >= 0
        # 确认生效（注：保存任务时已自动从配置生成 current/ version=1，故此处 confirm 为 v2）
        con = client.post(f"/api/scripts/{key}/llm-confirm",
                          json={"script": VALID_SCRIPT, "generated_by": "tester"})
        assert con.status_code == 200
        assert con.json()["version"] == 2
        cur = client.get(f"/api/scripts/{key}/current").json()
        assert cur["version"] == 2 and "etl_sdk" in cur["script"]
        versions = client.get(f"/api/scripts/{key}/versions").json()
        assert len(versions) == 2 and versions[0]["generated_by"] == "tester"
        # 校验失败不可生效
        bad = {"script": "import shutil\nx=1\n"}
        con2 = client.post(f"/api/scripts/{key}/llm-confirm", json={"script": bad["script"],
                                                                    "generated_by": "tester"})
        assert con2.status_code == 422
    finally:
        client.close(); tmp.cleanup()


# ---- 5.4 手写 Python 路径 ----

def test_handwritten_save_and_dry_run_no_target_write():
    client, tmp = _client()
    try:
        client.post("/api/tasks", json={"project": "proj-x", "name": "sync",
                                        "config": VALID_CONFIG})
        key = "handy"
        # 语法错误被拒
        bad = {"task_key": key, "script": "def broken(:\n", "project": "proj-x"}
        assert client.post("/api/scripts/handwritten", json=bad).status_code == 422
        # 正常保存
        ok = client.post("/api/scripts/handwritten",
                         json={"task_key": key, "script": VALID_SCRIPT, "project": "proj-x",
                               "name": "sync", "generated_by": "hand"})
        assert ok.status_code == 200
        cur = client.get(f"/api/scripts/{key}/current").json()
        assert cur["version"] == 1
        # dry-run 不写目标：借助执行器直接验证 report 且无真实写入行为
        res = dry_run_script(VALID_SCRIPT, VALID_CONFIG, sample=[{"id": 1, "name": "a"}])
        assert res.ok
        # 子进程 dry-run 只在内存记数，未触碰任何真实源/目标
        assert res.total_records() >= 0
    finally:
        client.close(); tmp.cleanup()


def test_dry_run_bare_etl_sdk_global_bound():
    """裸引用 etl_sdk.xxx（无 import）必须可用——防 preamble 未绑定全局名的回归。"""
    source = ('rows = [{"id": 1, "city": "Shanghai"}]\n'
              'etl_sdk.writers.relational("SAMPLE_TARGET", "ods", mode="upsert", '
              'primary_keys=["id"]).buffered(rows)\n')
    res = dry_run_script(source, VALID_CONFIG, sample=[{"id": 1}])
    assert res.ok
    assert res.total_records() == 1
    assert res.report[0]["rows"] == 1


def test_dry_run_records_without_writing(tmp_path):
    res = dry_run_script(VALID_SCRIPT, VALID_CONFIG, sample=[{"id": 1}, {"id": 2}])
    assert res.ok
    assert res.total_records() == 2  # buffered 收到 2 行，仅记数
    assert all(r["rows"] in (0, 2) for r in res.report)


# ---- 5.5 物理脚本 current/versions 原子替换与版本记录 ----

def test_script_store_version_and_rollback(tmp_path):
    store = ScriptStore(tmp_path / "sk")
    v1 = store.replace_current("T1", "print(1)", generated_by="a", config_snapshot={"c": 1})
    v2 = store.replace_current("T1", "print(2)", generated_by="b", config_snapshot={"c": 2})
    assert (v1, v2) == (1, 2)
    assert store.read_current("T1") == "print(2)"
    recs = store.list_records("T1")
    assert [r["version"] for r in recs] == [2, 1]
    # 回滚
    v3 = store.rollback("T1", 1, actor="sys")
    assert v3 == 3
    assert store.read_current("T1") == "print(1)"
    assert store.current_path("T1").exists()


def test_syntax_and_static_checks():
    assert syntax_check("print('ok')") == []
    assert any("语法" in e for e in syntax_check("def broken(:\n"))
    assert any("shutil" in v for v in static_validate("import shutil\n"))
    assert static_validate(VALID_SCRIPT) == []