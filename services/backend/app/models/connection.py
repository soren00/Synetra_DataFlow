"""连接管理数据模型：结构化元数据与 conn_key 引用，不含明文凭证。"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_serializer, field_validator

# meta 允许嵌套一层参数结构（如 api 连接的 params/headers），但不能成为任意结构倾倒场
META_MAX_BYTES = 8192


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _check_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """校验连接元数据：键为字符串，值为标量 / 一层字符串键值对 / 标量列表。"""
    for key, value in meta.items():
        if not isinstance(key, str):
            raise ValueError("meta 的键必须是字符串")
        if _is_scalar(value):
            continue
        if isinstance(value, dict):
            if not all(isinstance(k, str) and _is_scalar(v) for k, v in value.items()):
                raise ValueError(f"meta.{key} 只允许一层字符串键值对")
            continue
        if isinstance(value, list):
            if not all(_is_scalar(v) for v in value):
                raise ValueError(f"meta.{key} 的列表元素只允许标量")
            continue
        raise ValueError(f"meta.{key} 的类型不受支持")
    if len(json.dumps(meta, ensure_ascii=False).encode("utf-8")) > META_MAX_BYTES:
        raise ValueError(f"meta 序列化后不得超过 {META_MAX_BYTES} 字节")
    return meta


class ConnectionType(str, Enum):
    """数据源/目标类型。"""

    MYSQL = "mysql"
    ORACLE = "oracle"
    SQLSERVER = "sqlserver"
    POSTGRESQL = "postgresql"
    CSV = "csv"
    REST = "rest"
    API = "api"
    OBJECT_STORAGE = "object_storage"
    HIVE = "hive"
    CLICKHOUSE = "clickhouse"
    # 目标关系库与源关系库共用同类型枚举


class ConnectionDirection(str, Enum):
    """连接用途：作为数据源读取，或作为数据目标写入。"""

    SOURCE = "source"
    TARGET = "target"
    BOTH = "both"  # 双方：既可作数据源又可作数据目标


class ConnectionCreate(BaseModel):
    """创建连接的请求体。

    `secret` 为明文字段，仅在创建/连通性测试时使用，
    落库时必须经 Vault 加密为密文并以 conn_key 引用，禁止明文持久化。
    """

    project: str
    name: str
    ctype: ConnectionType
    direction: ConnectionDirection = ConnectionDirection.SOURCE
    # 支持嵌套一层参数结构（api 连接的 params/headers），见 _check_meta
    meta: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = None
    created_by: str = "system"

    @field_validator("meta")
    @classmethod
    def _validate_meta(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_meta(value)


class ConnectionOut(BaseModel):
    """对外返回的连接信息：只含元数据与 conn_key，绝不包含明/密秘密。"""

    conn_key: str
    project: str
    name: str
    ctype: ConnectionType
    direction: ConnectionDirection
    meta: dict[str, Any]
    created_by: str
    created_at: str
    updated_at: str

    @field_serializer("ctype")
    def _ctype(self, v: ConnectionType) -> str:
        return v.value

    @field_serializer("direction")
    def _direction(self, v: ConnectionDirection) -> str:
        return v.value