"""etl_sdk：真实抽取 / 加载运行时（任务 11.1 / 14.x）。

与 dry-run 的 `etl_runtime` 保持同一契约（readers / writers），但面向真实连接执行：

- 关系库（任务 11.1）：PostgreSQL / Oracle / SQL Server 的全量、增量+水位、主键 upsert。
- 多源（任务 14.1/14.2）：CSV extractor、REST/API extractor（认证/分页/参数）。
- 多目标（任务 14.3/14.4）：API loader（推送、超时/重试，幂等由端点保证）、
  对象存储 / Hive / ClickHouse loader。

所有连接器遵循统一注册表（driver 缺失时返回清晰原因，绝不抛未定义异常）；
可在单测中以假连接注入验证取数/落数语义，无需真机集群。
"""

from __future__ import annotations

from . import (
    loaders,  # noqa: F401
    relational,  # noqa: F401
)
from .loaders import load
from .relational import extract

__all__ = ["extract", "load"]