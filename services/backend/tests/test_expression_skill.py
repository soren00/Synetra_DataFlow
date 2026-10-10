"""技能包一致性守门（docs-as-tests）。

校验实现集中在 `app/llm/skill_lint.py`（与「保存技能包」接口同一份逻辑），本文件只做两件事：
1. 断言随服务分发的技能包通过校验；
2. 断言校验器确实能检出破坏（防止校验形同虚设）。

因此 tests/ 内不得再写第二份技能包断言。与技能包内容无关的回归（生成脚本命名空间同源、
启动指纹审计、脱敏配方）保持原样。
"""

from __future__ import annotations

from app.expr.whitelist import (
    EXPR_NAMESPACE_SOURCE,
    apply_expression,
    validate_expression,
)
from app.llm.skill_lint import lint_skill, lint_skill_files, load_skill_files

# ---- 1. 随服务分发的技能包必须通过校验 ----

def test_skill_content_passes_lint():
    assert lint_skill("expression") == []


# ---- 2. 校验器必须能检出各类破坏 ----

def test_lint_detects_function_list_drift():
    """函数清单与权威白名单不一致（少一个）必须被检出。"""
    files = dict(load_skill_files("expression"))
    files["SKILL.md"] = files["SKILL.md"].replace("concat, cast", "cast", 1)
    issues = lint_skill_files(files)
    assert any("函数清单与权威白名单不一致" in i for i in issues), issues


def test_lint_detects_missing_forbidden_note():
    """禁止项未逐字包含权威说明必须被检出。"""
    files = dict(load_skill_files("expression"))
    files["SKILL.md"] = files["SKILL.md"].replace("禁止属性访问", "禁止")
    issues = lint_skill_files(files)
    assert any("禁止项" in i for i in issues), issues


def test_lint_detects_broken_positive_example():
    """正例被白名单拒绝必须被检出。"""
    files = dict(load_skill_files("expression"))
    files["examples.md"] = files["examples.md"].replace(
        """lower(email) | {"email": "A@B.com"} | 'a@b.com'""",
        """COALESCE(email, 'x') | {"email": "A@B.com"} | 'a@b.com'""", 1)
    issues = lint_skill_files(files)
    assert any("正例被校验拒绝" in i for i in issues), issues


def test_lint_detects_wrong_example_outcome():
    """正例求值结果不符必须被检出。"""
    files = dict(load_skill_files("expression"))
    files["examples.md"] = files["examples.md"].replace(
        """concat(id, '-', name) | {"id": 1, "name": "x"} | '1-x'""",
        """concat(id, '-', name) | {"id": 1, "name": "x"} | '1_y'""", 1)
    issues = lint_skill_files(files)
    assert any("正例求值不符" in i for i in issues), issues


def test_lint_detects_missing_examples_file():
    files = dict(load_skill_files("expression"))
    files.pop("examples.md")
    issues = lint_skill_files(files)
    assert any("examples.md" in i for i in issues), issues


def test_lint_rejects_path_traversal_skill_name():
    issues = lint_skill("../../etc")
    assert issues and "非法" in issues[0]


# ---- 3. 与技能包内容无关的既有回归 ----

def test_generated_script_namespace_same_source_as_validator():
    """生成脚本内联的 _EXPR_NS 就是权威源码本身 → 校验器与运行时不存第二份清单。"""
    from app.expr.whitelist import allowed_functions
    from app.models.task_config import TaskConfig
    from app.scripting.gen_config import render_config_script

    cfg = TaskConfig(project="p", name="t",
                     source_conn_key="s", target_conn_key="g")
    src = render_config_script(cfg)
    assert EXPR_NAMESPACE_SOURCE in src, "生成脚本未内联权威命名空间源码"

    scope: dict = {}
    exec(EXPR_NAMESPACE_SOURCE, scope)  # noqa: S102 - 内容为模块内常量
    assert set(scope["_EXPR_NS"]) == set(allowed_functions())
    assert len(allowed_functions()) == 16


def test_startup_event_records_skill_fingerprints(monkeypatch):
    """启动即记录技能包指纹，用于排查「服务是否在跑旧规范」（实测坑：改了代码未重启）。"""
    from app import main

    calls: list[dict] = []

    class _FakeStore:
        def __init__(self, data_dir):
            self.data_dir = data_dir

        def log_event(self, **kw):
            calls.append(kw)

    monkeypatch.setattr(main, "Store", _FakeStore)
    main._log_llm_skill_versions()

    assert len(calls) == 1
    assert calls[0]["action"] == "llm_skill_loaded"
    skills = calls[0]["detail"]["skills"]
    assert any(s.startswith("expression@") for s in skills)


def test_masking_recipe_is_executable():
    """实测案例回归：姓名/手机号脱敏 —— SQL 写法被拒，切片写法可求值（用户报错场景）。"""
    bad = ["CONCAT(LEFT(customer_name, 1), '*', SUBSTRING(customer_name, 3))",
           "CONCAT(LEFT(phone, 3), '****', RIGHT(phone, 4))"]
    for expr in bad:
        assert validate_expression(expr), f"SQL 方言写法应被拒：{expr}"

    rows = [
        ("concat(customer_name[0], '*', customer_name[2:])",
         {"customer_name": "张小明"}, "张*明"),
        ("concat(customer_name[0], '*', customer_name[2:])",
         {"customer_name": "张三"}, "张*"),
        ("concat(phone[0:3], '****', phone[7:])",
         {"phone": "13800138001"}, "138****8001"),
    ]
    for expr, row, expected in rows:
        assert validate_expression(expr) == [], f"切片写法应通过校验：{expr}"
        assert apply_expression(expr, row) == expected
