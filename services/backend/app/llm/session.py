"""多轮生成会话的模型可见历史：契约、校验、追加与裁剪（决策 1/3）。

契约（服务端唯一产出方，客户端只做 opaque 回传）：
- 元素为 `{"role": "user" | "assistant", "content": str}`，按发生顺序排列；
- 首条必须是 `user`（否则视为轮次顺序错乱）；
- **不接受 `system`**：会话级系统段落（含表达示技能包）每轮由服务端重新组装，
  客户端不得注入，因此 `system` 属于非法角色；
- 超限（轮次 / 字符双限）时从最早的非会话级轮次开始裁剪，会话级 `system` 因不在
  历史中而永不被裁剪。

服务端不保存跨请求状态：历史由响应返回、由客户端在下一次请求原样回传。
"""

from __future__ import annotations

import hashlib

# 客户端可回传的角色白名单（system 由服务端重建，故不在其中）
ALLOWED_ROLES: tuple[str, ...] = ("user", "assistant")

# 历史长度双限（决策 3）
MAX_HISTORY_TURNS = 20
MAX_HISTORY_CHARS = 60_000


class InvalidHistory(ValueError):
    """回传的会话历史不满足契约（角色非法 / 结构非法 / 轮次顺序错乱）。"""


def validate_history(raw: object) -> list[dict[str, str]]:
    """校验并归一化客户端回传的会话历史；不合法时抛 InvalidHistory。"""
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list):
        raise InvalidHistory("会话历史必须是消息数组")
    out: list[dict[str, str]] = []
    for idx, item in enumerate(raw):
        pos = idx + 1
        if not isinstance(item, dict):
            raise InvalidHistory(f"第 {pos} 条会话历史结构非法：应为对象")
        role = item.get("role")
        content = item.get("content")
        if role not in ALLOWED_ROLES:
            raise InvalidHistory(
                f"第 {pos} 条会话历史角色非法：{role!r}（仅允许 user/assistant，"
                "系统段落由服务端重建）")
        if not isinstance(content, str):
            raise InvalidHistory(f"第 {pos} 条会话历史内容非法：应为字符串")
        out.append({"role": role, "content": content})
    if out and out[0]["role"] != "user":
        raise InvalidHistory("会话历史轮次顺序错乱：首条必须是 user")
    return out


def append_turn(history: list[dict[str, str]], role: str, content: str) -> list[dict[str, str]]:
    """在历史末尾追加一轮，返回新列表（不修改入参）。"""
    if role not in ALLOWED_ROLES:
        raise InvalidHistory(f"不可追加的角色：{role!r}")
    return [*history, {"role": role, "content": content}]


def trim_history(history: list[dict[str, str]], *,
                 max_turns: int = MAX_HISTORY_TURNS,
                 max_chars: int = MAX_HISTORY_CHARS) -> tuple[list[dict[str, str]], bool]:
    """按轮次与字符双限裁剪历史：从最早开始丢弃；返回 (历史, 是否被裁剪)。

    裁剪后若首条为 assistant（其对应的 user 轮已被丢弃），一并丢弃以保证
    历史仍以 user 开头。
    """
    kept = list(history)
    trimmed = False
    if max_turns > 0 and len(kept) > max_turns:
        kept = kept[len(kept) - max_turns:]
        trimmed = True
    total = sum(len(m.get("content") or "") for m in kept)
    while kept and max_chars > 0 and total > max_chars:
        dropped = kept.pop(0)
        total -= len(dropped.get("content") or "")
        trimmed = True
    if kept and kept[0]["role"] != "user":
        kept.pop(0)
        trimmed = True
    return kept, trimmed


def conversation_id(history: list[dict[str, str]]) -> str:
    """由会话历史派生的稳定会话标识（审计用；同前缀历史视为同一会话）。"""
    first = history[0]["content"] if history else ""
    return hashlib.sha256(first.encode("utf-8")).hexdigest()[:16]
