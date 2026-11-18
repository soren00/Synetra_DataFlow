"""前端画布与后端契约一致性（5.2）：分类映射与规则类型不得各自漂移。

画布的步骤清单由后端注册表驱动（GET /pipeline/steps），前端只维护分类中文名与
规则类型的中文名映射；本测试守门这两处静态映射，避免新增分类/规则类型后界面缺项。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.etl_sdk.pipeline.registry import steps_payload
from app.models.task_config import TransformKind

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components"


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def test_canvas_category_labels_cover_backend_categories():
    src = _read("PipelineCanvas.jsx")
    match = re.search(r"const CATEGORY_CN = \{([^}]*)\}", src)
    assert match, "未找到 CATEGORY_CN 分类映射"
    labels = set(re.findall(r"(\w+):", match.group(1)))
    backend = {s["category"] for s in steps_payload()}
    missing = backend - labels
    assert not missing, f"画布缺少分类中文名: {sorted(missing)}"


def test_rules_editor_kinds_match_transform_kind():
    src = _read("FieldRulesEditor.jsx")
    match = re.search(r"export const RULE_KINDS = \[(.*?)\];", src, re.DOTALL)
    assert match, "未找到 RULE_KINDS"
    kinds = set(re.findall(r'\["(\w+)"', match.group(1)))
    assert kinds == {k.value for k in TransformKind}


def test_canvas_nodes_are_backend_driven():
    """节点面板必须来自 /pipeline/steps，不得在画布内硬编码步骤清单。"""
    src = _read("PipelineCanvas.jsx")
    assert 'api.get("/pipeline/steps")' in src
    assert "specMap" in src


def test_canvas_wires_schema_and_ai_helpers():
    """5.4/5.6 的接线守门：节点抽屉走 AI 生成表达式，合并节点走列对齐接口。"""
    canvas = _read("PipelineCanvas.jsx")
    rules = _read("FieldRulesEditor.jsx")
    assert "/nodes/${selected.id}/schema" in canvas
    assert 'api.post("/llm/expression"' in rules


def test_canvas_passes_field_errors_and_column_alias():
    """5.4/5.6 接线守门：保存期字段级违规透传到规则行，合并列可重命名。"""
    canvas = _read("PipelineCanvas.jsx")
    tasks = (FRONTEND.parent / "pages" / "Tasks.jsx").read_text(encoding="utf-8")
    assert "fieldErrors = {}" in canvas
    assert "errors={fieldErrors}" in canvas
    assert "fieldErrors={fieldErrors}" in tasks
    assert "updateConfig(\"aliases\"" in canvas
    assert "aliasOf" in canvas
