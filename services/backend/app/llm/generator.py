"""LLM+Python 生成路径编排（任务 5.3 / 决策 9）。

流程：配置层表达式校验 -> 脱敏配置 -> 注入 RAG 契约与表达式技能包 -> 调 LLM -> 静态校验
     -> （经 dry-run 与人工确认后）生效。

配置层表达式校验（决策 9）：生成脚本前先跑与保存链路同一个校验器（validate_rules）。
技能包要求「脚本从 fields[].expression 读取表达式、不得自行硬编码表达式逻辑」，
因此脚本内联表达式的合规性由这一层校验唯一覆盖；违规直接拒绝生成并给出可读原因。
"""

from __future__ import annotations

import contextlib

from pydantic import ValidationError

from app.llm.provider import LLMProvider
from app.llm.rag import build_generation_prompt, build_rag_context
from app.llm.redact import redact_object
from app.models.task_config import FieldRule
from app.scripting.checks import static_validate
from app.transform import validate_rules


class GenerateResult:
    def __init__(self, script: str, violations: list[str], redacted: object):
        self.script = script
        self.violations = violations
        self.redacted = redacted

    @property
    def valid(self) -> bool:
        return not self.violations


class ScriptGenerator:
    """集齐一次生成：表达式校验 + 脱敏 + RAG（契约与技能包）+ 调用 + 静态校验。"""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def generate(self, task_config: dict) -> GenerateResult:
        # 表达式必须先合规（与保存链路同一校验器）：违规脚本不得生成/生效
        rule_violations = validate_rules(_field_rules(task_config))
        if rule_violations:
            return GenerateResult(script="", violations=[
                f"表达式校验未通过，已拒绝生成脚本：{v}" for v in rule_violations], redacted={})
        redacted, _secrets = redact_object(task_config)
        context = build_rag_context()
        prompt = build_generation_prompt(redacted, context)
        script = self._provider.complete(prompt, system="你是严谨的数据同步存 Python 代码生成器。")
        script = _strip_fences(script)
        return GenerateResult(script=script, violations=static_validate(script), redacted=redacted)


def _field_rules(task_config: dict) -> list[FieldRule]:
    """从（可能已脱敏的）任务配置中解析字段规则；结构非法时按无规则处理。"""
    raw = task_config.get("fields") if isinstance(task_config, dict) else None
    rules: list[FieldRule] = []
    for item in raw or []:
        with contextlib.suppress(ValidationError):  # 结构非法交由后续流程处理
            rules.append(FieldRule.model_validate(item))
    return rules


def _strip_fences(text: str) -> str:
    """去掉 LLM 常见的 ```python 围栏。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text