"""6.1 / 6.2 真实 LLM 对照评测（随本变更归档，用于验收与后续回归）。

6.1 表达式准确性：同一批自然语言需求，在「注入表达式技能包」与「不注入」两种配置下
    各生成一轮，统计
      - 首次通过率 first_pass_rate：生成过程**未触发修复轮**（round=repair）的比例
      - 最终合规率 final_compliant_rate：expression_issues 为空的比例
      - 规范化率 checks_ok_rate：拼接类需求用了 concat(...)（未用 f''/%/+）、
        cast/null_fill 类需求未误写成 expression、条件映射用 if-else
      - repair_rounds：触发修复轮的用例数

6.2 多轮价值：同一初始草稿连续 3 轮修订，统计「未被指令涉及的字段映射保持不变」的比例，
    多轮（携带 history）对比单轮退化（不携带 history）。

用法（在 services/backend 下执行，需先配置好 LLM）：
    ..\\..\\..\\venv\\Scripts\\python.exe <此脚本> [--arms skill,baseline] [--only 6.1|6.2]
    或： .\\venv\\Scripts\\python.exe <此脚本> --only 6.1 --arms skill,baseline

「不注入技能包」对照组的实现：把 app.llm.rag._SKILLS_DIR 指向不存在的目录（进程内改，
不改动仓库文件）；技能包的两个注入点（会话级 system 与 RAG）都从该常量取路径。

结果写在脚本同目录的 eval_result.json（含逐用例明细与首次表达式）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[4] / "services" / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.llm import rag  # noqa: E402
from app.llm.config import apply_effective  # noqa: E402
from app.llm.provider import get_provider  # noqa: E402
from app.llm.task_draft import _parse_full_task, generate_draft, generate_revise  # noqa: E402
from app.store import Store  # noqa: E402


class Recorder:
    """包一层 provider，记录每轮原始输出（用于捕获首次写错的表达式）。"""

    def __init__(self, inner):
        self.inner = inner
        self.outputs: list[str] = []
        self.name = inner.name
        self.model_name = inner.model_name

    def converse(self, messages, *, system=None):
        out = self.inner.converse(messages, system=system)
        self.outputs.append(out)
        return out

    def list_models(self):
        return self.inner.list_models()

SKILLS_DIR = BACKEND / "app" / "llm" / "skills"
MISSING_DIR = BACKEND / "app" / "llm" / "_skills_disabled_"

STORE = Store(get_settings().data_dir)
CONN = STORE.list_connections()[0]
CONN_KEY = CONN.conn_key
CONNECTIONS = [{"conn_key": CONN.conn_key, "name": CONN.name,
                "ctype": CONN.ctype.value, "project": CONN.project}]

# ---- 6.1 用例 ----

CONCAT = {"expr_has": "concat(", "no_discouraged": True}
CASES_61 = [
    {"id": "T01", "table": "orders", "desc": "把 orders 的 id、order_no、customer_name 同步到 orders_dw，customer_name 去掉首尾空格",
     "checks": [{"expr_has": "strip("}]},
    {"id": "T02", "table": "orders", "desc": "同步 orders 到 orders_dw，phone 字段统一去掉首尾空格，amount 保留两位小数",
     "checks": [{"expr_has": "strip("}, {"expr_has": "round("}]},
    {"id": "T03", "table": "orders", "desc": "同步 orders 到 orders_dw，customer_name 统一转成大写",
     "checks": [{"expr_has": "upper("}]},
    {"id": "T04", "table": "orders", "desc": "同步 orders 到 orders_dw，把 id 与 order_no 拼成一个新列 order_key，中间用 - 连接",
     "checks": [CONCAT]},
    {"id": "T05", "table": "orders", "desc": "同步 orders 到 orders_dw，create_time 为空时填 1970-01-01 00:00:00",
     "checks": [{"source": "create_time", "kind_not": "expression"}]},
    {"id": "T06", "table": "orders4", "desc": "同步 orders4 到 orders4_dw，qty 是字符串，需要转成整数写入",
     "checks": [{"source": "qty", "kind_not": "expression"}]},
    {"id": "T07", "table": "orders4", "desc": "同步 orders4 到 orders4_dw，active 为空时填 0，非空时转成整数",
     "checks": [{"source": "active", "kind_not": "expression"}]},
    {"id": "T08", "table": "orders_z", "desc": "orders_z 里的字段都是字符串，同步到 orders_z_dw 时 amount 要转成数值、status 要转成整数，其余字段原样同步",
     "checks": [{"source": "amount", "kind_not": "expression"},
                {"source": "status", "kind_not": "expression"}]},
    {"id": "T09", "table": "product", "desc": "同步 product 到 product_dw，把 name 与 description 拼接成一个新列 full_desc，中间用冒号分隔",
     "checks": [CONCAT]},
    {"id": "T10", "table": "product", "desc": "同步 product 到 product_dw，stock 为空时填 0",
     "checks": [{"source": "stock", "kind_not": "expression"}]},
    {"id": "T11", "table": "orders", "desc": "同步 orders 到 orders_dw，status 等于 1 时输出 paid，否则输出 unpaid",
     "checks": [{"expr_has": "if "}]},
    {"id": "T12", "table": "orders", "desc": "同步 orders 到 orders_dw，order_no 与 id 拼接成 sn，用 / 连接，并把 customer_name 去掉首尾空格",
     "checks": [CONCAT, {"expr_has": "strip("}]},
    # T13 复现用户实测报错的原话（掩码需求，曾回落为 CONCAT/LEFT/SUBSTRING）
    {"id": "T13", "table": "orders",
     "desc": "每天从 orders 订单抽取到 orders_dw，主键 id，将姓名的第二个字转换为*，手机号中间四位转换为*",
     "checks": [{"expr_has": "customer_name[0]"}, {"expr_has": "phone[0:3]"}]},
    {"id": "T14", "table": "orders", "desc": "同步 orders 到 orders_dw，把 customer_name 的第二个字替换为 *",
     "checks": [{"expr_has": "customer_name[0]"}]},
    {"id": "T15", "table": "orders", "desc": "同步 orders 到 orders_dw，phone 中间四位用 * 打码，只保留前三位与后四位",
     "checks": [{"expr_has": "phone[0:3]"}, {"expr_has": "phone[7:]"}]},
]


def _check(fields: list[dict], check: dict) -> bool:
    if "expr_has" in check:
        exprs = [str(f.get("expression") or "") for f in fields
                 if f.get("kind") == "expression"]
        if not any(check["expr_has"] in e for e in exprs):
            return False
        if check.get("no_discouraged"):
            bad = ("f'", 'f"', " % ", " + '", ' + "')
            if any(mark in e for e in exprs for mark in bad):
                return False
        return True
    if "source" in check:
        matched = [f for f in fields if f.get("source") == check["source"]]
        if not matched:
            return False
        return all(f.get("kind") != check["kind_not"] for f in matched)
    return True


def _draft_calls() -> list[dict]:
    """llm_draft_call 审计事件（list_events 按时间倒序）。"""
    return [json.loads(e["detail_json"]) for e in STORE.list_events(limit=400)
            if e["action"] == "llm_draft_call"]


def _new_events(before: int) -> list[dict]:
    """取本次生成新增的事件（倒序列表新增的前若干条）。"""
    calls = _draft_calls()
    return calls[:max(len(calls) - before, 0)]


def run_61(arm: str) -> dict:
    rec = Recorder(get_provider(get_settings().llm_provider))
    rows = []
    for case in CASES_61:
        before = len(_draft_calls())
        rec.outputs = []
        err = ""
        result = None
        try:
            result = generate_draft(STORE, rec, case["desc"], "demo", CONNECTIONS,
                                    actor="eval", source_conn_key=CONN_KEY,
                                    target_conn_key=CONN_KEY)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:120]}"
        # 捕获「首次完整草稿」里的表达式（第 2 轮输出），用于说明首轮违规内容
        first_exprs: list[str] = []
        if len(rec.outputs) >= 2:
            try:
                first = _parse_full_task(rec.outputs[1], "demo")
                first_exprs = [str(f.get("expression"))
                               for f in first.get("fields") or []
                               if f.get("kind") == "expression" and f.get("expression")]
            except Exception:  # noqa: BLE001
                first_exprs = ["<unparsable>"]
        evs = _new_events(before)
        repaired = any(e.get("round") == "repair" for e in evs)
        fields = (result.draft.get("fields") or []) if result else []
        checks_ok = all(_check(fields, c) for c in case["checks"]) if result else False
        rows.append({
            "id": case["id"], "error": err,
            "first_pass": bool(result) and not repaired,
            "repaired": repaired,
            "issues": len(result.expression_issues) if result else -1,
            "checks_ok": checks_ok,
            "exprs": [f.get("expression") for f in fields if f.get("kind") == "expression"],
            "first_exprs": first_exprs,
            "kinds": {f.get("source"): f.get("kind") for f in fields},
        })
        print(f"  [{arm}] {case['id']} err={err[:40]!r} repaired={repaired} "
              f"issues={rows[-1]['issues']} checks_ok={checks_ok} exprs={rows[-1]['exprs']}")
    n = len(rows)
    ok_rows = [r for r in rows if not r["error"]]
    summary = {
        "arm": arm, "n": n, "errors": n - len(ok_rows),
        "first_pass_rate": round(sum(1 for r in ok_rows if r["first_pass"]) / max(len(ok_rows), 1), 3),
        "final_compliant_rate": round(sum(1 for r in ok_rows if r["issues"] == 0) / max(len(ok_rows), 1), 3),
        "checks_ok_rate": round(sum(1 for r in ok_rows if r["checks_ok"]) / max(len(ok_rows), 1), 3),
        "repair_rounds": sum(1 for r in ok_rows if r["repaired"]),
    }
    return {"summary": summary, "rows": rows}


# ---- 6.2 用例 ----

REVISES = [
    "改成全量同步，不要增量",
    "增量水位字段改用 update_time",
    "再加一个字段：把 order_no 原样同步到目标列 order_no",
]


def _field_sig(f: dict) -> tuple:
    return (f.get("source"), f.get("target"), f.get("kind"), f.get("cast_to"),
            f.get("null_default"), f.get("constant_value"), f.get("expression"))


def _mentioned(field: dict, instruction: str) -> bool:
    text = instruction.lower()
    for key in ("source", "target"):
        col = (field.get(key) or "")
        if col and col.lower() in text:
            return True
    expr = field.get("expression") or ""
    return bool(expr) and expr.lower() in text


def run_62(arm: str) -> dict:
    """arm: 'multiturn' | 'singleturn'"""
    provider = get_provider(get_settings().llm_provider)
    seed = generate_draft(STORE, provider, "把 orders 的 id、order_no、customer_name、amount、status 同步到 orders_dw",
                          "demo", CONNECTIONS, actor="eval",
                          source_conn_key=CONN_KEY, target_conn_key=CONN_KEY)
    draft, history = seed.draft, seed.history
    stats = []
    for i, instruction in enumerate(REVISES, start=1):
        try:
            if arm == "multiturn":
                res = generate_revise(STORE, provider, draft, instruction, "demo",
                                      actor="eval", history=history)
                history = res.history
            else:
                res = generate_revise(STORE, provider, draft, instruction, "demo",
                                      actor="eval", history=None)
        except Exception as e:  # noqa: BLE001
            print(f"  [{arm}] revise{i} ERROR {type(e).__name__}: {str(e)[:100]}")
            stats.append({"round": i, "stable": None, "total": None, "error": str(e)[:80]})
            continue
        old = {f.get("source"): f for f in (draft.get("fields") or [])}
        new = {f.get("source"): f for f in (res.draft.get("fields") or [])}
        stable = total = 0
        for src, f in old.items():
            if _mentioned(f, instruction):
                continue
            total += 1
            if src in new and _field_sig(new[src]) == _field_sig(f):
                stable += 1
        rate = round(stable / total, 3) if total else None
        stats.append({"round": i, "stable": stable, "total": total, "rate": rate,
                      "issues": len(res.expression_issues)})
        print(f"  [{arm}] revise{i} 未涉及字段稳定 {stable}/{total} rate={rate} "
              f"issues={len(res.expression_issues)}")
        draft = res.draft

    rates = [s["rate"] for s in stats if s.get("rate") is not None]
    summary = {"arm": arm, "rounds": len(stats),
               "avg_stability_rate": round(sum(rates) / len(rates), 3) if rates else None,
               "expression_issues": sum(s.get("issues") or 0 for s in stats)}
    return {"summary": summary, "rows": stats}


def set_skills(enabled: bool) -> None:
    rag._SKILLS_DIR = SKILLS_DIR if enabled else MISSING_DIR


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="skill,baseline")
    ap.add_argument("--only", default="6.1,6.2")
    args = ap.parse_args()

    apply_effective(STORE, get_settings())
    provider = get_provider(get_settings().llm_provider)
    print(f"provider={provider.name} model={provider.model_name} "
          f"conn={CONN.name} tables=orders/product/orders4/orders_z\n")

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    out: dict = {}
    if "6.1" in only:
        for arm in [a.strip() for a in args.arms.split(",") if a.strip()]:
            enabled = arm == "skill"
            set_skills(enabled)
            print(f"=== 6.1 arm={arm}（技能包注入={enabled}）===")
            out[f"6.1:{arm}"] = run_61(arm)
    if "6.2" in only:
        for arm in ["multiturn", "singleturn"]:
            set_skills(True)
            print(f"=== 6.2 arm={arm} ===")
            out[f"6.2:{arm}"] = run_62(arm)

    out_path = Path(__file__).with_name("eval_result.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print("\n=== 汇总 ===")
    for k, v in out.items():
        print(k, json.dumps(v["summary"], ensure_ascii=False))
    print(f"\n明细已写入 {out_path}")


if __name__ == "__main__":
    main()
