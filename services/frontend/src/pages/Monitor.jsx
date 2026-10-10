import { useEffect, useState } from "react";
import { Badge, Button, Callout, Card, Row, StatCard, Table, shortId, shortTime } from "../components/ui";
import { api } from "../api/client";
import RunDetailModal from "../components/RunDetailModal";

const STATE_TONE = { success: "success", running: "info", failed: "danger", retrying: "warning", waiting: "warning" };
const STATE_CN = { success: "成功", running: "运行中", failed: "失败", retrying: "重试中", waiting: "等待重试" };
const TRIGGER_CN = { schedule: "定时", external_api: "外部API", api: "外部API", manual: "手动", cron: "定时" };

function Bar({ value, tone = "info" }) {
  return (
    <div style={{ width: 180, height: 8, borderRadius: 999, background: "var(--bg-subtle)", overflow: "hidden" }} >
      <div style={{ width: `${Math.min(100, value)}%`, height: "100%", borderRadius: 999, background: `var(--${tone})` }} />
    </div>
  );
}

export default function Monitor() {
  const [runs, setRuns] = useState([]);
  const [statusCounts, setStatusCounts] = useState({});
  const [metrics, setMetrics] = useState([]);
  const [recons, setRecons] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [events, setEvents] = useState([]);
  const [logs, setLogs] = useState([]);
  const [sys, setSys] = useState(null);
  const [error, setError] = useState(null);
  const [openRun, setOpenRun] = useState(null);

  const loadSys = () => api.get("/monitor/system").then(setSys).catch(() => setSys(null));

  const load = () => {
    Promise.all([
      api.get("/runs", { page_size: 50 }),
      api.get("/metrics"),
      api.get("/reconciliations"),
      api.get("/alerts"),
      api.get("/alerts/events"),
    ]).then(([r, m, rc, al, ev]) => {
      setRuns(r.runs || []);
      setStatusCounts(r.status_counts || {});
      setMetrics(m || []);
      setRecons(rc || []);
      setAlerts(al || []);
      setEvents(ev || []);
      setError(null);
      // 载入最近一次运行的日志
      const latest = (r.runs || [])[0];
      if (latest) api.get(`/runs/${latest.run_id}/logs`).then((l) => setLogs(l.logs || [])).catch(() => {});
    }).catch((e) => setError(e.message));
  };
  useEffect(() => { load(); loadSys(); }, []);

  const done = runs.filter((x) => x.status === "success").length;
  const fail = runs.filter((x) => x.status === "failed").length;
  const totalRows = runs.reduce((a, x) => a + (x.rows_total || 0), 0);
  const activeAlerts = alerts.filter((a) => a.enabled).length;
  const avgLat = metrics.length ? Math.round(metrics.reduce((a, m) => a + (m.latency_ms || 0), 0) / metrics.length) : 0;

  return (
    <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <div className="mac-row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <div><h1 className="mac-h1">监控与运行历史</h1><div className="mac-muted">实时日志 · 资源占用 · 延迟与数据量 · 对账</div></div>
        <Button variant="primary" icon="refresh" onClick={() => { load(); loadSys(); }}>刷新</Button>
      </div>

      {error && <div className="mac-callout mac-callout--warning">部分监控数据不可达（{error}）。</div>}

      <Card title="系统资源守护" sub={sys ? `主机 ${sys.hostname || ""} · ${sys.ts} · ${sys.cpu_cores} 核` : "读取中…"}
        actions={<Button variant="ghost" icon="refresh" onClick={loadSys}>刷新</Button>}>
        {sys ? (
          <div className="mac-detail-row__inner">
            <div className="mac-detail__item">
              <span className="mac-detail__label">CPU 使用率</span>
              <Row><Bar value={sys.cpu_percent} tone={sys.cpu_percent > 85 ? "danger" : "info"} />
                <span className="num" style={{ width: 64, textAlign: "right" }}>{sys.cpu_percent}%</span></Row>
            </div>
            <div className="mac-detail__item">
              <span className="mac-detail__label">内存占用</span>
              <Row><Bar value={sys.memory.percent} tone={sys.memory.percent > 85 ? "danger" : "warning"} />
                <span className="num" style={{ width: 84, textAlign: "right" }}>{sys.memory.percent}% · {sys.memory.used_mb}MB/{sys.memory.total_mb}MB</span></Row>
            </div>
            <div className="mac-detail__item">
              <span className="mac-detail__label">磁盘使用</span>
              <Row><Bar value={sys.disk.percent} tone={sys.disk.percent > 90 ? "danger" : "success"} />
                <span className="num" style={{ width: 96, textAlign: "right" }}>{sys.disk.percent}% · {sys.disk.used_gb.toLocaleString()}G/{sys.disk.total_gb.toLocaleString()}G</span></Row>
            </div>
          </div>
        ) : <div className="mac-muted">无法读取主机资源（仅支持 Linux `/proc`）。</div>}
      </Card>

      <div className="mac-statgrid">
        <StatCard label="运行批次" value={String(runs.length)} icon="run" tone="info" />
        <StatCard label="成功" value={String(done)} up icon="check" tone="success" />
        <StatCard label="失败" value={String(fail)} icon="alert" tone="danger" />
        <StatCard label="同步总行数" value={totalRows.toLocaleString()} icon="mdm" tone="success" />
        <StatCard label="平均延迟" value={`${avgLat}ms`} icon="monitor" tone="warning" />
        <StatCard label="启用的告警" value={String(activeAlerts)} icon="alert" tone={activeAlerts ? "warning" : "success"} />
      </div>

      <Card title="最近运行">
        <Table head={["运行实例", "任务", "触发方式", "状态", "行数", "时间", "操作"]}>
          {runs.length === 0
            ? <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--text-tertiary)" }}>暂无运行记录</td></tr>
            : runs.map((r) => (
                <tr key={r.run_id} style={{ cursor: "pointer" }} title="点击查看运行详情" onClick={() => setOpenRun(r)}>
                  <td className="num" style={{ color: "var(--text-tertiary)" }} title={r.run_id}>{shortId(r.run_id)}</td>
                  <td className="mac-nowrap" title={r.task_key}><span style={{ fontWeight: 500 }}>{shortId(r.task_key, 12)}</span></td>
                  <td><span className="mac-chip">{TRIGGER_CN[r.trigger_via] || r.trigger_via || "-"}</span></td>
                  <td><Badge tone={STATE_TONE[r.status]} label={STATE_CN[r.status] || r.status} /></td>
                  <td className="num">{r.rows_total ?? "-"}</td>
                  <td className="mac-nowrap" style={{ color: "var(--text-secondary)" }} title={r.started_at || ""}>{shortTime(r.started_at)}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <Button variant="ghost" icon="eye" style={{ padding: 6 }} title="查看运行详情" onClick={() => setOpenRun(r)}>详情</Button>
                  </td>
                </tr>
              ))}
        </Table>
      </Card>

      <Card title="实时日志" actions={<Button variant="ghost" icon="refresh" onClick={load}>刷新</Button>}>
        <pre style={{ fontSize: 12, lineHeight: 1.7, margin: 0, fontFamily: "var(--font-mono)", color: "var(--text-secondary)", whiteSpace: "pre-wrap" }}>
          {logs.length === 0 ? "(无日志)" : logs.map((l) => `${l.ts} [${(l.level || "info").toUpperCase()}] ${l.line}`).join("\n")}
        </pre>
      </Card>

      <Card title="数据对账" actions={<Badge tone={recons.some((r) => !r.checksum_match) ? "danger" : "success"} label={recons.some((r) => !r.checksum_match) ? "存在差异" : "一致"} dot={false} />}>
        <Table head={["任务", "源行数", "目标行数", "校验和一致", "时间"]}>
          {recons.length === 0
            ? <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-tertiary)" }}>暂无对账记录</td></tr>
            : recons.map((r) => (
                <tr key={r.id}>
                  <td className="mac-nowrap" title={r.task_key}><span style={{ fontWeight: 500 }}>{shortId(r.task_key, 12)}</span></td>
                  <td className="num">{r.source_rows}</td>
                  <td className="num">{r.target_rows}</td>
                  <td><Badge tone={r.checksum_match ? "success" : "danger"} label={r.checksum_match ? "一致" : "差异"} /></td>
                  <td className="mac-nowrap" style={{ color: "var(--text-secondary)" }} title={r.created_at || ""}>{shortTime(r.created_at)}</td>
                </tr>
              ))}
        </Table>
      </Card>

      <Card title="告警事件" sub="阈值触发历史">
        <Table head={["时间", "任务", "指标", "事件"]}>
          {events.length === 0
            ? <tr><td colSpan={4} style={{ textAlign: "center", color: "var(--text-tertiary)" }}>暂无告警事件</td></tr>
            : events.map((e, i) => (
                <tr key={i}>
                  <td className="mac-nowrap" style={{ color: "var(--text-secondary)" }} title={e.ts || e.created_at || ""}>{shortTime(e.ts || e.created_at)}</td>
                  <td className="mac-nowrap" title={e.task_key || (e.rule && e.rule.task_key) || ""}>{shortId(e.task_key || (e.rule && e.rule.task_key))}</td>
                  <td>{e.metric || "-"}</td>
                  <td><Badge tone="danger" label={e.message || "触发"} /></td>
                </tr>
              ))}
        </Table>
      </Card>

      {openRun && <RunDetailModal key={openRun.run_id} run={openRun} onClose={() => setOpenRun(null)} />}
    </div>
  );
}