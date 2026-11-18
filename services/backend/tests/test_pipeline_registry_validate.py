"""步骤注册表与图校验（1.4/1.5）：五类步骤齐备、约束完整、违规定位到节点。"""

from __future__ import annotations

from app.etl_sdk.pipeline.registry import blocking_types, get_spec, steps_payload
from app.etl_sdk.pipeline.validate import validate_pipeline
from app.models.pipeline import PipelineConfig, PipelineEdge, PipelineNode, StepType


def test_five_categories_present():
    payload = steps_payload()
    categories = {s["category"] for s in payload}
    assert categories == {"source", "merge", "row", "batch", "target"}
    assert len(payload) >= 19


def test_each_step_declares_constraints_and_schema():
    for step in steps_payload():
        assert step["label"]
        assert isinstance(step["min_in"], int)
        assert isinstance(step["max_in"], int)
        assert set(step["config_schema"]).issubset({"fields"})
        for f in step["config_schema"].get("fields", []):
            assert {"key", "label", "type"}.issubset(f.keys())


def test_blocking_types_cover_batch_steps():
    blocking = blocking_types()
    assert {StepType.SORT, StepType.UNIQUE, StepType.AGGREGATE}.issubset(blocking)
    assert StepType.SELECT not in blocking


def test_spec_degrees():
    assert get_spec(StepType.SOURCE_TABLE).min_in == 0
    assert get_spec(StepType.SOURCE_TABLE).max_out == 1
    assert get_spec(StepType.TARGET_TABLE).max_out == 0
    assert get_spec(StepType.UNION).min_in == 2


def _pipe(nodes, edges) -> PipelineConfig:
    return PipelineConfig(nodes=nodes, edges=edges)


def _src(node_id: str = "a") -> PipelineNode:
    return PipelineNode(id=node_id, type=StepType.SOURCE_TABLE, config={"conn_key": "c"})


def _tgt(node_id: str = "t", **cfg) -> PipelineNode:
    return PipelineNode(id=node_id, type=StepType.TARGET_TABLE,
                        config={"conn_key": "g", **cfg})


def test_source_with_incoming_edge_rejected():
    pipe = _pipe([_src("a"), _src("b"), _tgt()],
                 [PipelineEdge(source="a", target="b"),
                  PipelineEdge(source="b", target="t")])
    problems = validate_pipeline(pipe)
    assert any("节点[b]" in p and "入边" in p for p in problems)


def test_union_needs_two_inputs():
    pipe = _pipe(
        [_src("a"), PipelineNode(id="u", type=StepType.UNION, config={}), _tgt()],
        [PipelineEdge(source="a", target="u"), PipelineEdge(source="u", target="t")],
    )
    problems = validate_pipeline(pipe)
    assert any("节点[u]" in p and "入边" in p for p in problems)


def test_orphan_node_reported():
    pipe = _pipe(
        [_src("a"), _tgt(),
         PipelineNode(id="lonely", type=StepType.FILTER, config={"expression": "x > 1"})],
        [PipelineEdge(source="a", target="t")],
    )
    problems = validate_pipeline(pipe)
    assert any("lonely" in p and "连通" in p for p in problems)


def test_upsert_requires_primary_keys():
    pipe = _pipe([_src("a"), _tgt(mode="upsert")],
                 [PipelineEdge(source="a", target="t")])
    assert any("主键" in p for p in validate_pipeline(pipe))


def test_expression_whitelist_enforced():
    pipe = _pipe(
        [_src("a"),
         PipelineNode(id="f", type=StepType.FILTER, config={"expression": "eval('1')"}),
         _tgt()],
        [PipelineEdge(source="a", target="f"), PipelineEdge(source="f", target="t")],
    )
    problems = validate_pipeline(pipe)
    assert any("节点[f]" in p and "白名单" in p for p in problems)


def test_legal_pipeline_has_no_problems():
    pipe = _pipe([_src("a"), _tgt()], [PipelineEdge(source="a", target="t")])
    assert validate_pipeline(pipe) == []
