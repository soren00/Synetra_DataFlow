"""从任务配置确定性生成可运行 Python 脚本（薄入口）。

多步清洗逻辑由 `etl_sdk.run_pipeline` 解释执行，生成脚本只承载两件事：
1) 内联本次的任务配置与流水线配置（确定性渲染，可版本化、可 diff）；
2) 调用运行时并按约定打印写入行数、水位与步级指标标记，供执行器解析。

因此：
- `render_config_script` 仍是纯函数：不依赖 LLM、不访问外部状态；
- 既有 current/versions 版本化、dry-run（SYNETRA_DRY_RUN=1）与人工确认语义不变；
- 连接一律以 conn_key 引用，脚本自身绝不内联明文口令。
"""

from __future__ import annotations

from app.etl_sdk.pipeline.compile import compile_task_pipeline
from app.models.task_config import TaskConfig

_TEMPLATE = '''# -*- coding: utf-8 -*-
"""ETL 流水线执行入口（由 render_config_script 从任务配置确定性生成，请勿手改）。

来源任务：{project}/{name}（task_key 由运行期绑定）。
处理过程由 etl_sdk.run_pipeline 解释执行；连接以 conn_key 引用，凭证由运行期解析。
dry-run(SYNETRA_DRY_RUN=1) 下目标步骤只计数不写目标。
"""
import json
import os

from etl_sdk import PipelineRunError, run_pipeline

# ---- 任务与流水线配置（确定性渲染，勿手改） ----
TASK_CONFIG = {task_config}
PIPELINE = {pipeline}


def main():
    # 增量水位：运行期经环境变量提供；缺省则按配置语义全量抽取
    watermark = os.environ.get("SYNETRA_WATERMARK", "")
    dry_run = os.environ.get("SYNETRA_DRY_RUN") == "1"
    # dry-run 下若执行器提供了样例行，则各源直接产出样本（不接触真实数据源）
    sample_raw = os.environ.get("SYNETRA_SAMPLE_JSON", "") or "[]"
    try:
        sample = json.loads(sample_raw) or None
    except ValueError:
        sample = None
    try:
        result = run_pipeline(
            PIPELINE,
            task_config=TASK_CONFIG,
            watermark=watermark,
            dry_run=dry_run,
            # dry-run 且无样本时以空样本执行：不接触真实数据源（与旧 dry-run 语义一致）
            sample=(sample if sample is not None else ([] if dry_run else None)),
            run_id=os.environ.get("SYNETRA_RUN_ID", ""),
            collect_samples=True,
        )
    except PipelineRunError as e:
        print("SYNETRA_STEP_METRICS=" + json.dumps(e.outcome.metrics, ensure_ascii=False))
        print("SYNETRA_STEP_FAILED=" + json.dumps(
            {{"node": e.node_id, "error": e.error}}, ensure_ascii=False))
        raise
    print("SYNETRA_STEP_METRICS=" + json.dumps(result.metrics, ensure_ascii=False))
    print("SYNETRA_STEP_SPILLED=%s" % ("1" if result.spilled else "0"))
    print("SYNETRA_SCRIPT_WRITTEN_ROWS=%d" % result.rows)
    if result.watermark:
        print("SYNETRA_SCRIPT_WATERMARK=%s" % result.watermark)


main()  # 运行期以 exec 注入新鲜命名空间执行，故不用 __name__ 守卫
'''


def render_config_script(cfg: TaskConfig) -> str:
    """把任务配置渲染成一份确定性的可运行脚本（纯函数，无副作用）。"""
    pipe = compile_task_pipeline(cfg)
    return _TEMPLATE.format(
        project=cfg.project,
        name=cfg.name,
        task_config=repr(cfg.model_dump(mode="json")),
        pipeline=repr(pipe.model_dump(mode="json")),
    )


__all__ = ["render_config_script"]
