"""技能包一致性校验与读写（单一实现，被 pytest 与「保存技能包」接口共用）。

为什么抽成独立模块：技能包是决定 AI 生成质量的核心资产，其正确性原先只由 CI 守门。
允许在线编辑后必须做到「保存即校验」——否则一致性破坏只能被 CI 发现，生产上表现为
生成质量静默下降。因此测试与保存接口调用同一份校验，禁止在测试里再写第二套断言。

校验内容（与既有 docs-as-tests 口径一致）：
1. 受支持函数清单 == `app.expr.whitelist.allowed_functions()`；
2. 禁止项清单逐字包含权威说明；
3. 写法要点（切片/小写/concat/row[）、五类规则与「选择判据」、SQL 方言对照齐全；
4. 示例文件各标记块可解析，且正例可求值、反例被拒、运行期错误类静态通过但求值失败、
   不合规范写法与推荐写法等价。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from app.expr.whitelist import (
    allowed_functions,
    apply_expression,
    forbidden_notes,
    validate_expression,
)
from app.llm.rag import skills_dir

#: 技能包名称：小写字母/数字开头，后接小写字母、数字、下划线或连字符（防路径穿越）
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: 允许在线编辑的技能包文件（白名单，其余一律拒绝写入）
SKILL_FILES = ("SKILL.md", "examples.md")

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_EXC = {"NameError": NameError, "TypeError": TypeError, "ValueError": ValueError}


# ---- 定位与读取 ----

def validate_skill_name(name: str) -> str | None:
    """返回 None 表示合法，否则返回可读原因。"""
    if not name:
        return "技能包名称不能为空"
    if not SKILL_NAME_RE.match(name):
        return f"技能包名称非法：{name!r}（只允许小写字母/数字/下划线/连字符）"
    return None


def skill_path(name: str) -> Path:
    """技能包目录；名称非法或越出技能包根目录时抛 ValueError。"""
    reason = validate_skill_name(name)
    if reason:
        raise ValueError(reason)
    root = skills_dir().resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise ValueError(f"技能包路径越界：{name!r}")
    return path


def validate_filename(filename: str) -> str | None:
    if filename not in SKILL_FILES:
        return f"只允许编辑技能包既定文件：{'、'.join(SKILL_FILES)}（收到 {filename!r}）"
    return None


def list_skill_names() -> list[str]:
    root = skills_dir()
    if not root.is_dir():
        return []
    return sorted(p.parent.name for p in root.glob("*/SKILL.md") if p.is_file())


def skill_files(name: str) -> list[str]:
    directory = skill_path(name)
    return [f for f in SKILL_FILES if (directory / f).is_file()]


def load_skill_file(name: str, filename: str) -> str:
    reason = validate_filename(filename)
    if reason:
        raise ValueError(reason)
    return (skill_path(name) / filename).read_text(encoding="utf-8")


def load_skill_files(name: str) -> dict[str, str]:
    """读取该技能包已存在的既定文件（按 SKILL_FILES 顺序）。"""
    return {f: load_skill_file(name, f) for f in skill_files(name)}


def parse_frontmatter(text: str) -> dict[str, str]:
    """极简 frontmatter 解析（只取顶层 `key: value`，不引入 yaml 依赖）。"""
    m = _FRONTMATTER_RE.match(text or "")
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


def fingerprint(name: str) -> str:
    """技能包内容指纹（与 rag.skill_fingerprints 同口径：主文件 sha256 前 8 位）。"""
    data = (skill_path(name) / "SKILL.md").read_bytes()
    return f"{name}@{hashlib.sha256(data).hexdigest()[:8]}"


def skill_meta(name: str) -> dict:
    directory = skill_path(name)
    files = skill_files(name)
    main = directory / "SKILL.md"
    fm = parse_frontmatter(main.read_text(encoding="utf-8")) if main.is_file() else {}
    updated = ""
    if main.is_file():
        updated = datetime.fromtimestamp(main.stat().st_mtime, tz=timezone.utc).isoformat(
            timespec="seconds")
    return {
        "name": name,
        "description": fm.get("description", ""),
        "applies_to": fm.get("applies_to", ""),
        "fingerprint": fingerprint(name),
        "updated_at": updated,
        "files": files,
    }


def list_skills() -> list[dict]:
    return [skill_meta(n) for n in list_skill_names()]


# ---- 校验 ----

def _block(text: str, marker: str) -> list[str] | None:
    """取标记块内的非空行；标记块缺失返回 None。"""
    pattern = rf"<!-- {marker}:BEGIN -->\n(.*?)\n<!-- {marker}:END -->"
    m = re.search(pattern, text or "", re.DOTALL)
    if not m:
        return None
    return [line.strip() for line in m.group(1).splitlines() if line.strip()]


def _fields(line: str, count: int) -> list[str] | None:
    parts = [p.strip() for p in line.split(" | ")]
    return parts if len(parts) == count else None


def lint_skill_files(files: Mapping[str, str]) -> list[str]:
    """校验技能包内容；返回问题清单（空表示通过）。校验的是提交内容本身，而非磁盘。"""
    issues: list[str] = []
    skill = files.get("SKILL.md", "")
    examples = files.get("examples.md", "")
    if not skill.strip():
        issues.append("缺少技能包主文件 SKILL.md 的内容")
    if not examples.strip():
        issues.append("缺少随包示例文件 examples.md 的内容")
    if issues:
        return issues

    # 1. 受支持函数清单与权威白名单同源
    fn_lines = _block(skill, "FUNCTIONS")
    if fn_lines is None:
        issues.append("SKILL.md 缺少 FUNCTIONS 标记块")
    else:
        declared = {f.strip() for f in fn_lines[0].split(",") if f.strip()}
        expected = set(allowed_functions())
        if declared != expected:
            missing = sorted(expected - declared)
            extra = sorted(declared - expected)
            issues.append(
                f"技能包函数清单与权威白名单不一致（缺少：{'、'.join(missing) or '无'}；"
                f"多余：{'、'.join(extra) or '无'}）")

    # 2. 禁止项逐字包含权威说明
    forbidden_lines = _block(skill, "FORBIDDEN")
    if forbidden_lines is None:
        issues.append("SKILL.md 缺少 FORBIDDEN 标记块")
    else:
        block_text = " ".join(forbidden_lines)
        for note in forbidden_notes():
            if note not in block_text:
                issues.append(f"技能包缺少权威禁止项说明：{note}")

    # 3. 写法要点、五类规则与 SQL 方言对照
    tips = _block(skill, "WRITING_TIPS")
    if not tips:
        issues.append("SKILL.md 缺少 WRITING_TIPS 标记块（或块内为空）")
    else:
        joined = " ".join(tips)
        for key in ("切片", "小写", "concat", "row["):
            if key not in joined:
                issues.append(f"写法要点缺少关键内容：{key}")
    if "选择判据" not in skill:
        issues.append("SKILL.md 缺少五类规则的选择判据")
    for kind in ("direct", "constant", "null_fill", "cast", "expression"):
        if kind not in skill:
            issues.append(f"SKILL.md 缺少规则类型说明：{kind}")
    if "切片" not in skill:
        issues.append("SKILL.md 未讲清「取子串靠切片」")
    for dialect in ("CONCAT", "LEFT", "RIGHT", "SUBSTRING", "COALESCE", "CASE WHEN"):
        if dialect not in skill:
            issues.append(f"SKILL.md 缺少 SQL 方言对照：{dialect}")

    # 4. 示例块可解析且结论成立
    issues.extend(_lint_examples(examples))
    return issues


def _lint_examples(examples: str) -> list[str]:
    issues: list[str] = []

    positive = _block(examples, "POSITIVE")
    if not positive:
        issues.append("examples.md 缺少 POSITIVE 标记块（或块内为空）")
    else:
        for line in positive:
            parts = _fields(line, 3)
            if not parts:
                issues.append(f"正例行字段数应为 3：{line!r}")
                continue
            expr, row_json, expected = parts
            violations = validate_expression(expr)
            if violations:
                issues.append(f"正例被校验拒绝：{expr} → {'; '.join(violations)}")
                continue
            try:
                actual = repr(apply_expression(expr, json.loads(row_json)))
            except Exception as e:  # noqa: BLE001 - 正例不应运行期失败
                issues.append(f"正例求值失败：{expr} → {e}")
                continue
            if actual != expected:
                issues.append(f"正例求值不符：{expr} → {actual}（期望 {expected}）")

    rejected = _block(examples, "REJECTED")
    if not rejected:
        issues.append("examples.md 缺少 REJECTED 标记块（或块内为空）")
    else:
        for line in rejected:
            parts = _fields(line, 2)
            if not parts:
                issues.append(f"反例行字段数应为 2：{line!r}")
                continue
            expr, reason = parts
            violations = validate_expression(expr)
            if not violations:
                issues.append(f"反例未被校验拒绝：{expr}")
            elif reason not in "; ".join(violations):
                issues.append(f"反例拒绝原因不含预期关键字：{expr} → {violations}")

    runtime = _block(examples, "RUNTIME_ERROR")
    if not runtime:
        issues.append("examples.md 缺少 RUNTIME_ERROR 标记块（或块内为空）")
    else:
        for line in runtime:
            parts = _fields(line, 3)
            if not parts:
                issues.append(f"运行期错误行字段数应为 3：{line!r}")
                continue
            expr, row_json, exc_name = parts
            if validate_expression(expr):
                issues.append(f"运行期错误示例应通过静态校验：{expr}")
                continue
            expected_exc = _EXC.get(exc_name)
            if expected_exc is None:
                issues.append(f"运行期错误示例的异常名无法识别：{exc_name!r}")
                continue
            try:
                apply_expression(expr, json.loads(row_json))
            except expected_exc:
                pass
            except Exception as e:  # noqa: BLE001 - 期望的是声明的那种异常
                issues.append(f"运行期错误示例异常不符：{expr} → {type(e).__name__}")
            else:
                issues.append(f"运行期错误示例未抛异常：{expr}")

    discouraged = _block(examples, "DISCOURAGED")
    if not discouraged:
        issues.append("examples.md 缺少 DISCOURAGED 标记块（或块内为空）")
    else:
        for line in discouraged:
            parts = _fields(line, 3)
            if not parts:
                issues.append(f"不合规范写法行字段数应为 3：{line!r}")
                continue
            discouraged_expr, recommended, row_json = parts
            if validate_expression(discouraged_expr) or validate_expression(recommended):
                issues.append(f"不合规范写法与其推荐写法都应能通过校验：{discouraged_expr}")
                continue
            row = json.loads(row_json)
            if apply_expression(discouraged_expr, row) != apply_expression(recommended, row):
                issues.append(f"两条写法应等价：{discouraged_expr} / {recommended}")
    return issues


def lint_skill(name: str) -> list[str]:
    """校验磁盘上的技能包；技能包不存在或名称非法时返回单条可读原因。"""
    try:
        files = load_skill_files(name)
    except ValueError as e:
        return [str(e)]
    if "SKILL.md" not in files:
        return [f"技能包不存在或缺少主文件：{name}"]
    return lint_skill_files(files)
