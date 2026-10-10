"""LLM 配置：Vault 密文存取、掩码展示、生效配置解析与热切换（决策 1）。

生效规则 = 存储值优先，`Settings.deepseek_*` 环境变量兜底；api_key 密文经
`SecretVault` 解密后参与注册，查询接口只返回掩码（尾 4 位），明文仅在保存时一次可见。
"""

from __future__ import annotations

from app.core.config import Settings
from app.store import Store


def mask_api_key(key: str) -> str:
    """掩码：保留尾 4 位，其余以 * 替代；过短时整体打码。"""
    if not key:
        return ""
    if len(key) <= 4:
        return "*" * len(key)
    return "*" * (len(key) - 4) + key[-4:]


def stored_llm_config(store: Store) -> dict:
    """存储中的配置快照；api_key 仅以掩码展示，绝不返回明文。"""
    row = store.get_llm_config_row()
    if not row:
        return {
            "provider": "", "base_url": "", "model": "",
            "api_key_masked": "", "has_api_key": False,
            "updated_at": "", "updated_by": "",
        }
    cipher = row.get("api_key_cipher") or ""
    has_key = bool(cipher)
    masked = mask_api_key(store.vault.decrypt(cipher).decode()) if has_key else ""
    return {
        "provider": row.get("provider") or "",
        "base_url": row.get("base_url") or "",
        "model": row.get("model") or "",
        "api_key_masked": masked,
        "has_api_key": has_key,
        "updated_at": row.get("updated_at", ""),
        "updated_by": row.get("updated_by", ""),
    }


def effective_llm_config(store: Store, settings: Settings) -> dict:
    """生效配置 = 存储值优先，env 兜底；api_key 为明文，仅供内部注册/测试使用。"""
    row = store.get_llm_config_row()
    api_key = ""
    if row and row.get("api_key_cipher"):
        api_key = store.vault.decrypt(row["api_key_cipher"]).decode()
    return {
        "provider": (row or {}).get("provider") or settings.llm_provider,
        "base_url": (row or {}).get("base_url") or settings.deepseek_base_url,
        "model": (row or {}).get("model") or settings.deepseek_model,
        "api_key": api_key or settings.deepseek_api_key,
        "has_api_key": bool(api_key or settings.deepseek_api_key),
    }


def apply_effective(store: Store, settings: Settings) -> str:
    """按生效配置注册默认供应商（启动/保存/吊销后共用入口，实现热切换）；返回 provider 名。"""
    from app.llm.deepseek import DeepSeekProvider
    from app.llm.provider import register_provider

    eff = effective_llm_config(store, settings)
    register_provider(eff["provider"], DeepSeekProvider(
        api_key=eff["api_key"],
        base_url=eff["base_url"],
        model=eff["model"],
    ))
    return eff["provider"]
