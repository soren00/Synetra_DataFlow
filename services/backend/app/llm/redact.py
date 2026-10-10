"""LLM 调用前配置脱敏（任务 5.2）。

确保发往 LLM 的请求不包含明文凭证/口令：把配置里任何可能携带秘密的字段（如同连接串中
`user:pass@host`）替换为占位符；生成脚本中的占位符在保存前被还原为运行时引用（环境变量）。
"""

from __future__ import annotations

import re

_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "access_key", "secret_key", "secretkey",
})

# 连接串 `scheme://user:pass@host` 明文口令
_DSN_PASSWORD = re.compile(r"://([^:@/]+):([^@/]+)@")

# 占位符模板：{{SECRET:<id>}}
_PLACEHOLDER_RE = re.compile(r"\{\{SECRET:([A-Za-z0-9_]+)\}\}")


def _is_sensitive_key(key: str) -> bool:
    k = key.lower().replace("-", "_")
    return k in _SENSITIVE_KEYS or k.endswith(("_secret", "_password"))


def redact_value(value: object, key: str, secrets: dict[str, str], counter: list[int]) -> object:
    """把单个字段值中的秘密替换为占位符，映射记入 secrets（值->id）。"""
    if isinstance(value, str):
        if _is_sensitive_key(key) and value:
            return _placeholder_for(value, secrets, counter)
        text = _DSN_PASSWORD.sub(
            lambda m: f"://{m.group(1)}:{_placeholder_for(m.group(2), secrets, counter)}@",
            value,
        )
        if text != value:
            return text
    return value


def _placeholder_for(value: str, secrets: dict[str, str], counter: list[int]) -> str:
    """同一明文值映射到同一占位符 id（保留引用对应关系）。"""
    if value in secrets:
        return f"{{{{SECRET:{secrets[value]}}}}}"
    counter[0] += 1
    sid = f"s{counter[0]}"
    secrets[value] = sid
    return f"{{{{SECRET:{sid}}}}}"


def redact_object(obj: object, _secret_map: dict[str, str] | None = None) -> tuple[object, dict[str, str]]:
    """递归脱敏 config；返回 (脱敏后的对象, secrets 明文值->占位符id 的映射)。"""
    secrets: dict[str, str] = {}
    counter = [0]

    def walk(node: object, key: str = "") -> object:
        if isinstance(node, dict):
            return {k: walk(v, str(k)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            return redact_value(node, key, secrets, counter)
        return node

    return walk(obj), secrets


def restore_placeholder(text: str) -> str:
    """占位符整体替换为"保留 id 的单向引用"（不携带明文）。"""
    return _PLACEHOLDER_RE.sub(r"${{SECRET_\1}}", text)


def redact_text(text: str, _secret_map: dict[str, str] | None = None) -> str:
    """对自由文本（prompt/脚本）剔除 DSN 明文口令与占位符直引。"""
    out = _DSN_PASSWORD.sub(lambda m: f"://{m.group(1)}:***@", text)
    return _PLACEHOLDER_RE.sub(r"${{SECRET_\1}}", out)