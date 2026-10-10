"""按提示词生成单字段受限表达式（AI 生成表达式）。

三处复用，避免出现第二份口径：
- 规范要点：技能包 `expression` 的 `WRITING_TIPS` 标记块；
- 可用函数与禁止项：`app.expr.whitelist` 的权威清单；
- 校验：`validate_expression`（与任务保存链路同一校验器），未通过时在同一上下文追加受限修复轮。

上下文脱敏口径：只包含「提示词 + 源字段 + 目标字段 + 源列名清单」，**不携带源表样例数据行**——
列名与需求描述可出平台，数据行不出平台。生成结果仅作为建议回填，不自动保存任务。
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field

from app.expr.whitelist import allowed_functions, forbidden_notes, validate_expression
from app.llm.provider import LLMProvider
from app.llm.rag import skill_block, skill_fingerprints
from app.store import Store

#: 表达式修复轮次数上限（1 次生成 + 最多 1 次修复）
MAX_EXPR_REPAIR_ROUNDS = 1

#: 提示词长度上限（防止把整段文档塞进来）
MAX_PROMPT_CHARS = 2000


@dataclass
class ExpressionResult:
    ok: bool
    expression: str = ""
    issues: list[str] = field(default_factory=list)
    attempts: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "expression": self.expression,
            "issues": self.issues,
            "attempts": self.attempts,
        }


# ---- 输出解析 ----

def _extract_expression(text: str) -> str:
    """从模型输出中取出单行表达式：容忍 JSON、代码围栏、`expression =` 前缀与包裹引号。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    if raw.startswith("{"):
        try:
            data = _json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("expression"), str):
            raw = data["expression"].strip()
    for line in raw.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("```"):
            continue
        candidate = candidate.strip("`").strip()
        for prefix in ("expression =", "expression=", "expression:", "表达式："):
            if candidate.lower().startswith(prefix):
                candidate = candidate[len(prefix):].strip()
        candidate = candidate.strip().rstrip(";；")
        if candidate:
            return candidate
    return ""


# ---- 提示词（复用技能包与白名单，不写第二份口径） ----

def expression_system() -> str:
    """会话级 system：角色约束 + 技能包写法要点 + 权威禁止项与可用函数。"""
    tips = skill_block("expression", "WRITING_TIPS")
    return (
        "你是 ETL 字段转换表达式的生成助手：只输出一行受限 Python 表达式。\n"
        "写法要点：\n"
        + "\n".join(f"- {t}" for t in tips)
        + "\n禁止项：\n"
        + "\n".join(f"- {n}" for n in forbidden_notes())
        + "\n可用函数（必须小写）：" + "、".join(sorted(allowed_functions()))
    )


def build_expression_prompt(prompt: str, source_field: str = "", target_field: str = "",
                            columns: list[str] | None = None) -> str:
    """构造本轮 user 消息：只给列名清单，不给样例数据行。"""
    cols = [c for c in (columns or []) if c]
    return (
        "需求：\n"
        f"{prompt.strip()}\n\n"
        f"源字段：{source_field or '（未指定）'}\n"
        f"目标字段：{target_field or source_field or '（未指定）'}\n"
        f"可引用的源列（只有这些列名可引用）：{'、'.join(cols) if cols else '（未提供源列清单）'}\n\n"
        "请只输出一行表达式，不要输出解释文字、不要输出 JSON、不要加代码围栏。"
    )


def build_expression_repair_turn(candidate: str, issues: list[str]) -> str:
    """修复轮：回喂候选表达式与违规原因，要求只输出修正后的一行表达式。"""
    return (
        "上一个候选表达式未通过受限表达式白名单校验：\n"
        f"候选：{candidate or '（空）'}\n"
        + "\n".join(f"- {msg}" for msg in issues)
        + "\n请只输出修正后的一行表达式，不要输出解释文字。"
    )


# ---- 生成入口 ----

def generate_expression(store: Store, provider: LLMProvider, prompt: str, *,
                        source_field: str = "", target_field: str = "",
                        columns: list[str] | None = None, actor: str = "") -> ExpressionResult:
    """生成受限表达式；违规时在同一上下文追加受限修复轮，达上限返回可读原因。

    返回的 `expression` 仅在 `ok=True` 时非空：修复后仍不通过时不返回候选，
    以免前端把不合规表达式回填进用户的输入框。
    """
    system = expression_system()
    skills = skill_fingerprints()
    history: list[dict[str, str]] = [
        {"role": "user", "content": build_expression_prompt(prompt, source_field, target_field, columns)}
    ]
    issues: list[str] = []
    attempts = 0
    for attempt in range(1, MAX_EXPR_REPAIR_ROUNDS + 2):  # 1 次生成 + 最多 1 次修复
        attempts = attempt
        raw = provider.converse(history, system=system)
        candidate = _extract_expression(raw)
        issues = validate_expression(candidate) if candidate else ["模型未输出可用的表达式"]
        _audit(store, actor, provider, attempt, ok=not issues, issues=issues, skills=skills)
        if not issues:
            return ExpressionResult(True, candidate, [], attempts)
        if attempt <= MAX_EXPR_REPAIR_ROUNDS:
            history = history + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": build_expression_repair_turn(candidate, issues)},
            ]
    return ExpressionResult(False, "", issues, attempts)


def _audit(store: Store, actor: str, provider: LLMProvider, attempt: int, *,
           ok: bool, issues: list[str], skills: list[str]) -> None:
    """审计每次调用（含修复轮与失败），带模型与本次使用的技能包指纹。"""
    store.log_event(actor=actor, project="", action="llm_expression_call", detail={
        "provider": provider.name,
        "model": getattr(provider, "model_name", ""),
        "attempt": attempt,
        "ok": ok,
        "issues": list(issues),
        "skills": list(skills),
    })
