"""网络白名单（任务 6.3）。

按任务声明的源/目标端点限制出网：解析端点 host:port，白名单内可通，白名单外加盖拒绝。
同时提供对脚本源码的静态扫描：检出脚本中硬编码、不在白名单内的出网地址。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

_URL_RE = re.compile(r"(?:https?|ftp)://([^\s\"'<>()|]+)", re.IGNORECASE)


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int | None = None

    def __str__(self) -> str:
        return self.host if self.port is None else f"{self.host}:{self.port}"


_EXPLICIT_PORT = re.compile(r"^(?P<host>.+):(?P<port>\d+)$")


def extract_endpoint(url: str) -> Endpoint | None:
    """从 URL 提取 host[:port]。无法识别返回 None。"""
    if not url:
        return None
    # 去掉 scheme 与 path
    for scheme in ("http://", "https://", "ftp://"):
        if url.lower().startswith(scheme):
            url = url[len(scheme):]
            break
    url = url.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if not url:
        return None
    m = _EXPLICIT_PORT.match(url)
    if m:
        if not m.group("port").isdigit():
            return None
        return Endpoint(host=m.group("host"), port=int(m.group("port")))
    return Endpoint(host=url)


def parse_whitelist(urls: list[str]) -> list[Endpoint]:
    """把一组 URL/host 字符串解析为白名单端点（去重、去空）。"""
    out: list[Endpoint] = []
    seen: set[tuple[str, int | None]] = set()
    for u in urls or []:
        ep = extract_endpoint(u) or (Endpoint(host=u) if u else None)
        if ep is None:
            continue
        key = (ep.host, ep.port)
        if key not in seen:
            seen.add(key)
            out.append(ep)
    return out


def _host_match(rule_host: str, actual_host: str) -> bool:
    r = rule_host.lower().strip("*").strip(".")
    a = actual_host.lower()
    if r == a:
        return True
    if a.endswith("." + r):
        return True  # 规则为父域，子域放行
    try:
        return ipaddress.ip_address(a) == ipaddress.ip_address(r)
    except ValueError:
        return False


def allowed(host: str, port: int | None, whitelist: list[Endpoint]) -> bool:
    """判断目标 host:port 是否命中白名单。port 未声明时按任意端口放行对应主机。"""
    for rule in whitelist:
        if not _host_match(rule.host, host):
            continue
        if rule.port is None or port is None or rule.port == port:
            return True
    return False


def scan_endpoints(source: str) -> list[Endpoint]:
    """静态扫描脚本源码中出现的所有 URL 端点。"""
    found = [extract_endpoint(u) for u in _URL_RE.findall(source or "")]
    return [ep for ep in found if ep is not None]


def validate_network(source: str, whitelist: list[Endpoint]) -> list[str]:
    """扫描脚本出网地址并对照白名单：返回不允许访问的违规说明列表。"""
    violations: list[str] = []
    for ep in scan_endpoints(source):
        if not allowed(ep.host, ep.port, whitelist):
            violations.append(
                f"访问 {ep} 不在网络白名单内（允许: "
                + (", ".join(str(e) for e in whitelist) or "无"))
    return violations


__all__ = [
    "Endpoint",
    "allowed",
    "extract_endpoint",
    "parse_whitelist",
    "scan_endpoints",
    "validate_network",
]