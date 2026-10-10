"""DeepSeek 供应商实现（任务 5.1），OpenAI 兼容 chat/completions。

仅由 DL 层按需调用；测试用 RecordingProvider 捕获请求，不发起真实网络请求。
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.llm.provider import (
    LLMProvider,
    ModelInfo,
    ProviderAuthError,
    ProviderNotConfigured,
    ProviderRequestError,
    model_info,
)


class DeepSeekProvider(LLMProvider):
    name = "deepseek"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self.model_name = model

    def converse(self, messages: list[dict[str, str]], *, system: str | None = None) -> str:
        if not self._api_key:
            raise ProviderNotConfigured(
                "LLM 未配置 API Key，请先在「模型配置」中保存 API Key，或配置环境变量 SYNETRA_DEEPSEEK_API_KEY")
        turns: list[dict] = []
        if system:
            turns.append({"role": "system", "content": system})
        # 会话历史原样下发（多轮）：不再固定 [system?, user] 两条
        turns.extend({"role": str(m.get("role", "user")),
                      "content": str(m.get("content", ""))} for m in messages)
        payload: dict = {
            "model": self._model,
            "messages": turns,
            "temperature": 0.2,
        }
        try:
            resp = httpx.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise ProviderRequestError(f"调用模型接口失败：{e}") from e
        if resp.status_code >= 400:
            raise ProviderRequestError(f"请求被拒绝（HTTP {resp.status_code}）：{_err_detail(resp)}")
        data = resp.json()
        return str(data["choices"][0]["message"]["content"])

    def list_models(self) -> list[ModelInfo]:
        """调用供应商模型列表接口验证凭据有效（连通性测试）；失败抛异常由上层转可读原因。"""
        if not self._api_key:
            raise ProviderNotConfigured("LLM 未配置 API Key，请先保存配置")
        resp = httpx.get(
            f"{self._base_url}/models",
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
        )
        if resp.status_code in (401, 403):
            raise ProviderAuthError(f"API Key 无效（HTTP {resp.status_code}），请检查后重新保存")
        resp.raise_for_status()
        data = resp.json()
        models = [
            model_info(m["id"], "deepseek", ["chat"])
            for m in (data.get("data") or []) if m.get("id")
        ]
        return models or [
            model_info("deepseek-chat", "deepseek", ["chat"]),
            model_info("deepseek-reasoner", "deepseek", ["chat", "reasoning"]),
        ]


def _err_detail(resp: httpx.Response) -> str:
    """从供应商错误响应中提取可读原因（OpenAI 兼容 error.message）。"""
    try:
        body = resp.json()
        err = body.get("error") or {}
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str) and err:
            return err
    except ValueError:
        pass
    return (resp.text or "").strip()[:200] or resp.reason_phrase


def auto_register(settings: Settings) -> None:
    """启动注册入口：生效配置 = 存储值优先、env 兜底（决策 1）。"""
    from app.llm.config import apply_effective
    from app.store import Store

    apply_effective(Store(settings.data_dir), settings)