"""从任务配置确定性生成可运行 Python 脚本（任务：保存即生成执行体）。

`render_config_script` 是纯函数：不依赖 LLM、不访问外部状态，仅根据 TaskConfig
字段渲染一段同步脚本。生成的脚本严格遵循 etl_sdk 契约（readers / writers），因此：

- 既可被 dry_run_script 以子进程 dry-run 执行（SYNETRA_DRY_RUN=1，writers 只记数不写目标），
- 也可在真实运行期被 etl_sdk 的读取/加载器执行。

连接一律以 conn_key 引用、凭证由运行期按 conn_key 从 store/config+vault 解析，
脚本自身绝不内联明文口令（与 app/llm/contracts/etl_sdk.md 契约一致）。
字段转换规则（direct/constant/null_fill/cast/expression）在脚本内联应用，
表达式使用与 app.expr.whitelist 一致的能力（仅白名单函数）。
"""

from __future__ import annotations

from app.expr.whitelist import EXPR_NAMESPACE_SOURCE
from app.models.task_config import TaskConfig


def render_config_script(cfg: TaskConfig) -> str:
    """把 TaskConfig 渲染成一份确定性的可运行同步脚本（纯函数，无副作用）。"""
    watermark_src = (cfg.incremental.field or "").strip() or None
    watermark_report = (cfg.incremental.watermark_field or cfg.incremental.field or "").strip() or None

    # 安全地嵌入配置字符串与结构为 Python 字面量（repr 保证 None/True/引号/换行
    # 均不破坏外层 Python 语法；json.dumps 会产出 null/true，非合法 Python，故用 repr）
    def j(v):
        return repr(v)

    src_conn = j(cfg.source_conn_key)
    tgt_conn = j(cfg.target_conn_key)
    target_obj = j(cfg.target_object)
    mode = j(cfg.mode.value)
    pks = j(cfg.primary_keys)
    src_field = j(watermark_src)
    report_field = j(watermark_report)
    fields = j([r.model_dump(mode="json") for r in cfg.fields])

    table_ident = cfg.source_object or "source_table"

    return f'''# -*- coding: utf-8 -*-
"""ETL 同步脚本（由 render_config_script 从任务配置确定性生成，请勿手改）。

契约：仅依赖 etl_sdk(readers/writers) 与标准库；连接仅以 conn_key 引用，
凭证由运行期按 conn_key 从 store/config+vault 解析，脚本不内联明文口令。
dry-run(SYNETRA_DRY_RUN=1) 下 writers 只计数不写目标，可直接被 dry_run_script 执行。
来源任务：{j(cfg.project)}/{j(cfg.name)}（task_key 由运行期绑定）。
"""
import os
from etl_sdk import readers, writers

# ---- 任务配置常量（确定性渲染，勿手改） ----
SRC_CONN_KEY = {src_conn}
TGT_CONN_KEY = {tgt_conn}
TARGET_OBJECT = {target_obj}
MODE = {mode}
PRIMARY_KEYS = {pks}
# 增量水位：源端过滤列（WATERMARK_SRC_FIELD）与目标记录的 max 水位标签（WATERMARK_REPORT_FIELD）
WATERMARK_SRC_FIELD = {src_field}
WATERMARK_REPORT_FIELD = {report_field}
# 目标字段转换规则列表（来自 TaskConfig.fields）
FIELDS = {fields}

{EXPR_NAMESPACE_SOURCE}


def _cast_value(value, cast_to):
    if cast_to not in ("int", "float", "str", "bool", "bool_int"):
        return value
    try:
        if cast_to == "bool_int":
            return 1 if value else 0
        return {{"int": int, "float": float, "str": str, "bool": bool}}[cast_to](value)
    except (TypeError, ValueError):
        return None


def _apply_rule(rule, row):
    """把一条字段规则应用到单行，返回目标列值（与 app.transform.apply_rule 语义一致）。"""
    kind = rule.get("kind", "direct")
    src = rule.get("source", "")
    if kind == "constant":
        return rule.get("constant_value")
    if kind == "null_fill":
        val = row.get(src)
        if val is None or val == "":
            return rule.get("null_default")
        return _cast_value(val, rule.get("cast_to"))
    if kind == "cast":
        return _cast_value(row.get(src), rule.get("cast_to"))
    if kind == "expression":
        ns = dict(_EXPR_NS)
        ns.update({{k: row[k] for k in row if isinstance(k, str) and k.isidentifier()}})
        return eval(rule.get("expression", ""), {{"__builtins__": {{}}}}, ns)
    return row.get(src)


def _transform(rows):
    """把抽取行按 FIELDS 规则映射为目标字段行。"""
    if not FIELDS:
        return rows
    out = []
    for row in rows:
        mapped = {{}}
        for r in FIELDS:
            mapped[r.get("target") or r.get("source")] = _apply_rule(r, row)
        out.append(mapped)
    return out


def main():
    # 增量水位：运行期经环境变量 SYNETRA_WATERMARK 提供；缺省则全量抽取
    watermark = os.environ.get("SYNETRA_WATERMARK", "")
    if WATERMARK_SRC_FIELD and watermark:
        sql = "SELECT * FROM {table_ident} WHERE %s > :wm" % WATERMARK_SRC_FIELD
        params = {{"wm": watermark}}
    else:
        sql = "SELECT * FROM {table_ident}"
        params = None

    # 抽取（dry-run 下返回样例行；真实运行期由 etl_sdk 按 SRC_CONN_KEY 连接）
    rows = readers.query(SRC_CONN_KEY, sql, params)
    # 按配置字段规则转换
    output = _transform(rows)
    # 加载目标（mode=upsert 走主键 upsert；dry-run 下仅记本应写入的行数）
    writer = writers.relational(TGT_CONN_KEY, TARGET_OBJECT, mode=MODE, primary_keys=PRIMARY_KEYS)
    # columns 传字段映射：目标表缺失时由运行期按列定义自动建表
    writer.buffered(output, watermark_field=WATERMARK_REPORT_FIELD, columns=FIELDS)
    # 打印写入行数（dry-run 时为"本应写入"计数）
    print("SYNETRA_SCRIPT_WRITTEN_ROWS=%d" % len(output))


main()  # 运行期（含 dry-run）以 exec 注入新鲜命名空间执行，故不用 __name__ 守卫
'''



__all__ = ["render_config_script"]