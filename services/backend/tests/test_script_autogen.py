"""任务：保存配置即自动生成可运行执行脚本。

验证 render_config_script 的确定性渲染，以及它生成脚本能被 dry_run_script 执行、
total_records 正确；并覆盖保存接口自动写入 current/ 的行为。
"""

from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.task_config import (
    FieldRule,
    IncrementalConfig,
    InjectMode,
    TaskConfig,
    TransformKind,
)
from app.scripting.executor import dry_run_script
from app.scripting.gen_config import render_config_script
from app.scripts import ScriptStore


def _cfg() -> TaskConfig:
    return TaskConfig(
        project="proj-autogen", name="sync-orders",
        source_conn_key="SRC_PG", target_conn_key="TGT_PG",
        source_object="public.orders", target_object="ods.orders_dw",
        mode=InjectMode.UPSERT,
        primary_keys=["id"],
        incremental=IncrementalConfig(field="updated_at", watermark_field="max_updated_at"),
        fields=[
            # 直接映射
            FieldRule(source="id", target="id", kind=TransformKind.DIRECT),
            # 常量
            FieldRule(source="", target="batch", kind=TransformKind.CONSTANT, constant_value="nightly"),
            # 空值填充 + 类型转换
            FieldRule(source="name", target="customer_name", kind=TransformKind.NULL_FILL,
                      null_default="N/A", cast_to="str"),
            # 类型转换
            FieldRule(source="amount", target="amount", kind=TransformKind.CAST, cast_to="float"),
            # 白名单表达式
            FieldRule(source="name", target="name_upper", kind=TransformKind.EXPRESSION,
                      expression="upper(name)"),
        ],
        schedule="0 2 * * *",
    )


def test_render_script_is_deterministic_and_contains_connections():
    s1 = render_config_script(_cfg())
    s2 = render_config_script(_cfg())
    assert s1 == s2  # 纯确定性：同一配置 → 同一脚本
    # 断言含源/目标 conn_key 与目标对象
    assert "SRC_PG" in s1 and "TGT_PG" in s1
    assert "ods.orders_dw" in s1 and "public.orders" in s1
    # 断言含 upsert 主键与增量水位语义
    assert "'upsert'" in s1 and "'id'" in s1
    assert "updated_at" in s1 and "max_updated_at" in s1
    # 断言转换规则被内联
    assert "'null_fill'" in s1 and "'expression'" in s1 and "upper" in s1


def test_render_script_runs_under_dry_run():
    """生成的脚本可直接被 dry_run_script 执行，且 total_records 正确。"""
    script = render_config_script(_cfg())
    sample = [
        {"id": 1, "name": "alice", "amount": "12.5"},
        {"id": 2, "name": "bob", "amount": "3.0"},
        {"id": 3, "name": None, "amount": "0.0"},
    ]
    res = dry_run_script(script, _cfg().model_dump(), sample=sample)
    assert res.ok, f"dry-run 失败: {res.stderr}"
    # 每行映射为 1 行目标行 → 计数 = 样例行数；不接触真实源/目标
    assert res.total_records() == len(sample)


def test_save_task_auto_writes_current_script():
    """保存配置后应自动生成脚本写入 current/，使弹出"尚无已确认脚本"不再出现。"""
    tmp = tempfile.TemporaryDirectory()
    os.environ["SYNETRA_DATA_DIR"] = tmp.name
    os.environ["SYNETRA_SCRIPTS_ROOT"] = tmp.name
    get_settings.cache_clear()
    from app.main import app
    from app.store import Store

    Store(get_settings().data_dir).upsert_user("admin", "admin")
    client = TestClient(app)
    client.headers["X-Synetra-User"] = "admin"
    try:
        r = client.post("/api/tasks",
                        json={"project": "proj-autogen", "name": "sync-orders",
                              "config": _cfg().model_dump()})
        assert r.status_code == 200
        key = r.json()["task_key"]
        # 保存后任务执行体 current/ 已被生成，has_current 为真
        scripts = ScriptStore(get_settings().scripts_root or get_settings().data_dir)
        assert scripts.has_current(key)
        script = scripts.read_current(key)
        assert "etl_sdk" in script and "ods.orders_dw" in script
        # 版本记录标记为自动生成
        recs = scripts.list_records(key)
        assert recs and recs[0]["generated_by"] == "auto-config"
        # 即可点击「运行」：已绕过"尚无已确认脚本"守卫并真正发起执行。
        # 测试环境无真实连接（SRC_PG/TGT_PG 不存在），真实写库会失败，
        # 但错误不应再是"缺少执行脚本"，证明执行体已生效。
        run = client.post(f"/api/tasks/{key}/run")
        assert run.status_code == 200
        body = run.json()
        assert body["status"] in ("success", "failed")
        assert "尚无已确认脚本" not in (body.get("error") or "")
    finally:
        client.close()
        tmp.cleanup()