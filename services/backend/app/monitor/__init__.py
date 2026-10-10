"""监控、对账与阈值告警（任务 9.x）。

- 9.1 任务列表/状态（running/success/failed/retrying/unscheduled）与运行历史
- 9.2 实时日志
- 9.3 资源/延迟/数据量指标
- 9.4 数据对账（源端 vs 目标端行数/校验和，可注入行源以真机/单测两用）
- 9.5 可配置阈值告警（metric/op/threshold → 触发并通知）
"""

from __future__ import annotations

from typing import ClassVar

RunStatus = [
    "running", "success", "failed", "retrying", "unscheduled",
]


def summarize_run_count(rows: int) -> str:
    """行数友好的展示：<1000 原值，否则缩写。"""
    if rows < 1000:
        return str(rows)
    return f"{rows / 1000:.1f}k"


def derive_trigger_via(task_cfg: dict) -> str:
    """按任务配置推导其默认触发方式（用于运行历史按触发方式区分）。"""
    schedule = (task_cfg.get("schedule") or "").strip()
    if schedule:
        return "schedule"
    return "manual"


class Reconciler:
    """源端 vs 目标端行数/校验和对账（任务 9.4）。

    通过可注入的行源函数拉取源/目标计数与校验和；一致输出一致结论，不一致输出差异明细。
    """

    def __init__(self, source_counter=None, target_counter=None):
        self._source_counter = source_counter
        self._target_counter = target_counter

    @staticmethod
    def _default_rowset(source_rows):
        return {"count": len(source_rows), "checksum": sum_checksum(source_rows)}

    def reconcile(self, task_key: str, project: str,
                  source_rows=None, target_rows=None) -> dict:
        if callable(self._source_counter) and callable(self._target_counter):
            src = self._source_counter()
            tgt = self._target_counter()
        else:
            src = self._default_rowset(source_rows or [])
            tgt = self._default_rowset(target_rows or [])
        matched = src["count"] == tgt["count"] and src["checksum"] == tgt["checksum"]
        details = {
            "source_count": src["count"], "target_count": tgt["count"],
            "source_checksum": src["checksum"], "target_checksum": tgt["checksum"],
        }
        if matched:
            details["conclusion"] = "一致"
        else:
            details["conclusion"] = "不一致"
            diff = diff_rows(source_rows or [], target_rows or [])
            details["diff_sample"] = diff[:10]
            details["diff_count"] = len(diff)
        return {"task_key": task_key, "project": project, "consistent": matched,
                "source_rows": src["count"], "target_rows": tgt["count"],
                "checksum_match": matched, "details": details}


def sum_checksum(rows: list[dict]) -> str:
    """对一组行做简单校验和（对有序键值拼接取 hash），用于对账一致性判断。"""
    import hashlib

    parts = []
    for r in sorted(rows, key=lambda s: str(sorted(s.items()))):
        parts.append(str(sorted((str(k), str(v)) for k, v in r.items())))
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def diff_rows(src: list[dict], tgt: list[dict]) -> list[dict]:
    """行级差异（用于不一致时的明细）：返回仅出现在源端/仅出现在目标端的样例。"""
    src_map = {str(sorted(r.items())): r for r in src}
    tgt_map = {str(sorted(r.items())): r for r in tgt}
    out: list[dict] = []
    for k, r in src_map.items():
        if k not in tgt_map:
            out.append({"in": "source_only", "row": r})
    for k, r in tgt_map.items():
        if k not in src_map:
            out.append({"in": "target_only", "row": r})
    return out


class AlertEvaluator:
    """根据监控指标对可配置阈值规则求值，命中则落事件并可通知（任务 9.5）。"""

    OPS: ClassVar[set[str]] = {">", ">=", "<", "<=", "=="}

    def __init__(self, store):
        self.store = store

    def evaluate_rule(self, rule: dict, value: float) -> bool:
        op = rule.get("op")
        threshold = float(rule.get("threshold", 0))
        if op == ">":
            return value > threshold
        if op == ">=":
            return value >= threshold
        if op == "<":
            return value < threshold
        if op == "<=":
            return value <= threshold
        if op == "==":
            return value == threshold
        return False

    def evaluate_and_notify(self, rule: dict, value: float, *, send=None) -> dict | None:
        """命中阈值则记录事件并在配置收件人时通知；返回事件 dict 或 None。"""
        if not rule.get("enabled", True):
            return None
        if not self.evaluate_rule(rule, value):
            return None
        event_id = self.store.record_alert_event(rule, value)
        event = {"id": event_id, "rule_id": rule["id"], "project": rule["project"],
                 "task_key": rule["task_key"], "metric": rule["metric"],
                 "value": value, "sent": 0}
        if rule.get("notify_to") and send:
            res = send(rule["notify_to"],
                       f"[Synetra] 监控告警 {rule['metric']} {rule['op']} {rule['threshold']}，当前 {value}")
            if res.get("sent"):
                self.store.set_alert_sent(event_id)
                event["sent"] = 1
        return event


def list_runs_presentable(store, project: str | None = None, limit: int = 100) -> list[dict]:
    return store.list_runs(project, limit=limit)


__all__ = [
    "AlertEvaluator",
    "Reconciler",
    "RunStatus",
    "derive_trigger_via",
    "diff_rows",
    "list_runs_presentable",
    "sum_checksum",
    "summarize_run_count",
]