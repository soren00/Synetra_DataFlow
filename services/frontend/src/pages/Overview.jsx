import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Badge, Button, Callout, Card, Empty, Pager, Row, Segmented, StatCard, Table, shortId, shortTime } from "../components/ui";
import RunDetailModal from "../components/RunDetailModal";

const STATE_TONE = { success: "success", failed: "danger", running: "info", retrying: "warning" };
const STATE_CN = { success: "成功", failed: "失败", running: "运行中", retrying: "重试中" };
const TRIGGER_CN = { schedule: "定时", external_api: "外部API", api: "外部API", manual: "手动", cron: "定时" };

export default function Overview() {
  const [health, setHealth] = useState(null);
  const [runs, setRuns] = useState([]);
  const [metrics, setMetrics] = useState([]);
  const [runFilter, setRunFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [runTotal, setRunTotal] = useState(0);
  const [rowsSum, setRowsSum] = useState(0);
  const [statusCounts, setStatusCounts] = useState({});
  const [openRun, setOpenRun] = useState(null);

  useEffect(() => {
    fetch("/health").then((r) => r.json()).then(setHealth).catch(() => setHealth({ status: "unreachable" }));
    api.get("/metrics").then((m) => setMetrics(m || [])).catch(() => {});
  }, []);

  // 分页与状态筛选全部交由后端裁决，前端只负责传参与渲染
  useEffect(() => {
    let alive = true;
    const params = { page, page_size: pageSize };
    if (runFilter !== "all") params.status = runFilter;
    api.get("/runs", params)
      .then((r) => {
        if (!alive) return;
        setRuns(r.runs || []);
        setRunTotal(r.total ?? 0);
        setRowsSum(r.rows_sum ?? 0);
        setStatusCounts(r.status_counts || {});
        if (r.page && r.page !== page) setPage(r.page); // 后端回夹页码（如筛选后页数变少）
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [runFilter, page, pageSize]);

  const counts = statusCounts || {};
  const total = Object.values(counts).reduce((a, n) => a + n, 0);
  const ok = counts.success || 0;
  const fail = counts.failed || 0;
  const sumRows = metrics.reduce((a, x) => a + (x.data_rows || 0), 0);
  const displayedRows = rowsSum || sumRows;
  const listRuns = runs;

  const changeFilter = (v) => { setRunFilter(v); setPage(1); };
  const changePageSize = (n) => { setPageSize(n); setPage(1); };

  return (
    <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <div className="mac-row mac-end" style={{ justifyContent: "space-between" }}>
        <div>
          <h1 className="mac-h1">总览</h1>
          <div className="mac-muted">数据管道健康与近期执行一览</div>
        </div>
        <Row>
          <Badge tone={health?.status === "ok" ? "success" : "warning"} label={`服务 ${health?.status ?? "诊断中"}`} />
          <Button variant="primary" icon="refresh" onClick={() => { fetch("/health").then((r) => r.json()).then(setHealth); }}>刷新</Button>
        </Row>
      </div>

      <div className="mac-statgrid">
        <StatCard label="运行批次" value={String(total)} icon="run" tone="info" />
        <StatCard label="成功" value={String(ok)} icon="check" tone="success" />
        <StatCard label="失败" value={String(fail)} icon="alert" tone={fail ? "danger" : "success"} />
        <StatCard label="同步总行数" value={displayedRows.toLocaleString()} icon="mdm" tone="success" />
      </div>

      <Card title="最近运行历史" actions={
        <Segmented items={[
          { value: "all", label: `全部 ${total}` },
          { value: "success", label: `成功 ${ok}` },
          { value: "failed", label: `失败 ${fail}` },
        ]} value={runFilter} onChange={changeFilter} />
      }>
        <Table head={["运行实例", "任务", "触发方式", "状态", "行数", "时间", "操作"]}>
          {listRuns.length === 0
            ? <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--text-tertiary)" }}>{runFilter === "all" ? "暂无运行记录" : "暂无匹配的运行记录"}</td></tr>
            : listRuns.map((r) => (
                <tr key={r.run_id} style={{ cursor: "pointer" }} title="点击查看运行详情" onClick={() => setOpenRun(r)}>
                  <td className="num" style={{ color: "var(--text-tertiary)" }} title={r.run_id}>{shortId(r.run_id)}</td>
                  <td className="mac-nowrap" title={r.task_key}><span style={{ fontWeight: 500 }}>{shortId(r.task_key, 12)}</span></td>
                  <td><span className="mac-chip">{TRIGGER_CN[r.trigger_via] || r.trigger_via || "-"}</span></td>
                  <td><Badge tone={STATE_TONE[r.status] || "neutral"} label={STATE_CN[r.status] || r.status} /></td>
                  <td className="num">{r.rows_total ?? "-"}</td>
                  <td className="mac-nowrap" style={{ color: "var(--text-secondary)" }} title={r.started_at || ""}>{shortTime(r.started_at)}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <Button variant="ghost" icon="eye" style={{ padding: 6 }} title="查看运行详情" onClick={() => setOpenRun(r)}>详情</Button>
                  </td>
                </tr>
              ))}
        </Table>
        <Pager page={page} pageSize={pageSize} total={runTotal}
          onPageChange={setPage} onPageSizeChange={changePageSize} />
      </Card>

      <Card title="需要关注">
        {health && health.status !== "ok"
          ? <Callout tone="danger">后端服务不可达，请检查 FastAPI 服务。</Callout>
          : <Empty icon="check" title="一切正常" desc="暂无需要关注的事项" />}
      </Card>

      {openRun && <RunDetailModal key={openRun.run_id} run={openRun} onClose={() => setOpenRun(null)} />}
    </div>
  );
}