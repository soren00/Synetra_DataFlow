"""AI 管理（技能包）：清单/查看/保存、保存前一致性校验、路径安全、权限与审计。

写路径一律在临时沙箱里进行（复制技能包后 monkeypatch 根目录），避免改到随服务分发的文件。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.llm import skill_lint


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


@pytest.fixture()
def sandbox_skills(monkeypatch, tmp_path_factory):
    """把技能包复制到临时目录，并把 skill_lint 的根目录指向副本。"""
    root = tmp_path_factory.mktemp("skills")
    shutil.copytree(skill_lint.skills_dir(), root / "skills")
    fake_root = root / "skills"
    monkeypatch.setattr(skill_lint, "skills_dir", lambda: fake_root)
    return fake_root


def _as_admin(client):
    client.headers["X-Synetra-User"] = "admin"


def _as_dev(client):
    client.headers["X-Synetra-User"] = "dev"


# ---- 7.2 清单与内容查看 ----

def test_list_skills_returns_metadata(sandbox_skills):
    client, _, tmp = _client()
    try:
        with client:
            _as_admin(client)
            r = client.get("/api/llm/skills")
            assert r.status_code == 200
            items = r.json()
            assert [s["name"] for s in items] == ["expression"]
            meta = items[0]
            assert meta["description"]
            assert meta["fingerprint"].startswith("expression@")
            assert meta["updated_at"]
            assert meta["files"] == ["SKILL.md", "examples.md"]
    finally:
        client.close(); tmp.cleanup()


def test_get_skill_returns_raw_contents(sandbox_skills):
    client, _, tmp = _client()
    try:
        with client:
            _as_admin(client)
            r = client.get("/api/llm/skills/expression")
            assert r.status_code == 200
            body = r.json()
            assert body["contents"]["SKILL.md"] == (sandbox_skills / "expression" / "SKILL.md").read_text(encoding="utf-8")
            assert "FUNCTIONS:BEGIN" in body["contents"]["SKILL.md"]
    finally:
        client.close(); tmp.cleanup()


def test_skill_endpoints_are_admin_only(sandbox_skills):
    client, _, tmp = _client()
    try:
        with client:
            _as_dev(client)
            assert client.get("/api/llm/skills").status_code == 403
            assert client.get("/api/llm/skills/expression").status_code == 403
            assert client.put("/api/llm/skills/expression",
                              json={"files": {"SKILL.md": "x"}}).status_code == 403
    finally:
        client.close(); tmp.cleanup()


def test_get_unknown_skill_404(sandbox_skills):
    client, _, tmp = _client()
    try:
        with client:
            _as_admin(client)
            assert client.get("/api/llm/skills/nope").status_code == 404
    finally:
        client.close(); tmp.cleanup()


# ---- 7.3 保存：校验通过才写盘 ----

def test_save_rejects_broken_content_and_keeps_file_unchanged(sandbox_skills):
    client, s, tmp = _client()
    try:
        with client:
            _as_admin(client)
            path = sandbox_skills / "expression" / "SKILL.md"
            original = path.read_text(encoding="utf-8")
            before_fp = skill_lint.fingerprint("expression")

            broken = original.replace("禁止属性访问", "禁止")
            r = client.put("/api/llm/skills/expression", json={"files": {"SKILL.md": broken}})

            assert r.status_code == 422
            assert "技能包校验未通过" in r.json()["detail"]
            assert "禁止项" in r.json()["detail"]
            assert path.read_text(encoding="utf-8") == original       # 未写盘
            assert skill_lint.fingerprint("expression") == before_fp
            # 拒绝后生成行为不变：后续校验仍通过（内容仍是原版）
            assert skill_lint.lint_skill("expression") == []
            assert not [e for e in s.list_events() if e["action"] == "llm_skill_save"]
    finally:
        client.close(); tmp.cleanup()


def test_save_accepts_valid_content_immediately_and_audits(sandbox_skills):
    client, s, tmp = _client()
    try:
        with client:
            _as_admin(client)
            path = sandbox_skills / "expression" / "SKILL.md"
            before_fp = skill_lint.fingerprint("expression")

            updated = path.read_text(encoding="utf-8") + "\n<!-- 保存即生效冒烟注释 -->\n"
            r = client.put("/api/llm/skills/expression", json={"files": {"SKILL.md": updated}})

            assert r.status_code == 200
            body = r.json()
            assert body["saved"] is True
            assert body["before"] == before_fp
            assert body["after"] != before_fp            # 指纹随内容变化
            assert path.read_text(encoding="utf-8") == updated
            assert skill_lint.lint_skill("expression") == []

            events = [e for e in s.list_events() if e["action"] == "llm_skill_save"]
            assert len(events) == 1
            detail = events[0]["detail_json"]
            assert before_fp in detail and body["after"] in detail
    finally:
        client.close(); tmp.cleanup()


def test_save_preserves_existing_line_endings(sandbox_skills):
    """保存不改写原有换行风格（仓库内技能包为 CRLF），避免版本控制里整文件重写。"""
    client, _, tmp = _client()
    try:
        with client:
            _as_admin(client)
            path = sandbox_skills / "expression" / "SKILL.md"
            assert b"\r\n" in path.read_bytes()

            updated = path.read_text(encoding="utf-8") + "\n<!-- 行尾风格冒烟 -->\n"
            r = client.put("/api/llm/skills/expression", json={"files": {"SKILL.md": updated}})
            assert r.status_code == 200

            raw = path.read_bytes()
            assert b"\r\n" in raw
            assert raw.count(b"\n") == raw.count(b"\r\n"), "保存后出现混用换行"
    finally:
        client.close(); tmp.cleanup()


# ---- 7.3 路径与文件安全 ----

def test_save_rejects_non_allowlisted_file(sandbox_skills):
    client, _, tmp = _client()
    try:
        with client:
            _as_admin(client)
            r = client.put("/api/llm/skills/expression",
                           json={"files": {"../../etc/passwd": "x"}})
            assert r.status_code == 422
            assert "只允许编辑技能包既定文件" in r.json()["detail"]
    finally:
        client.close(); tmp.cleanup()


def test_save_rejects_path_traversal_skill_name(sandbox_skills):
    client, _, tmp = _client()
    try:
        with client:
            _as_admin(client)
            r = client.put("/api/llm/skills/..%2F..%2Fetc",
                           json={"files": {"SKILL.md": "x"}})
            assert r.status_code in (404, 422)
            assert not (Path(tmp.name).parent / "etc").exists()
    finally:
        client.close(); tmp.cleanup()


def test_save_rejects_unknown_skill(sandbox_skills):
    client, _, tmp = _client()
    try:
        with client:
            _as_admin(client)
            r = client.put("/api/llm/skills/nope", json={"files": {"SKILL.md": "x"}})
            assert r.status_code == 404
            assert "不存在" in r.json()["detail"]
    finally:
        client.close(); tmp.cleanup()


def test_save_rejects_empty_payload(sandbox_skills):
    client, _, tmp = _client()
    try:
        with client:
            _as_admin(client)
            assert client.put("/api/llm/skills/expression", json={"files": {}}).status_code == 422
    finally:
        client.close(); tmp.cleanup()


# ---- 校验合并语义 ----

def test_save_validates_merged_files(sandbox_skills):
    """只改 examples.md 时，校验针对「新 examples + 既有 SKILL.md」的合并结果。"""
    client, _, tmp = _client()
    try:
        with client:
            _as_admin(client)
            examples = (sandbox_skills / "expression" / "examples.md").read_text(encoding="utf-8")
            broken = examples.replace(
                "coalesce(amount, 0) | 函数不在白名单",
                "lower(email) | 函数不在白名单", 1)
            r = client.put("/api/llm/skills/expression", json={"files": {"examples.md": broken}})
            assert r.status_code == 422
            assert "反例未被校验拒绝" in r.json()["detail"]
    finally:
        client.close(); tmp.cleanup()
