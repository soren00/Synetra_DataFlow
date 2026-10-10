"""连接管理数据模型：结构化元数据与 conn_key 引用，不含明文凭证。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_serializer


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
    meta: dict[str, str] = Field(default_factory=dict)
    secret: str | None = None
    created_by: str = "system"


class ConnectionOut(BaseModel):
    """对外返回的连接信息：只含元数据与 conn_key，绝不包含明/密秘密。"""

    conn_key: str
    project: str
    name: str
    ctype: ConnectionType
    direction: ConnectionDirection
    meta: dict[str, str]
    created_by: str
    created_at: str
    updated_at: str

    @field_serializer("ctype")
    def _ctype(self, v: ConnectionType) -> str:
        return v.value

    @field_serializer("direction")
    def _direction(self, v: ConnectionDirection) -> str:
        return v.value