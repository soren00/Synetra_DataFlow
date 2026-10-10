"""LLM 接入层（任务 5.1/5.2）。供应商无关抽象 + DeepSeek 默认实现 + 可插拔注册。"""

from .provider import LLMProvider, ModelInfo  # noqa: F401