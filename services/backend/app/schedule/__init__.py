"""调度与执行编排（任务 8.x）。

- 定时触发：本地轮询调度器 LocalScheduler 扫描启用任务，按 cron 到达时触发执行。
- 失败重试（次数/间隔）与失败邮件告警。
- 触发执行复用本地执行器（子进程 + etl_sdk），无外部调度引擎依赖。
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings

DEFAULT_RETRY_TIMES = 2
DEFAULT_RETRY_INTERVAL_S = 60


def new_run_id() -> str:
    return "run-" + secrets.token_urlsafe(12)


def run_with_retry(store, run_id: str, execute, retry_times: int = DEFAULT_RETRY_TIMES,
                   retry_interval_s: int = DEFAULT_RETRY_INTERVAL_S,
                   log: bool = False) -> str:
    """8.3 按 (次数, 间隔) 执行任务，失败重试；仍失败返回 'failed'（邮件告警由 API 层触发）。"""
    import time

    attempt = 0
    last_error = None
    while attempt <= retry_times:
        attempt += 1
        store.append_run_log(run_id, f"尝试 # {attempt}", level="info")
        try:
            outcome = execute()  # 返回 {"status": "success"|"failed", "rows": int}
            store.finish_run(run_id, status=outcome.get("status", "success"),
                             rows_total=outcome.get("rows", 0),
                             error=outcome.get("error"))
            return outcome.get("status", "success")
        except Exception as e:  # noqa: BLE001 - 捕获执行异常以判定重试
            last_error = str(e)
            store.append_run_log(run_id, f"执行异常: {last_error}", level="error")
            if attempt <= retry_times:
                store.append_run_log(run_id, f"{retry_interval_s}s 后重试", level="warn")
                time.sleep(min(retry_interval_s, 5))  # 单测/轻量场景最多等 5s
    store.finish_run(run_id, status="failed", error=last_error)
    return "failed"


def _field_values(field: str, lo: int, hi: int) -> set[int]:
    """把 cron 单个字段解析为允许取值集合：支持 *、数字、逗号列表、*/步进。"""
    field = field.strip()
    if not field or field == "*":
        return set(range(lo, hi + 1))
    vals: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            base, _step_s = part.split("/", 1)
            step = int(_step_s)
            if base == "*":
                vals.update(range(lo, hi + 1, step))
            else:
                vals.update(range(int(base), hi + 1, step))
        elif part == "*":
            vals.update(range(lo, hi + 1))
        else:
            vals.add(int(part))
    return vals


def next_cron_time(expr: str, now: datetime | None = None) -> datetime:
    """计算 5 字段 cron 的下一次运行时间（UTC）。

    支持每分钟/小时扫描；minute hour day-of-month month day-of-week 五段均支持
    *、数字、逗号列表与 */步进；dom 与 dow 均限定时按"或"匹配。
    """
    parts = (expr or "").strip().split()
    if len(parts) != 5:
        raise ValueError("cron 需为 5 字段（分 时 日 月 周）")
    mins = _field_values(parts[0], 0, 59)
    hrs = _field_values(parts[1], 0, 23)
    doms = _field_values(parts[2], 1, 31)
    months = _field_values(parts[3], 1, 12)
    dows = _field_values(parts[4], 0, 7)
    py_dows = {6 if c in (0, 7) else c - 1 for c in dows}  # cron 周几 → python weekday
    dom_restricted = parts[2].strip() != "*"
    dow_restricted = parts[4].strip() != "*"

    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    cur = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(525600):  # 约一年，找不到即抛错
        dom_ok = cur.day in doms
        dow_ok = cur.weekday() in py_dows
        if dom_restricted and dow_restricted:
            day_ok = dom_ok or dow_ok
        elif dom_restricted:
            day_ok = dom_ok
        elif dow_restricted:
            day_ok = dow_ok
        else:
            day_ok = True
        if cur.minute in mins and cur.hour in hrs and cur.month in months and day_ok:
            return cur
        cur += timedelta(minutes=1)
    raise ValueError("在可搜索范围内找不到下一次运行时间")


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    v = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class LocalScheduler:
    """本地轮询调度器：后台线程扫描 store.list_schedules() 中启用任务并按 next_run 触发。

    回调可注入以复测（默认执行脚本）。用 config 开关控制启停，随主进程生命周期托管。
    """

    def __init__(self, store, run_callback=None, interval_s: int = 20):
        self.store = store
        self.run_callback = run_callback or self._default_run
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- 触发执行（复用 API 层任务执行语义，标注 via=schedule） ----

    def _default_run(self, task_key: str) -> str:
        cfg = self.store.get_task(task_key)
        from app.scripts import ScriptStore

        scripts = ScriptStore(get_settings().scripts_root or get_settings().data_dir)
        run_id = new_run_id()
        via = "schedule"
        self.store.start_run(run_id, task_key, cfg.project, via, attempt=1)

        def execute():
            if not scripts.has_current(task_key):
                return {"status": "failed", "rows": 0,
                        "error": "该任务尚无已确认脚本，请先生成/编写脚本"}
            from app.scripting.executor import dry_run_script

            res = dry_run_script(scripts.read_current(task_key), cfg.model_dump())
            if res.ok:
                self.store.append_run_log(run_id, f"本地调度执行完成，写入 {res.total_records()} 行", level="info")
                return {"status": "success", "rows": res.total_records()}
            self.store.append_run_log(run_id, f"脚本执行失败: {res.stderr.strip()}", level="error")
            return {"status": "failed", "rows": 0, "error": res.stderr.strip()}

        status = run_with_retry(self.store, run_id, execute,
                                retry_times=get_settings().retry_times,
                                retry_interval_s=get_settings().retry_interval_s)
        return status

    # ---- 调度扫描 ----

    def tick(self, now: datetime | None = None) -> list[str]:
        """扫描一次：触发到期任务并推进 next_run，返回本次触发任务 key 列表。"""
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        fired: list[str] = []
        for s in self.store.list_schedules():
            if not s["schedule_enabled"] or not s["schedule"]:
                continue
            next_run = _parse_iso(s["schedule_next_run"])
            if next_run is not None and next_run > now:
                continue
            # 到期或未初始化（next_run 为空）→ 触发，并把下次运行推进
            try:
                nxt = next_cron_time(s["schedule"], now)
            except ValueError:
                continue
            self.store.update_task_schedule(s["task_key"], schedule_next_run=nxt.isoformat())
            self.run_callback(s["task_key"])
            fired.append(s["task_key"])
        return fired

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="local-scheduler", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001, S110 - 单次扫描失败不应终止调度线程
                pass
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


__all__ = [
    "DEFAULT_RETRY_INTERVAL_S",
    "DEFAULT_RETRY_TIMES",
    "LocalScheduler",
    "new_run_id",
    "next_cron_time",
    "run_with_retry",
]