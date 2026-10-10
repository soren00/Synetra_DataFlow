"""供应商无关的统一 LLM 接口（任务 5.1）。

`LLMProvider` 是唯一抽象：对话/补全 + 模型列表。具体供应商（DeepSeek、未来其它）通过
注册表插拔，切换供应商不改变既有任务配置与已生成脚本（二者与供应商隔离存储）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol


class ProviderNotConfigured(Exception):
    """LLM 凭据缺失/未配置；上层应返回 503 与配置引导文案。"""


class ProviderAuthError(Exception):
    """凭据无效等鉴权类失败；上层应返回带原因的失败结果。"""


class ProviderRequestError(Exception):
    """请求层失败（HTTP 4xx/5xx、网络错误）；重试无意义，上层应透出可读原因。"""


class ModelInfo(Protocol):
    """模型元信息（可序列化）。"""

    id: str
    family: str
    capabilities: list[str]


class _ModelInfoImpl:
    def __init__(self, id: str, family: str, capabilities: list[str]) -> None:
        self.id = id
        self.family = family
        self.capabilities = capabilities

    def model_dump(self) -> dict:
        return {"id": self.id, "family": self.family, "capabilities": self.capabilities}

    def __repr__(self) -> str:
        return f"ModelInfo({self.id!r})"


def model_info(id: str, family: str, capabilities: list[str] | None = None) -> ModelInfo:
    return _ModelInfoImpl(id, family, capabilities or ["chat"])


# ---- 供应商注册表（可插拔） ----

_PROVIDERS: dict[str, LLMProvider] = {}


def register_provider(name: str, provider: LLMProvider) -> None:
    """注册一个带名字的供应商实例；同名覆盖（实现热切换）。"""
    _PROVIDERS[name] = provider


def get_provider(name: str) -> LLMProvider:
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise KeyError(f"LLM provider 未注册: {name}")


def list_providers() -> list[str]:
    return list(_PROVIDERS)


class LLMProvider(ABC):
    """统一供应商抽象：多轮对话 与 模型列表。"""

    name: str = "base"
    model_name: str = ""  # 审计用：当前生效模型名

    @abstractmethod
    def converse(self, messages: list[dict[str, str]], *, system: str | None = None) -> str:
        """多轮对话入口：messages 为 [{role, content}] 的轮次列表（可含会话历史）。

        `system` 为会话级系统段落，由调用方组装；返回本轮助手文本输出。
        """

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        """单轮补全便捷入口：等价于只含一条 user 消息的多轮调用。"""
        return self.converse([{"role": "user", "content": prompt}], system=system)

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """返回该供应商可用模型列表。"""