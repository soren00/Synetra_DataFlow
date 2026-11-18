"""测试鉴权助手：把客户端切到指定账号身份。

`tests/conftest.py` 的 autouse 夹具默认以管理员身份调用（只替换身份解析，
`require_role` 的角色判定仍真实执行）。需要切换身份的用例调用 `act_as()`，
它会移除该覆盖并挂上真实签名的会话票据——此后每个请求都走真实验签路径。

登录链路本身（口令校验、停用拒登、改密失效）由 `test_auth_token.py` 与
`test_access.py` 的真实 `/api/auth/login` 断言覆盖，这里不重复。
"""

from __future__ import annotations

TEST_PASSWORD = "synetra-test-pw"


def act_as(client, username: str, password: str | None = None) -> None:
    """以 `username` 身份调用后续接口。

    提供 `password` 时走真实登录换票据；未提供时直接为该账号签发会话票据，
    避免测试为了造身份而覆盖被测账号的口令。
    """
    from starlette.testclient import TestClient

    from app.auth import current_user, issue_session_token
    from app.core.config import get_settings
    from app.main import app
    from app.store import Store

    store = Store(get_settings().data_dir)
    if store.get_user(username) is None:
        raise AssertionError(f"测试账号 {username} 不存在，请先在测试前置中创建")

    app.dependency_overrides.pop(current_user, None)

    if password:
        resp = TestClient(app).post(
            "/api/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200, f"{username} 登录失败：{resp.status_code} {resp.text}"
        token = resp.json()["token"]
    else:
        token, _ = issue_session_token(username, store)

    client.headers["Authorization"] = f"Bearer {token}"


def clear_identity(client) -> None:
    """移除客户端票据，回到匿名状态（断言 401 用）。"""
    client.headers.pop("Authorization", None)
