"""自然语言 → TaskConfig 草稿：多轮对话会话式生成（决策 1/2/3）。

会话轮次（同一会话内连续追加，见 app/llm/session.py）：
  turn 1  骨架识别：用户需求 + 连接清单 → assistant 输出骨架 JSON
  turn 2  源表结构注入（三级降级链：Catalog → information_schema → LLM 推断）→ 完整 TaskConfig
  turn 3+ 表达式修复轮 / 用户修订轮，均在既有会话历史上追加

会话级 `system`（角色约束 + 表达式技能包）由服务端每轮重组，不进入轮次历史。
兼容：调用方不传 `history` 时退回既有单轮编排（第二次调用重拼上下文）。

安全（决策 6）：输出必须反序列化为 `TaskConfig`；conn_key 必须存在于连接库；草稿不落库。
审计（决策 7）：每次生成/修订调用（含失败与自动重试）写 `store.log_event`，带会话标识与轮次序号。
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.expr.whitelist import allowed_functions, forbidden_notes, validate_expression
from app.llm.provider import LLMProvider
from app.llm.rag import load_skills, skill_block, skill_fingerprints
from app.llm.session import (
    append_turn,
    conversation_id,
    trim_history,
    validate_history,
)
from app.metadata import Catalog
from app.models.connection import ConnectionType
from app.models.task_config import FieldRule, TaskConfig, TransformKind
from app.queries.executor import run_select
from app.store import Store
from app.transform import validate_rules

_SAFE_TABLE_NAME = re.compile(r"^[A-Za-z0-9_`.\[\]\"\-]+$")


class DraftGenerationError(Exception):
    """草稿生成失败（LLM 输出非法重试仍失败 / 连接校验拒绝）。"""


@dataclass
class DraftResult:
    draft: dict
    warnings: list[str] = field(default_factory=list)
    sample: dict = field(default_factory=lambda: {
        "columns": [], "rows": [], "available": False, "note": "",
    })
    schema_source: str = ""
    inferred_fields: bool = False
    # 多轮会话：模型可见轮次历史（客户端 opaque 回传）与裁剪标记
    history: list[dict[str, str]] = field(default_factory=list)
    history_trimmed: bool = False
    # 表达式校验结果（字段级；为空表示全部通过或未校验）
    expression_issues: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "draft": self.draft,
            "warnings": self.warnings,
            "sample": self.sample,
            "schema_source": self.schema_source,
            "inferred_fields": self.inferred_fields,
            "history": self.history,
            "history_trimmed": self.history_trimmed,
            "expression_issues": self.expression_issues,
        }


# ---- 骨架契约（第一轮输出） ----

class Skeleton(BaseModel):
    source_conn_key: str = ""
    source_object: str = ""
    target_conn_key: str = ""
    target_object: str = ""
    mode: str = "append"  # append | upsert（incremental 归并为 append + 增量字段）
    primary_keys: list[str] = []
    incremental_field: str = ""
    watermark_field: str = ""


# ---- JSON 解析与围栏剥离 ----

def _strip_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return text


def _parse_json(text: str) -> dict:
    return json.loads(_strip_json(text))


def _parse_skeleton(text: str) -> Skeleton:
    raw = _parse_json(text)
    mode = str(raw.get("mode") or "append").strip().lower()
    if mode == "incremental":
        mode = "append"
    data = {
        "source_conn_key": str(raw.get("source_conn_key") or ""),
        "source_object": str(raw.get("source_object") or ""),
        "target_conn_key": str(raw.get("target_conn_key") or ""),
        "target_object": str(raw.get("target_object") or ""),
        "mode": mode,
        "primary_keys": raw.get("primary_keys") or [],
        "incremental_field": str(raw.get("incremental_field") or ""),
        "watermark_field": str(raw.get("watermark_field") or ""),
    }
    try:
        return Skeleton.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"骨架输出不符合契约: {e}") from e


def _parse_full_task(text: str, project: str | None) -> dict:
    raw = _parse_json(text)
    raw["project"] = project or ""
    if not raw.get("name"):
        raw["name"] = raw.get("target_object") or "nl-task"
    mode = str(raw.get("mode") or "append").strip().lower()
    if mode == "incremental":
        mode = "append"
    raw["mode"] = mode
    inc = raw.get("incremental") or {}
    raw["incremental"] = {
        "field": inc.get("field") or "",
        "watermark_field": inc.get("watermark_field") or inc.get("field") or "",
        "first_run_policy": inc.get("first_run_policy") or "all",
    }
    raw.setdefault("fields", [])
    try:
        return TaskConfig.model_validate(raw).model_dump()
    except ValidationError as e:
        raise ValueError(f"输出不符合 TaskConfig 契约: {e}") from e


# ---- 审计（决策 7） ----

def _audit_call(store: Store, actor: str, project: str, provider_name: str,
                model: str, round_name: str, attempt: int, ok: bool,
                error: str | None = None, conversation: str = "",
                turn: int = 0, skills: list[str] | None = None) -> None:
    store.log_event(actor=actor, project=project, action="llm_draft_call", detail={
        "provider": provider_name, "model": model, "round": round_name,
        "attempt": attempt, "ok": ok, "error": error,
        "conversation_id": conversation, "turn": turn,
        "skills": list(skills or []),
    })


def _round(provider: LLMProvider, history: list[dict[str, str]], system: str,
           parse, audit, round_name: str) -> tuple[object, list[dict[str, str]]]:
    """在会话历史上追加一轮调用。

    - 发往供应商的消息 = 本轮历史（超限时先裁剪）+ 会话级 `system`；
    - 输出非法时把「上次输出 + 错误信息」追加进历史后自动重试一次
      （与表达式修复轮的计数相互独立）；
    - 未配置（ProviderNotConfigured）与请求层错误（ProviderRequestError）直接上抛，不重试。

    返回 (解析结果, 追加了本轮 assistant 输出后的会话历史)。
    """
    current = list(history)
    for attempt in (1, 2):
        sent, _trimmed = trim_history(current)
        raw = provider.converse(sent, system=system)
        try:
            result = parse(raw)
        except Exception as e:
            audit(attempt, False, error=str(e))
            if attempt == 1:
                current = append_turn(current, "assistant", raw)
                current = append_turn(
                    current, "user",
                    f"上次输出非法，错误信息：{e}\n"
                    "请修正后严格输出合法 JSON，且不要输出任何解释文字。")
                continue
            raise DraftGenerationError(f"LLM 输出非法且自动重试仍失败（{round_name}）：{e}") from e
        audit(attempt, True)
        return result, append_turn(current, "assistant", raw)
    raise DraftGenerationError(f"LLM 输出非法（{round_name}）")


# ---- T5：表结构三级降级链 ----

def _information_schema_sql(ctype: ConnectionType, table: str) -> str | None:
    """构造 information_schema 列查询（只读）；表名经单引号转义防注入。"""
    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema, name = "", table

    def esc(s: str) -> str:
        return s.replace("'", "''")

    name_sql = f"table_name = '{esc(name)}'"
    if ctype is ConnectionType.MYSQL:
        schema_sql = f"table_schema = '{esc(schema)}'" if schema else "table_schema = DATABASE()"
        return (f"SELECT column_name, data_type FROM information_schema.columns "
                f"WHERE {schema_sql} AND {name_sql} ORDER BY ordinal_position")
    if ctype is ConnectionType.POSTGRESQL:
        schema_sql = f"table_schema = '{esc(schema)}'" if schema else "table_schema = current_schema()"
        return (f"SELECT column_name, data_type FROM information_schema.columns "
                f"WHERE {schema_sql} AND {name_sql} ORDER BY ordinal_position")
    return None


def resolve_source_schema(store: Store, conn_key: str, table: str) -> tuple[dict, str, bool]:
    """三级降级链：① Catalog → ② information_schema（只读）→ ③ 推断。

    返回 (schema dict, 来源标识, 是否推断)。
    """
    # ① 元数据 Catalog
    cols = Catalog(get_settings().data_dir).describe_table(conn_key, table)
    if cols:
        return {
            "columns": [{"name": c["column_name"], "type": c["column_type"]} for c in cols],
            "primary_keys": [c["column_name"] for c in cols if c["is_primary"]],
        }, "catalog", False
    # ② 连接只读查询 information_schema（仅 MySQL/PostgreSQL）
    try:
        conn = store.get_connection(conn_key)
    except KeyError:
        conn = None
    if conn and conn.ctype in (ConnectionType.MYSQL, ConnectionType.POSTGRESQL):
        sql = _information_schema_sql(conn.ctype, table)
        if sql:
            secret = store.get_secret(conn_key).decode() if store.has_secret(conn_key) else None
            try:
                res = run_select(conn.ctype, conn.meta, secret, sql, limit=500)
                columns = [{"name": row[0], "type": row[1]} for row in res.rows if row]
                if columns:
                    return {"columns": columns, "primary_keys": []}, "information_schema", False
            except Exception:  # noqa: BLE001 - 连接不可读则降级 ③
                columns = []
    # ③ LLM 推断（标记「字段为推断，待核对」）
    return {
        "columns": [],
        "primary_keys": [],
        "inferred_note": "字段为推断，待核对（源表结构不可得）",
    }, "inferred", True


# ---- T6：样例预览与目标列警告 ----

def _sample_preview(store: Store, conn_key: str, table: str) -> dict:
    """源连接可读时内嵌 10 行样例；预览失败不阻断草稿展示（降级为提示）。"""
    try:
        conn = store.get_connection(conn_key)
    except KeyError:
        return {"columns": [], "rows": [], "available": False, "note": "源连接不存在"}
    secret = store.get_secret(conn_key).decode() if store.has_secret(conn_key) else None
    if conn.ctype in (ConnectionType.MYSQL, ConnectionType.POSTGRESQL):
        if not _SAFE_TABLE_NAME.match(table or ""):
            return {"columns": [], "rows": [], "available": False,
                    "note": "表名含非法字符，跳过样例预览"}
        try:
            res = run_select(conn.ctype, conn.meta, secret, f"SELECT * FROM {table} LIMIT 10", limit=10)
            return {"columns": res.columns, "rows": res.rows[:10],
                    "available": True, "note": ""}
        except Exception as e:  # noqa: BLE001
            return {"columns": [], "rows": [], "available": False, "note": f"样例预览失败：{e}"}
    if conn.ctype is ConnectionType.API:
        try:
            from app.sources.rest import fetch_sample

            params = dict(conn.meta.get("params") or {})
            params.setdefault("limit", "10")
            result = fetch_sample(
                conn.meta.get("method", "GET"), conn.meta.get("url", ""),
                headers=dict(conn.meta.get("headers") or {}), params=params,
                secret=secret, auth_scheme=conn.meta.get("auth", "none"), limit=10,
            )
            arr = result.get("sample", []) if isinstance(result, dict) else (result or [])
            rows = list(arr[:10])
            columns = list(rows[0].keys()) if rows else []
            return {"columns": columns, "rows": rows, "available": True, "note": ""}
        except Exception as e:  # noqa: BLE001
            return {"columns": [], "rows": [], "available": False, "note": f"样例预览失败：{e}"}
    return {"columns": [], "rows": [], "available": False,
            "note": f"类型 {conn.ctype.value} 暂不支持在线预览"}


def _target_warnings(store: Store, target_conn_key: str, target_object: str,
                     fields: list[dict]) -> list[str]:
    """目标字段不在目标表已知列内时返回警告；目标表未知则跳过核对。"""
    cols = Catalog(get_settings().data_dir).describe_table(target_conn_key, target_object)
    if not cols:
        return []
    known = {c["column_name"] for c in cols}
    warnings = []
    for f in fields:
        tgt = f.get("target") or f.get("source")
        if tgt and tgt not in known:
            warnings.append(f"目标字段「{tgt}」不在目标表 {target_object} 已知列内")
    return warnings


def _validate_connections(store: Store, draft: dict) -> None:
    """conn_key 必须存在于连接库，否则拒绝（虚构连接不可进入保存流程）。"""
    for side, key in (("源", draft.get("source_conn_key")),
                      ("目标", draft.get("target_conn_key"))):
        if not key:
            raise DraftGenerationError(f"{side}连接未指定")
        try:
            store.get_connection(key)
        except KeyError:
            raise DraftGenerationError(f"{side}连接不存在：{key}") from None


def _decorate(store: Store, raw: dict, *, schema_source: str,
              inferred_fields: bool, history: list[dict[str, str]] | None = None,
              history_trimmed: bool = False,
              expression_issues: list[dict[str, str]] | None = None) -> DraftResult:
    _validate_connections(store, raw)
    warnings = _target_warnings(store, raw.get("target_conn_key", ""),
                                raw.get("target_object", ""), raw.get("fields") or [])
    if inferred_fields:
        warnings.insert(0, "字段为推断，待核对（源表结构不可得）")
    if history_trimmed:
        warnings.insert(0, "会话历史超过长度上限，已裁剪最早轮次（会话语境可能受影响）")
    sample = _sample_preview(store, raw.get("source_conn_key", ""),
                             raw.get("source_object", ""))
    return DraftResult(draft=raw, warnings=warnings, sample=sample,
                       schema_source=schema_source, inferred_fields=inferred_fields,
                       history=list(history or []), history_trimmed=history_trimmed,
                       expression_issues=list(expression_issues or []))


# ---- 提示词构造 ----

_SESSION_SYSTEM = (
    "你是严谨的数据同步 ETL 任务配置生成器，与用户进行多轮对话逐步完善任务配置。"
    "每一轮都只输出一个合法 JSON 对象，不要输出任何解释文字或代码围栏。"
    "不得虚构连接（conn_key 必须取自给定的连接清单）；不得虚构字段"
    "（字段必须来自给定的源表结构；结构不可得时才按描述推断）；"
    "修订时保持既有字段映射稳定，仅按修订指令调整。"
)

# 字段规则的类型选择判据（决策 4/8：优先简单类型，不得已才用受限表达式）
_KIND_CRITERIA = (
    "字段处理方式（kind）选择判据，务必优先使用更简单的类型：\n"
    "- direct：源字段直接映射到目标字段；\n"
    "- constant：目标字段为固定常量值；\n"
    "- null_fill：源值为空（null 或空串）时用固定的默认值兜底；\n"
    "- cast：仅做类型转换（配合 cast_to=int/float/str/bool/bool_int）；\n"
    "- expression：仅当以上四类都无法表达时才使用受限表达式。\n"
    "能用 cast/null_fill/direct 表达的，不得写成 expression。"
)

# 输出契约示例组（覆盖五类 kind 的形态）
_FIELDS_CONTRACT = (
    '  "fields": [\n'
    '    {"source": "源列", "target": "目标列", "kind": "direct"},\n'
    '    {"source": "源列", "target": "目标列", "kind": "cast", "cast_to": "int"},\n'
    '    {"source": "源列", "target": "目标列", "kind": "null_fill", "null_default": "0"},\n'
    '    {"source": "源列", "target": "目标列", "kind": "constant", "constant_value": "CN"},\n'
    '    {"source": "源列", "target": "目标列", "kind": "expression", "expression": "concat(源列,\'-\',id)"}\n'
    "  ],\n"
)


def session_system() -> str:
    """会话级 system 段落：角色约束 + 规则选择判据 + 表达式技能包。

    每轮由服务端重新组装（客户端不可伪造），且**不进入轮次历史**，因此不会随轮次重复膨胀，
    也不会被历史裁剪策略影响。
    """
    parts = [_SESSION_SYSTEM, _KIND_CRITERIA]
    for name, body in load_skills():
        parts.append(f"# 技能包：{name}（必须遵守）\n\n{body}")
    return "\n\n".join(parts)


def build_skeleton_prompt(description: str, connections: list[dict],
                          source_conn_key: str | None = None,
                          target_conn_key: str | None = None) -> str:
    fixed = ""
    if source_conn_key:
        fixed += f"\n数据源连接已由用户指定为「{source_conn_key}」，必须使用，不得更改。"
    if target_conn_key:
        fixed += f"\n目标连接已由用户指定为「{target_conn_key}」，必须使用，不得更改。"
    return (
        "连接清单（JSON）：\n"
        f"{json.dumps(connections, ensure_ascii=False)}\n\n"
        "用户需求：\n"
        f"{description}\n"
        f"{fixed}\n\n"
        "请输出骨架 JSON，仅含以下字段：\n"
        '{\n'
        '  "source_conn_key": "源连接 key（必须取自清单或用户指定）",\n'
        '  "source_object": "源表/对象名",\n'
        '  "target_conn_key": "目标连接 key（必须取自清单或用户指定）",\n'
        '  "target_object": "目标表/对象名",\n'
        '  "mode": "append 或 upsert",\n'
        '  "primary_keys": ["主键列"],\n'
        '  "incremental_field": "增量字段；全量则空字符串",\n'
        '  "watermark_field": "水位字段；全量则空字符串"\n'
        "}"
    )


def build_schema_turn(skeleton: Skeleton, schema: dict, source: str,
                      inferred: bool) -> str:
    """第二轮（turn 2）：源表结构注入。

    骨架识别结论已作为上一轮 assistant 输出存在于会话历史中，故本轮不再重复；
    仅补充源表结构与「本轮以此为准」的连接/对象（覆盖骨架可能被用户指定值改写的取值），
    再给出完整 TaskConfig 的输出契约。
    """
    source_cn = {"catalog": "元数据 Catalog", "information_schema": "实时 information_schema",
                 "inferred": "推断（结构不可得）"}.get(source, source)
    note = ("\n注意：以下字段为 LLM 推断所得，仅供核对，请据此输出字段映射。"
            if inferred else "")
    authoritative = {
        "source_conn_key": skeleton.source_conn_key,
        "source_object": skeleton.source_object,
        "target_conn_key": skeleton.target_conn_key,
        "target_object": skeleton.target_object,
    }
    return (
        f"源表结构（来源：{source_cn}）：\n"
        f"{json.dumps(schema, ensure_ascii=False)}{note}\n\n"
        "本轮必须使用的连接与对象（若与上一轮骨架不同，以此为准）：\n"
        f"{json.dumps(authoritative, ensure_ascii=False)}\n\n"
        "请输出完整 TaskConfig JSON，字段：\n"
        '{\n'
        '  "name": "简短任务名",\n'
        '  "source_conn_key": "...",\n'
        '  "source_object": "...",\n'
        '  "target_conn_key": "...",\n'
        '  "target_object": "...",\n'
        '  "mode": "append 或 upsert",\n'
        '  "primary_keys": ["..."],\n'
        '  "incremental": {"field": "增量字段或空", "watermark_field": "水位字段或空", "first_run_policy": "all"},\n'
        f"{_FIELDS_CONTRACT}"
        '  "schedule": ""\n'
        "}"
    )


def build_revise_turn(instruction: str) -> str:
    """修订轮（多轮会话内）：当前草稿已在会话历史中，无需重发草稿 JSON。"""
    return (
        "修订指令：\n"
        f"{instruction}\n\n"
        "请基于本会话中当前的完整 TaskConfig，输出修订后的完整 TaskConfig JSON"
        "（与当前草稿同构，未被指令涉及的字段映射保持不变）。"
    )


def build_revise_prompt(draft: dict, instruction: str) -> str:
    """修订轮（单轮退化模式）：调用方未提供会话历史时，重发草稿 JSON + 指令。"""
    return (
        "当前草稿（TaskConfig JSON）：\n"
        f"{json.dumps(draft, ensure_ascii=False)}\n\n"
        "修订指令：\n"
        f"{instruction}\n\n"
        "请输出修订后的完整 TaskConfig JSON（与输入同构，保持字段映射稳定）。"
    )


# ---- 表达式校验回环（决策 9） ----

#: 修复轮次数上限（与输出非法重试相互独立）
MAX_REPAIR_ROUNDS = 1


def build_repair_turn(violations: list[str]) -> str:
    """修复轮提示：回喂「违规字段 + 原因 + 规范要点」。

    规范要点来自权威来源，避免第三份口径：
    - 写法要点：技能包 `expression` 的 `WRITING_TIPS` 标记块（含切片、拼接、大小写）；
    - 禁止项：`app.expr.whitelist.forbidden_notes()`；
    - 可用函数：`app.expr.whitelist.allowed_functions()`。
    """
    tips = skill_block("expression", "WRITING_TIPS")
    return (
        "你刚输出的 TaskConfig 中有字段的表达式未通过受限表达式白名单校验：\n"
        + "\n".join(f"- {v}" for v in violations)
        + "\n\n写法要点：\n"
        + "\n".join(f"- {t}" for t in tips)
        + "\n必须遵守的禁止项：\n"
        + "\n".join(f"- {n}" for n in forbidden_notes())
        + "\n可用函数（须小写）：" + "、".join(sorted(allowed_functions()))
        + "\n请输出修正后的完整 TaskConfig JSON：只修改违规字段（改用合规表达式，"
        "或改用更简单的 kind 如 cast / null_fill），其余字段映射保持不变。"
    )


def _rules_of(raw: dict) -> list[FieldRule]:
    """从草稿中解析字段规则；结构非法的条目跳过（交由后续校验/保存链路处理）。"""
    rules: list[FieldRule] = []
    for item in raw.get("fields") or []:
        with contextlib.suppress(ValidationError):
            rules.append(FieldRule.model_validate(item))
    return rules


def expression_issues(raw: dict) -> list[dict[str, str]]:
    """按字段给出表达式校验结果（结构化，供前端字段级标红）。"""
    issues: list[dict[str, str]] = []
    for rule in _rules_of(raw):
        if rule.kind is not TransformKind.EXPRESSION:
            continue
        for msg in validate_expression(rule.expression or ""):
            issues.append({"field": rule.source,
                           "target": rule.target or rule.source,
                           "message": msg})
    return issues


def _repair_expressions(provider: LLMProvider, history: list[dict[str, str]],
                        system: str, raw: dict, project: str | None,
                        audit, turn: int) -> tuple[dict, list[dict[str, str]]]:
    """表达式校验回环：违规时在同一会话追加修复轮（最多 MAX_REPAIR_ROUNDS 次）。

    返回 (最终草稿, 会话历史)。修复轮输出非法时保留原草稿与修复前历史，
    由调用方以 expression_issues 呈现告警。
    """
    violations = validate_rules(_rules_of(raw))
    if not violations:
        return raw, history
    h = append_turn(history, "user", build_repair_turn(violations))
    for _ in range(MAX_REPAIR_ROUNDS):
        try:
            repaired, h2 = _round(provider, h, system,
                                  lambda text: _parse_full_task(text, project),
                                  audit("repair", turn), "repair")
        except DraftGenerationError:
            return raw, history  # 修复失败：不遮蔽原草稿
        h = h2
        violations = validate_rules(_rules_of(repaired))
        raw = repaired
        if not violations:
            break
    return raw, h


# ---- 生成入口 ----

def _auditor(store: Store, actor: str, project: str, provider_name: str, model: str,
             ctx: dict):
    """构造审计回调：每轮调用的会话标识与轮次序号由 ctx 动态提供。"""
    def audit(round_name: str, turn: int):
        def _fn(attempt: int, ok: bool, error: str | None = None) -> None:
            _audit_call(store, actor, project, provider_name, model, round_name,
                        attempt, ok, error, ctx.get("conversation_id", ""), turn,
                        ctx.get("skills"))
        return _fn
    return audit


def _next_turn(history: list[dict[str, str]]) -> int:
    """本轮在会话内的轮次序号（1 起，按已发生的轮次数推算）。"""
    return len(history) // 2 + 1


def generate_draft(store: Store, provider: LLMProvider, description: str,
                   project: str | None, connections: list[dict], actor: str,
                   source_conn_key: str | None = None,
                   target_conn_key: str | None = None,
                   history: list[dict] | None = None) -> DraftResult:
    """多轮生成：在同一会话历史上追加「骨架轮 → 源表结构轮」并输出完整 TaskConfig 草稿。"""
    base = validate_history(history)
    model = provider.model_name
    system = session_system()
    ctx: dict = {"conversation_id": "", "skills": skill_fingerprints()}
    audit = _auditor(store, actor, project or "", provider.name, model, ctx)

    # turn 1：骨架识别（需求 + 连接清单）
    h = append_turn(base, "user",
                    build_skeleton_prompt(description, connections,
                                          source_conn_key, target_conn_key))
    ctx["conversation_id"] = conversation_id(h)
    turn = _next_turn(base)  # 逻辑轮次序号（按会话内的轮次数推进，不因输出非法重试而漂移）
    skeleton, h = _round(provider, h, system, _parse_skeleton,
                         audit("first", turn), "first")
    turn += 1
    # 用户下拉指定的连接优先于 LLM 骨架输出，避免 LLM 选错连接
    if source_conn_key:
        skeleton.source_conn_key = source_conn_key
    if target_conn_key:
        skeleton.target_conn_key = target_conn_key
    schema, source, inferred = resolve_source_schema(
        store, skeleton.source_conn_key, skeleton.source_object)

    # turn 2：源表结构注入 → 完整 TaskConfig
    h = append_turn(h, "user", build_schema_turn(skeleton, schema, source, inferred))
    raw, h = _round(provider, h, system,
                    lambda text: _parse_full_task(text, project),
                    audit("second", turn),
                    "second")
    # 表达式校验回环：违规时在同一会话追加修复轮（最多 1 次）
    raw, h = _repair_expressions(provider, h, system, raw, project, audit, turn + 1)
    h, trimmed = trim_history(h)
    return _decorate(store, raw, schema_source=source, inferred_fields=inferred,
                     history=h, history_trimmed=trimmed,
                     expression_issues=expression_issues(raw))


def generate_revise(store: Store, provider: LLMProvider, draft: dict,
                    instruction: str, project: str | None, actor: str,
                    history: list[dict] | None = None) -> DraftResult:
    """修订轮：在既有会话历史上追加修订轮；未提供历史时退回单轮（重发草稿 JSON）。"""
    base = validate_history(history)
    model = provider.model_name
    system = session_system()
    ctx: dict = {"conversation_id": "", "skills": skill_fingerprints()}
    audit = _auditor(store, actor, project or "", provider.name, model, ctx)

    if base:
        h = append_turn(base, "user", build_revise_turn(instruction))
    else:
        # 单轮退化：无会话历史可用时，重发当前草稿与指令
        h = [{"role": "user", "content": build_revise_prompt(draft, instruction)}]
    ctx["conversation_id"] = conversation_id(h)
    turn = _next_turn(base)
    raw, h = _round(provider, h, system,
                    lambda text: _parse_full_task(text, project),
                    audit("revise", turn), "revise")
    raw, h = _repair_expressions(provider, h, system, raw, project, audit, turn + 1)
    h, trimmed = trim_history(h)
    return _decorate(store, raw, schema_source="revise", inferred_fields=False,
                     history=h, history_trimmed=trimmed,
                     expression_issues=expression_issues(raw))
