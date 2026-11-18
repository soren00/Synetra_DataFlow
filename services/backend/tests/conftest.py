"""测试基线配置。

单元测试一律使用「独立临时目录 + SQLite」的元数据库，以保证用例隔离、可离线运行，
不依赖外部 MySQL（默认配置已指向 MySQL）。

环境变量优先级高于 .env 与代码默认值，故这里显式置空 DSN 即强制回退 SQLite；
必须早于 app.core.config 的首次导入执行，因此放在 conftest 顶层。
"""

from __future__ import annotations

import os

import pytest

os.environ["SYNETRA_METADATA_DB_DSN"] = ""


@pytest.fixture(autouse=True)
def default_admin_identity():
    """默认以管理员身份调用接口，避免历史用例逐条登录。

    只替换「身份解析」这一步（`current_user` 依赖），`require_role` 的角色判定、
    审计与业务逻辑仍真实执行，因此 403 语义依然被覆盖。真实登录/验签链路由
    `tests/test_auth_token.py` 与 `tests/test_access.py` 断言；需要切换身份或断言
    匿名行为的用例调用 `tests/authhelpers.act_as()` / `clear_identity()`。
    """
    from app.auth import AuthContext, current_user
    from app.main import app
    from app.models.access import Role

    app.dependency_overrides[current_user] = lambda: AuthContext(
        username="admin", role=Role.ADMIN)
    try:
        yield
    finally:
        app.dependency_overrides.pop(current_user, None)
