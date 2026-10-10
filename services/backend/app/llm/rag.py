"""RAG 上下文注入（任务 5.2 / 决策 7）。

加载随服务分发的两类上下文：
- `contracts/*.md`：etl_sdk 契约与示例，约束生成代码符合 SDK 规范；
- `skills/*/SKILL.md`：按能力划分的技能包（如 `expression` 字段转换规则与受限表达式规范）。

两类上下文均与具体供应商无关：任何 provider 的请求都会携带它们。
技能包同时以「会话级 system 段落」的形式注入多轮草稿路径（见 task_draft.session_system）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CONTRACTS_DIR = Path(__file__).parent / "contracts"
_SKILLS_DIR = Path(__file__).parent / "skills"


def skills_dir() -> Path:
    """技能包根目录（随服务分发；在线编辑与一致性校验共用同一位置）。"""
    return _SKILLS_DIR


def _load_contracts(contracts_dir: Path | None = None) -> str:
    directory = contracts_dir or _CONTRACTS_DIR
    parts: list[str] = []
    for path in sorted(directory.glob("*.md")):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def _skill_paths(skills_dir: Path | None = None) -> list[Path]:
    directory = skills_dir or _SKILLS_DIR
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*/SKILL.md") if p.is_file())


def load_skills(skills_dir: Path | None = None) -> list[tuple[str, str]]:
    """返回 [(技能名, 技能正文)]，按目录名排序（与供应商无关）。"""
    return [(p.parent.name, p.read_text(encoding="utf-8")) for p in _skill_paths(skills_dir)]


def skill_fingerprints(skills_dir: Path | None = None) -> list[str]:
    """技能名@内容指纹，用于审计「本次生成用了哪一版规范」。"""
    return [f"{p.parent.name}@{hashlib.sha256(p.read_bytes()).hexdigest()[:8]}"
            for p in _skill_paths(skills_dir)]


def skill_block(skill_name: str, marker: str, skills_dir: Path | None = None) -> list[str]:
    """读取指定技能包中的标记块行（形如 `<!-- MARKER:BEGIN -->` … `<!-- MARKER:END -->`）。

    供提示词复用技能包里的要点，避免在代码里再写第二份口径。
    """
    for name, body in load_skills(skills_dir):
        if name != skill_name:
            continue
        begin, end = f"<!-- {marker}:BEGIN -->", f"<!-- {marker}:END -->"
        if begin in body and end in body:
            chunk = body.split(begin, 1)[1].split(end, 1)[0]
            return [line.strip() for line in chunk.splitlines() if line.strip()]
    return []


def build_rag_context(contracts_dir: Path | None = None,
                      skills_dir: Path | None = None) -> str:
    """返回注入到 LLM 生成请求中的契约、技能包与示例文本（供应商无关）。"""
    sections: list[str] = []
    raw_contracts = _load_contracts(contracts_dir)
    if raw_contracts:
        sections.append(
            "# etl_sdk 契约（生成脚本必须遵守）\n"
            "# 以下为当前 SDK 规范与示例，生成代码须严格按此写导入、抽取、转换、加载。\n"
            + raw_contracts
        )
    for name, body in load_skills(skills_dir):
        sections.append(f"# 技能包：{name}（必须遵守）\n\n{body}")
    return "\n\n".join(sections)


def build_generation_prompt(task_config_redacted: object, rag_context: str) -> str:
    """组装发送给 LLM 的 prompt：脱敏配置 + 契约与技能包上下文。"""
    import json

    return (
        "请根据以下 ETL 任务配置生成一份 Python 同步脚本。\n"
        "要求：严格遵守 etl_sdk 契约；只能使用配置中给出的字段与转换规则"
        "（配置里的受限表达式必须原样从配置读取，不得自行硬编码表达式逻辑）；"
        "不得引入配置之外的秘密；不得写入目标之外的表。\n\n"
        f"【任务配置（已脱敏）】\n{json.dumps(task_config_redacted, ensure_ascii=False, indent=2)}\n\n"
        f"【etl_sdk 契约与技能包】\n{rag_context}\n\n"
        "只输出 Python 代码，不要解释。"
    )
