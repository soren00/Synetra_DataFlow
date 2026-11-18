"""MySQL 真实环境验收（7.1 / 7.2 / 7.3 / 7.4）。

测试数据标注：全部表名前缀 `it_epipe_`，位于系统配置的 MySQL 库（默认 `synetra`），
用例结束（成功或失败）都会 DROP，不残留业务数据。本机无 MySQL 时整组跳过。

运行：`pytest tests/test_pipeline_mysql_integration.py -v`
可用环境变量覆盖连接：`SYNETRA_TEST_MYSQL_HOST/PORT/USER/PASSWORD/DB`。
"""

from __future__ import annotations

import os
import tracemalloc

import pytest

from app.etl_sdk.pipeline.engine import run_pipeline
from app.etl_sdk.relational import MySQLConnector
from app.models.pipeline import PipelineConfig, PipelineEdge, PipelineNode, StepType
from app.models.task_config import TaskConfig

HOST = os.environ.get("SYNETRA_TEST_MYSQL_HOST", "127.0.0.1")
PORT = int(os.environ.get("SYNETRA_TEST_MYSQL_PORT", "3306"))
USER = os.environ.get("SYNETRA_TEST_MYSQL_USER", "root")
PASSWORD = os.environ.get("SYNETRA_TEST_MYSQL_PASSWORD", "12345")
DB = os.environ.get("SYNETRA_TEST_MYSQL_DB", "synetra")

META = {"host": HOST, "port": str(PORT), "user": USER, "db": DB, "charset": "utf8mb4"}
SRC_A = "it_epipe_src_a"
SRC_B = "it_epipe_src_b"
DW = "it_epipe_dw"
INC = "it_epipe_inc"
BIG = "it_epipe_big"
BIG_DW = "it_epipe_big_dw"


def _connect():
    import pymysql

    return pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD,
                           database=DB, connect_timeout=5, charset="utf8mb4",
                           autocommit=True)


def _mysql_available() -> bool:
    try:
        _connect().close()
        return True
    except Exception:  # noqa: BLE001 - 环境不可用即跳过
        return False


pytestmark = pytest.mark.skipif(not _mysql_available(), reason="本机 MySQL 不可用")


@pytest.fixture()
def db():
    conn = _connect()
    cur = conn.cursor()
    for t in (SRC_A, SRC_B, DW, INC, BIG, BIG_DW):
        cur.execute(f"DROP TABLE IF EXISTS `{t}`")
    yield conn
    cur = conn.cursor()
    for t in (SRC_A, SRC_B, DW, INC, BIG, BIG_DW):
        cur.execute(f"DROP TABLE IF EXISTS `{t}`")
    conn.close()


def _connector(_key: str):
    return MySQLConnector(META, PASSWORD)


def _node(node_id, kind, **cfg):
    return PipelineNode(id=node_id, type=kind, config=cfg)


def _edge(a, b):
    return PipelineEdge(source=a, target=b)


def test_end_to_end_multi_source_cleaning(db, tmp_path):
    """7.1 两源 union → 映射 → 过滤 → 去重 → 聚合 → upsert，六步指标齐全。"""
    cur = db.cursor()
    cur.execute(f"CREATE TABLE `{SRC_A}` (id INT, name VARCHAR(32), amount DECIMAL(10,2), dept VARCHAR(16))")
    cur.execute(f"CREATE TABLE `{SRC_B}` (id INT, name VARCHAR(32), amount DECIMAL(10,2), dept VARCHAR(16))")
    cur.executemany(f"INSERT INTO `{SRC_A}` VALUES (%s,%s,%s,%s)",
                    [(1, " bob ", 10, "a"), (2, "ann", 5, "b"), (3, "dup", 1, "a")])
    cur.executemany(f"INSERT INTO `{SRC_B}` VALUES (%s,%s,%s,%s)",
                    [(3, "dup", 1, "a"), (4, "carl", -2, "b"), (5, "dina", 7, "a")])

    pipe = PipelineConfig(
        nodes=[
            _node("src_a", StepType.SOURCE_TABLE, conn_key="c", object=SRC_A),
            _node("src_b", StepType.SOURCE_TABLE, conn_key="c", object=SRC_B),
            _node("merge", StepType.UNION, columns=[]),
            _node("pick", StepType.SELECT, rules=[
                {"kind": "direct", "source": "id", "target": "id"},
                {"kind": "direct", "source": "name", "target": "name"},
                {"kind": "cast", "source": "amount", "target": "amount", "cast_to": "float"},
                {"kind": "direct", "source": "dept", "target": "dept"},
            ]),
            _node("trim", StepType.STRING_OPS, field="name", target="name", op="trim"),
            _node("positive", StepType.FILTER, expression="amount > 0"),
            _node("dedup", StepType.UNIQUE, keys=["id"]),
            _node("agg", StepType.AGGREGATE, group_by=["dept"], aggregations=[
                {"func": "sum", "field": "amount", "target": "total"},
                {"func": "count", "field": "*", "target": "cnt"},
            ]),
            _node("dst", StepType.TARGET_TABLE, conn_key="c", object=DW,
                  mode="upsert", primary_keys=["dept"]),
        ],
        edges=[
            _edge("src_a", "merge"), _edge("src_b", "merge"), _edge("merge", "pick"),
            _edge("pick", "trim"), _edge("trim", "positive"), _edge("positive", "dedup"),
            _edge("dedup", "agg"), _edge("agg", "dst"),
        ],
        commit_size=2,
    )
    out = run_pipeline(pipe, connector_factory=_connector, spill_root=tmp_path / "spill")

    steps = {m["node_id"]: m for m in out.metrics}
    assert steps["merge"]["out_rows"] == 6           # 两源合并
    assert steps["positive"]["rejected_rows"] == 1   # carl 的负金额被过滤
    assert steps["dedup"]["out_rows"] == 4           # id=3 重复保留首行
    assert out.rows == 2

    cur.execute(f"SELECT dept, total, cnt FROM `{DW}` ORDER BY dept")
    assert cur.fetchall() == (("a", 18.0, 3), ("b", 5.0, 1))

    # upsert 幂等：重跑不产生重复行
    run_pipeline(pipe, connector_factory=_connector, spill_root=tmp_path / "spill")
    cur.execute(f"SELECT COUNT(*) FROM `{DW}`")
    assert cur.fetchone()[0] == 2


def test_incremental_watermark_second_run_only_new(db, tmp_path):
    """7.2 真实 SQL 水位过滤：第二次运行只抽取新增数据，水位推进到本次最大值。"""
    cur = db.cursor()
    cur.execute(f"CREATE TABLE `{INC}` (id INT, updated_at DATETIME)")
    cur.executemany(f"INSERT INTO `{INC}` VALUES (%s,%s)",
                    [(1, "2024-01-01 00:00:00"), (2, "2024-01-02 00:00:00")])

    pipe = PipelineConfig(
        nodes=[
            _node("src", StepType.SOURCE_TABLE, conn_key="c", object=INC,
                  watermark_field="updated_at", track_watermark="updated_at"),
            _node("dst", StepType.TARGET_TABLE, conn_key="c", object=DW, mode="append"),
        ],
        edges=[_edge("src", "dst")],
    )
    first = run_pipeline(pipe, connector_factory=_connector, spill_root=tmp_path / "spill")
    assert first.rows == 2
    assert str(first.watermark).startswith("2024-01-02")

    cur.execute(f"INSERT INTO `{INC}` VALUES (%s,%s)", (3, "2024-01-03 00:00:00"))
    second = run_pipeline(pipe, connector_factory=_connector, spill_root=tmp_path / "spill",
                          watermark=str(first.watermark))
    assert second.rows == 1
    assert str(second.watermark).startswith("2024-01-03")

    cur.execute(f"SELECT id FROM `{DW}` ORDER BY id")
    assert [r[0] for r in cur.fetchall()] == [1, 2, 3]


def test_scheduler_path_writes_real_data(db, tmp_path, monkeypatch):
    """7.3 调度通道真实写入：_default_run 生成运行记录且目标有数据（非 dry-run）。"""
    from app.core.config import get_settings
    from app.models.connection import ConnectionCreate, ConnectionDirection, ConnectionType
    from app.schedule import LocalScheduler
    from app.scripting.gen_config import render_config_script
    from app.scripts import ScriptStore
    from app.store import Store

    cur = db.cursor()
    cur.execute(f"CREATE TABLE `{SRC_A}` (id INT, name VARCHAR(32))")
    cur.executemany(f"INSERT INTO `{SRC_A}` VALUES (%s,%s)", [(1, "a"), (2, "b")])

    monkeypatch.setenv("SYNETRA_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    store = Store(str(tmp_path))
    conn = store.create_connection(ConnectionCreate(
        project="it", name="it-mysql", ctype=ConnectionType.MYSQL,
        direction=ConnectionDirection.SOURCE,
        meta={"host": HOST, "port": str(PORT), "user": USER, "db": DB},
        secret=PASSWORD, created_by="test",
    ))
    cfg = TaskConfig(
        project="it", name="sched", source_conn_key=conn.conn_key,
        target_conn_key=conn.conn_key, source_object=SRC_A, target_object=DW,
        mode="upsert", primary_keys=["id"], schedule="* * * * *",
        fields=[{"kind": "direct", "source": "id", "target": "id"},
                {"kind": "direct", "source": "name", "target": "name"}],
    )
    key = store.save_task(cfg)
    settings = get_settings()
    # 与 LocalScheduler._default_run 使用同一脚本根目录解析，避免用例间环境变量残留影响
    ScriptStore(settings.scripts_root or settings.data_dir).replace_current(
        key, render_config_script(cfg), generated_by="test")

    status = LocalScheduler(store)._default_run(key)
    assert status == "success", status
    run = store.list_runs(task_key=key)[0]
    assert run["status"] == "success", run.get("detail") or run
    assert run["rows_total"] == 2
    assert run["exec_channel"] == "real"

    cur.execute(f"SELECT COUNT(*) FROM `{DW}`")
    assert cur.fetchone()[0] == 2
    assert len(store.list_run_steps(run["run_id"])) >= 3


def test_large_volume_sort_memory_bounded(db, tmp_path):
    """7.4 大表分批 + 溢出：排序结果正确，进程内存峰值受控（不随总行数线性增长）。"""
    rows = 200_000
    cur = db.cursor()
    cur.execute(f"CREATE TABLE `{BIG}` (id INT, grp VARCHAR(8), v INT)")
    batch = []
    for i in range(rows):
        batch.append((i, f"g{i % 10}", (i * 7919) % 100_000))
        if len(batch) == 5000:
            cur.executemany(f"INSERT INTO `{BIG}` VALUES (%s,%s,%s)", batch)
            batch = []
    if batch:
        cur.executemany(f"INSERT INTO `{BIG}` VALUES (%s,%s,%s)", batch)

    pipe = PipelineConfig(
        nodes=[
            _node("src", StepType.SOURCE_TABLE, conn_key="c", object=BIG),
            _node("sort", StepType.SORT, keys=[{"field": "v"}, {"field": "id"}]),
            _node("dst", StepType.TARGET_TABLE, conn_key="c", object=BIG_DW, mode="append"),
        ],
        edges=[_edge("src", "sort"), _edge("sort", "dst")],
        batch_size=5000, commit_size=5000, spill_rows=20_000,
    )
    tracemalloc.start()
    out = run_pipeline(pipe, connector_factory=_connector, spill_root=tmp_path / "spill")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert out.rows == rows
    assert out.spilled is True, "预期触发溢出（spill_rows=20000 < 200000）"
    # 峰值 ≈ 一个溢出块 + 批次开销，不应接近全量数据规模
    assert peak < 120 * 1024 * 1024, f"内存峰值过高: {peak / 1024 / 1024:.1f}MB"

    cur.execute(f"SELECT COUNT(*), MIN(v), MAX(v) FROM `{BIG_DW}`")
    assert cur.fetchone() == (rows, 0, 99_999)
