"""流水线契约（1.1/1.2/1.5）：模型结构校验、旧配置兼容加载、图校验定位。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.pipeline import (
    PipelineConfig,
    PipelineEdge,
    PipelineGraphError,
    PipelineNode,
    StepType,
)
from app.models.task_config import TaskConfig


def _node(node_id: str, step: StepType, **cfg) -> PipelineNode:
    return PipelineNode(id=node_id, type=step, config=cfg)


def _legal() -> PipelineConfig:
    return PipelineConfig(
        nodes=[
            _node("src", StepType.SOURCE_TABLE, conn_key="c", object="t"),
            _node("pick", StepType.SELECT, rules=[{"kind": "direct", "source": "id", "target": "id"}]),
            _node("dst", StepType.TARGET_TABLE, conn_key="g", object="d", mode="append"),
        ],
        edges=[PipelineEdge(source="src", target="pick"),
               PipelineEdge(source="pick", target="dst")],
    )


def test_legal_pipeline_roundtrip():
    pipe = _legal()
    assert pipe.validate_structure() == []
    again = PipelineConfig.model_validate(pipe.model_dump(mode="json"))
    assert [n.id for n in again.nodes] == ["src", "pick", "dst"]
    assert again.topo_order()[0] == "src"


def test_node_id_must_be_identifier():
    with pytest.raises(ValidationError):
        PipelineNode(id="1-bad id", type=StepType.UNION)


def test_unknown_node_type_rejected():
    with pytest.raises(ValidationError):
        PipelineNode(id="x", type="nope")


def test_missing_required_fields_rejected():
    with pytest.raises(ValidationError):
        PipelineNode(id="x")  # type: ignore[call-arg]


def test_edge_to_unknown_node_detected():
    pipe = _legal()
    pipe.edges.append(PipelineEdge(source="pick", target="ghost"))
    problems = pipe.validate_structure()
    assert any("ghost" in p for p in problems)


def test_self_loop_detected():
    pipe = _legal()
    pipe.edges.append(PipelineEdge(source="pick", target="pick"))
    assert any("自环" in p for p in pipe.validate_structure())


def test_cycle_detected_with_located_nodes():
    pipe = _legal()
    pipe.edges.append(PipelineEdge(source="dst", target="pick"))
    with pytest.raises(PipelineGraphError) as e:
        pipe.topo_order()
    assert "环路" in str(e.value)


def test_missing_source_or_target():
    pipe = PipelineConfig(nodes=[_node("pick", StepType.SELECT, rules=[])],
                          edges=[])
    problems = pipe.validate_structure()
    assert any("源节点" in p for p in problems)
    assert any("目标节点" in p for p in problems)


def test_legacy_config_loads_without_pipeline():
    """旧配置（无 pipeline 字段）必须能直接加载，pipeline 为空表示待编译。"""
    cfg = TaskConfig.model_validate({
        "project": "p", "name": "t",
        "source_conn_key": "s", "target_conn_key": "g",
        "source_object": "a", "target_object": "b",
        "fields": [{"kind": "direct", "source": "id", "target": "id"}],
    })
    assert cfg.pipeline is None
    assert cfg.fields[0].source == "id"


def test_pipeline_field_persists():
    cfg = TaskConfig(project="p", name="t", source_conn_key="s", target_conn_key="g")
    cfg.pipeline = _legal()
    again = TaskConfig.model_validate(cfg.model_dump(mode="json"))
    assert again.pipeline is not None
    assert len(again.pipeline.nodes) == 3
