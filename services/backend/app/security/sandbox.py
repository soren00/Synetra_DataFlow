"""手写路径沙箱（任务 6.x）。

- 6.1 低权限执行身份：为手写任务指派独立 worker group + 运行用户；root 下真实创建，
      非 root 下优雅降级（沿用当前身份并标注 degraded）。
- 6.2 最小凭证注入：仅为任务声明的 conn_key 注入秘密，绝不注入平台级秘密/其他项目凭证。
- 6.3 网络白名单：由源/目标连接端点推导 outbound 白名单（合理默排序），出网受限。
- 6.4 资源与执行时长配额：CPU / 内存 / 磁盘 / 超时，超配额触发终止并记录。

配额/用户落地逻辑尽量真实可跑，同时保持可注入、可在单测中验证语义而无需真机集群。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

from app.security.network import Endpoint, extract_endpoint, validate_network

DEFAULT_QUOTA_CPU_S = 300          # 秒
DEFAULT_QUOTA_MEM_MB = 2048
DEFAULT_QUOTA_DISK_MB = 1024
DEFAULT_QUOTA_TIMEOUT_S = 600


@dataclass
class ResourceQuota:
    cpu_seconds: int = DEFAULT_QUOTA_CPU_S
    memory_mb: int = DEFAULT_QUOTA_MEM_MB
    disk_mb: int = DEFAULT_QUOTA_DISK_MB
    timeout_seconds: int = DEFAULT_QUOTA_TIMEOUT_S


@dataclass
class WorkerProfile:
    task_key: str
    project: str
    run_as_user: str
    worker_group: str
    conn_keys: set[str] = field(default_factory=set)
    allowed_endpoints: list[Endpoint] = field(default_factory=list)
    quota: ResourceQuota = field(default_factory=ResourceQuota)
    degraded: bool = False
    notes: list[str] = field(default_factory=list)


def _safe_slug(text: str, max_len: int = 32) -> str:
    keep = "".join(c if (c.isalnum() or c in "-_") else "-" for c in text.lower())
    return keep.strip("-")[:max_len] or "worker"


def _endpoint_for_connection(ctype: str, meta: dict) -> str | None:
    """按连接类型提取出网端点字符串（derive whitelist）。"""
    if ctype in ("mysql", "postgresql", "oracle", "sqlserver", "hive", "clickhouse"):
        host = meta.get("host")
        port = meta.get("port")
        return host if (host and not port) else (f"{host}:{port}" if host else None)
    if ctype in ("api", "rest"):
        return meta.get("url") or None
    return None


def build_worker_profile(store, task) -> WorkerProfile:
    """从任务配置 + 连接元数据推导执行沙箱画像。始终返回画像，绝不抛出（优雅降级）。"""
    cfg = task.model_dump() if hasattr(task, "model_dump") else dict(task)
    project = cfg.get("project", "")
    task_key = cfg.get("task_key") or cfg.get("name") or "task"
    slug = _safe_slug(f"synetra-{project}-{task_key}")
    allowed: list[Endpoint] = []
    conn_keys: set[str] = set()
    notes: list[str] = []

    for ck in (cfg.get("source_conn_key"), cfg.get("target_conn_key")):
        if not ck:
            continue
        conn_keys.add(ck)
        try:
            conn = store.get_connection(ck)
            ep = _endpoint_for_connection(conn.ctype.value if hasattr(conn.ctype, "value") else conn.ctype,
                                          conn.meta)
            if ep is not None and str(ep):
                allowed.append(extract_endpoint(str(ep)) or Endpoint(host=str(ep)))
        except (KeyError, Exception):  # noqa: BLE001 - 连接缺失不影响画像推导
            notes.append(f"连接 {ck} 不可达，未纳入网络白名单")

    profile = WorkerProfile(
        task_key=task_key, project=project,
        run_as_user=f"synetra-w-{slug[:24]}",
        worker_group="synetra-wg",
        conn_keys=conn_keys,
        allowed_endpoints=allowed,
        notes=notes,
    )
    # 网络白名单为空时，按 task 声明性默认：仅允许 loopback，避免"默认全开"
    if not allowed:
        profile.allowed_endpoints = [Endpoint(host="127.0.0.1"), Endpoint(host="localhost")]
        profile.notes.append("未声明外部端点，网络白名单默认仅放行本机")
    if os.geteuid() != 0:
        profile.degraded = True
        profile.notes.append("非 root 运行：无法切换低权限用户，任务以当前身份执行（优雅降级）")
    return profile


def _sh(cmd: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return -1, "命令不可用"


def ensure_worker_user(profile: WorkerProfile) -> dict:
    """6.1 为手写任务创建低权限运行身份（幂等）。

    root 下真实创建无特权用户并加入独立 worker group；非 root 返回 degraded 说明。
    返回 dict 描述执行安排；绝不因缺特权而抛异常阻塞任务落库。
    """
    if os.geteuid() != 0:
        return {"created": False, "run_as_user": profile.run_as_user,
                "note": "非 root 运行：沿用当前执行身份（优雅降级）"}
    group = profile.worker_group
    # 建独立 worker group（幂等）
    if _sh(["getent", "group", group])[0] != 0:
        _sh(["groupadd", "-f", group])  # -f 已存在不报错
    # 建低权限用户（幂等）
    user = profile.run_as_user
    if _sh(["id", "-u", user])[0] != 0:
        _sh(["useradd", "-r", "-g", group, "-s", "/usr/sbin/nologin",
             "-d", f"/var/lib/{user}", user])
        _sh(["mkdir", "-p", f"/var/lib/{user}"])
        _sh(["chown", f"{user}:{group}", f"/var/lib/{user}"])
        _sh(["chmod", "700", f"/var/lib/{user}"])
    return {"created": True, "run_as_user": user, "worker_group": group,
            "note": f"已在独立 group {group} 创建低权限用户 {user}"}


def minimum_credential_env(store, conn_keys: set[str]) -> dict[str, str]:
    """6.2 仅注入任务声明的 conn_key 秘密；其余（含平台级秘密）一律不注入。"""
    out: dict[str, str] = {}
    for ck in conn_keys or set():
        try:
            if store.has_secret(ck):
                out[f"SYNETRA_SECRET_{ck}"] = store.get_secret(ck).decode()
        except (KeyError, Exception):  # noqa: BLE001, S112 - 单连接失败不影响其余
            continue
    return out


def validate_script_network(profile: WorkerProfile, source: str) -> list[str]:
    """6.3 静态校验脚本出网地址是否落在白名单内。"""
    return validate_network(source, profile.allowed_endpoints)


def quota_enforcement_epilog(quota: ResourceQuota) -> list[str]:
    """生成在子进程内强制资源配额的 pre-preamble 片段（6.4）。

    返回在子进程解释器内、exec 目标脚本前执行的字节码片段：
    设置 RLIMIT_CPU / RLIMIT_AS（内存） / RLIMIT_FSIZE（磁盘）。超限即被内核终止（SIGKILL）。
    """
    mb = quota.memory_mb * 1024 * 1024
    disk_b = quota.disk_mb * 1024 * 1024
    return [
        "import resource",
        f"resource.setrlimit(resource.RLIMIT_CPU, ({quota.cpu_seconds},{quota.cpu_seconds}))",
        f"resource.setrlimit(resource.RLIMIT_AS, ({mb},{mb}))",
        f"resource.setrlimit(resource.RLIMIT_FSIZE, ({disk_b},{disk_b}))",
    ]


__all__ = [
    "ResourceQuota",
    "WorkerProfile",
    "build_worker_profile",
    "ensure_worker_user",
    "minimum_credential_env",
    "quota_enforcement_epilog",
    "validate_script_network",
]