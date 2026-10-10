"""权限多租户与外部 API 凭据模型（任务 7）。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Role(str, Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    READONLY = "readonly"


class UserOut(BaseModel):
    username: str
    role: Role


class ServiceCredential(BaseModel):
    cred_id: str
    project: str
    label: str
    created_at: str
    revoked: bool = False
    # token_hash 只存哈希；明文仅在签发时返回一次
    token_hash: str = ""


class IssueCredentialResponse(BaseModel):
    cred_id: str
    project: str
    label: str
    token: str  # 明文仅此一次机会展示
    revoked: bool = False


class TriggerReceipt(BaseModel):
    trigger_id: str
    task_key: str
    project: str
    fired_at: str
    run_id: str = ""  # 8.2：触发同步建立的运行记录 ID，执行结果走监控查询