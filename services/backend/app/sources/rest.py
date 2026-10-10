"""REST/API 数据源：发起请求并返回样例数据。

任务 2.5：API 源可发起请求并返回样例数据。
认证：none / bearer(token 存 secret) / basic(user:secret)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx


@dataclass
class ApiAuth:
    scheme: str = "none"  # none | bearer | basic
    token: str | None = None
    username: str | None = None
    password: str | None = None


def _auth(method: str, url: str, extra_headers: dict[str, str] | None,
          api_auth: ApiAuth | None) -> tuple[str, dict[str, str]]:
    headers = dict(extra_headers or {})
    if api_auth:
        if api_auth.scheme == "bearer" and api_auth.token:
            headers["Authorization"] = f"Bearer {api_auth.token}"
        elif api_auth.scheme == "basic":
            import base64

            raw = f"{api_auth.username or ''}:{api_auth.password or ''}"
            headers["Authorization"] = "Basic " + base64.b64encode(raw.encode()).decode()
    return method, headers


def fetch_sample(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    secret: str | None = None,
    auth_scheme: str = "none",
    limit: int = 5,
    timeout: float = 10.0,
) -> dict:
    """发起请求并抽取前若干条样例记录。"""
    api_auth = None
    if auth_scheme == "bearer":
        api_auth = ApiAuth(scheme="bearer", token=secret)
    elif auth_scheme == "basic":
        api_auth = ApiAuth(scheme="basic", username=params and params.pop("user", "") or "", password=secret)

    m, hdrs = _auth(method, url, headers, api_auth)
    with httpx.Client(timeout=timeout) as client:
        resp = client.request(m, url, headers=hdrs, params=params)
        resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        return {"ok": True, "status": resp.status_code, "content_type": resp.headers.get("content-type")}

    rows = data if isinstance(data, list) else [data]
    return {"ok": True, "status": resp.status_code, "sample": rows[:limit], "total": len(data) if isinstance(data, list) else 1}


def request_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: dict | None = None,
    json_body: dict | list | None = None,
    params: dict[str, str] | None = None,
    secret: str | None = None,
    auth_scheme: str = "none",
    timeout: float = 30.0,
) -> dict:
    """发送请求（支持 JSON body，供 API loader POST 使用）；返回解析后的 JSON 响应。"""
    api_auth = None
    if auth_scheme == "bearer":
        api_auth = ApiAuth(scheme="bearer", token=secret)
    elif auth_scheme == "basic":
        api_auth = ApiAuth(scheme="basic", username=params and params.pop("user", "") or "", password=secret)
    m, hdrs = _auth(method, url, headers, api_auth)
    with httpx.Client(timeout=timeout) as client:
        resp = client.request(m, url, headers=hdrs, params=params,
                              content=json.dumps(json_body if json_body is not None else body).encode() if (json_body is not None or body is not None) else None)
        resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return {"ok": True, "status": resp.status_code}


__all__ = ["ApiAuth", "fetch_sample", "request_json"]