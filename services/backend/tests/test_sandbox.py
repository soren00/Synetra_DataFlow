"""任务 6.x：手写路径安全模型（低权限身份/最小凭证/网络白名单/资源配额）。"""

from __future__ import annotations

import tempfile

from app.models.connection import ConnectionCreate, ConnectionType
from app.models.task_config import TaskConfig
from app.security.network import allowed, extract_endpoint, parse_whitelist, validate_network
from app.security.sandbox import (
    ResourceQuota,
    build_worker_profile,
    minimum_credential_env,
    quota_enforcement_epilog,
    validate_script_network,
)
from app.store import Store
from app.vault import SecretVault

# 保持临时数据目录存活，避免被 GC 提前删除导致 store 无法打开 db
_KEEP: list = []


def _store_with_task():
    tmp = tempfile.TemporaryDirectory()
    _KEEP.append(tmp)
    store = Store(tmp.name, SecretVault(tmp.name))
    src = store.create_connection(ConnectionCreate(
        project="p1", name="src", ctype=ConnectionType.MYSQL,
        meta={"host": "db.internal", "port": "3306"}, secret="srcpass", created_by="t"))
    tgt = store.create_connection(ConnectionCreate(
        project="p1", name="tgt", ctype=ConnectionType.API,
        meta={"url": "https://api.internal/v1/ingest"}, secret="tok", created_by="t"))

    task = TaskConfig(project="p1", name="job", source_conn_key=src.conn_key,
                      target_conn_key=tgt.conn_key, schedule="0 1 * * *")
    return store, task


def test_network_whitelist_parse_and_match():
    wl = parse_whitelist(["https://api.internal/v1", "db.internal:3306"])
    assert allowed("api.internal", 443, wl)
    assert allowed("db.internal", 3306, wl)
    # 白名单外主机不放行
    assert not allowed("evil.example.com", 443, wl)
    # 端口不符不放行
    assert not allowed("db.internal", 3307, wl)
    # 子域放行
    assert allowed("a.db.internal", 3306, wl)


def test_extract_endpoint_parsing():
    assert str(extract_endpoint("https://h:8080/x")) == "h:8080"
    assert str(extract_endpoint("http://plain/path")) == "plain"


def test_validate_network_reports_out_of_whitelist():
    wl = [extract_endpoint("https://ok.internal")]  # type: ignore
    src = "import urllib.request\nurllib.request.urlopen('http://evil:9999/x')"
    viol = validate_network(src, wl)
    assert any("evil:9999" in v for v in viol)


def test_sandbox_profile_derives_endpoints_and_degradation():
    store, task = _store_with_task()
    p = build_worker_profile(store, task)
    # 最小凭证仅含任务声明连接
    assert p.conn_keys == {task.source_conn_key, task.target_conn_key}
    addrs = {str(e) for e in p.allowed_endpoints}
    assert "db.internal:3306" in addrs and "api.internal" in addrs
    # 配额默认值
    assert p.quota.cpu_seconds > 0 and p.quota.timeout_seconds > 0


def test_minimum_credential_env_only_declared():
    store, task = _store_with_task()
    env = minimum_credential_env(store, {task.source_conn_key, task.target_conn_key})
    assert env.get(f"SYNETRA_SECRET_{task.source_conn_key}") == "srcpass"
    # 未声明的连接秘密不应出现
    other = store.create_connection(ConnectionCreate(
        project="p2", name="other", ctype=ConnectionType.MYSQL,
        meta={"host": "x"}, secret="others-secret", created_by="t"))
    assert f"SYNETRA_SECRET_{other.conn_key}" not in env
    assert "others-secret" not in str(env)
    _KEEP.pop()


def test_sandbox_script_blocked_for_out_of_whitelist():
    store, task = _store_with_task()
    p = build_worker_profile(store, task)
    src = "import urllib.request\nurllib.request.urlopen('http://evil:9999/x')"
    assert validate_script_network(p, src)  # 越网 → 违规


def test_quota_enforcement_emits_rlimit_guards():
    steps = quota_enforcement_epilog(ResourceQuota(
        cpu_seconds=10, memory_mb=512, disk_mb=64, timeout_seconds=60))
    text = "\n".join(steps)
    assert "RLIMIT_CPU" in text and "RLIMIT_AS" in text and "RLIMIT_FSIZE" in text