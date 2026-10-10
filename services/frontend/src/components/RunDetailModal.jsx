import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { Badge, Button, Callout, Modal, Skeleton } from "./ui";

const STATE_TONE = { success: "success", failed: "danger", running: "info", retrying: "warning", waiting: "warning", unscheduled: "neutral" };
const STATE_CN = { success: "成功", failed: "失败", running: "运行中", retrying: "重试中", waiting: "等待重试", unscheduled: "未调度" };
const TRIGGER_CN = { schedule: "定时", external_api: "外部API", api: "外部API", manual: "手动", cron: "定时" };
const MODE_CN = { append: "追加（append）", upsert: "主键更新（upsert）" };
const POLL_MS = 3000;

// 运行中判定：以 finished_at 为空为主判据，状态枚举作为辅助
function isActive(r) {
  if (!r) return false;
  if (r.finished_at) return false;
  return !["success", "failed", "unscheduled"].includes(r.status);
}

function durationText(r) {
  const start = Date.parse(r?.started_at || "");
  if (Number.isNaN(start)) return "-";
  const end = r?.finished_at ? Date.parse(r.finished_at) : Date.now();
  if (Number.isNaN(end)) return "-";
  const ms = Math.max(0, end - start);
  const text = ms < 1000 ? `${ms} ms` : ms < 60000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.floor(ms / 60000)} min ${Math.round((ms % 60000) / 1000)} s`;
  return r?.finished_at ? text : `${text}（进行中）`;
}

function Item({ label, value, mono }) {
  return (
    <div className="mac-detail__item">
      <span className="mac-detail__label">{label}</span>
      <span className="mac-detail__value" style={mono ? { fontFamily: "var(--font-mono)", fontWeight: 400, fontSize: 12 } : undefined}>
        {value === undefined || value === null || value === "" ? "-" : String(value)}
      </span>
    </div>
  );
}

function Section({ title, hint, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div className="mac-row" style={{ alignItems: "center", justifyContent: "space-between" }}>
        <span className="mac-label" style={{ fontSize: "var(--fs-xs)", color: "var(--text-tertiary)", letterSpacing: "0.04em" }}>{title}</span>
        {hint || null}
      </div>
      {children}
    </div>
  );
}

export default function RunDetailModal({ run, onClose }) {
  const runId = run?.run_id;
  const taskKey = run?.task_key;

  const [detail, setDetail] = useState(run || {});
  const [detailError, setDetailError] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(Boolean(runId));
  const [task, setTask] = useState(null);
  const [taskError, setTaskError] = useState(null);
  const [taskLoading, setTaskLoading] = useState(Boolean(taskKey));
  const [metrics, setMetrics] = useState([]);
  const [logs, setLogs] = useState([]);
  const [logsLoading, setLogsLoading] = useState(Boolean(runId));
  const [logsError, setLogsError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  const cursorRef = useRef(0);
  const logBoxRef = useRef(null);
  const running = isActive(detail);

  // 运行基本信息：先用列表行数据渲染首屏，再用详情接口结果覆盖
  useEffect(() => {
    if (!runId) return undefined;
    let alive = true;
    setDetail(run || {});
    setDetailError(null);
    setLoadingDetail(true);
    api.get(`/runs/${runId}`)
      .then((d) => { if (alive) setDetail((prev) => ({ ...prev, ...d })); })
      .catch((e) => { if (alive) setDetailError(e.message); })
      .finally(() => { if (alive) setLoadingDetail(false); });
    return () => { alive = false; };
  }, [runId, reloadKey]);

  // 任务上下文：任务不存在/失败时降级，不影响其余区域
  useEffect(() => {
    if (!taskKey) { setTaskLoading(false); return undefined; }
    let alive = true;
    setTask(null); setTaskError(null); setTaskLoading(true);
    api.get(`/tasks/${taskKey}`)
      .then((t) => { if (alive) setTask(t); })
      .catch((e) => { if (alive) setTaskError(e.message); })
      .finally(() => { if (alive) setTaskLoading(false); });
    return () => { alive = false; };
  }, [taskKey, reloadKey]);

  // 运行日志首屏（游标从 0 开始全量）
  useEffect(() => {
    if (!runId) return undefined;
    let alive = true;
    cursorRef.current = 0;
    setLogs([]); setLogsError(null); setLogsLoading(true);
    api.get(`/runs/${runId}/logs`, { since: 0 })
      .then((r) => { if (!alive) return; cursorRef.current = r.cursor ?? 0; setLogs(r.logs || []); })
      .catch((e) => { if (alive) setLogsError(e.message); })
      .finally(() => { if (alive) setLogsLoading(false); });
    return () => { alive = false; };
  }, [runId, reloadKey]);

  // 运行中：按 cursor 增量拉日志 + 刷新运行状态，结束后自动停止
  useEffect(() => {
    if (!runId || !running) return undefined;
    const timer = setInterval(() => {
      api.get(`/runs/${runId}/logs`, { since: cursorRef.current })
        .then((r) => {
          cursorRef.current = r.cursor ?? cursorRef.current;
          if (r.logs?.length) setLogs((prev) => [...prev, ...r.logs]);
        })
        .catch(() => {});
      api.get(`/runs/${runId}`)
        .then((d) => setDetail((prev) => ({ ...prev, ...d })))
        .catch(() => {});
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [runId, running]);

  // 资源与指标：/metrics 无 run_id 过滤，客户端按 run_id 命中
  useEffect(() => {
    if (!taskKey) return undefined;
    let alive = true;
    api.get("/metrics", { task_key: taskKey })
      .then((rows) => { if (alive) setMetrics((rows || []).filter((m) => m.run_id === runId)); })
      .catch(() => {});
    return () => { alive = false; };
  }, [taskKey, runId, reloadKey]);

  // 新日志追加后滚动到底部
  useEffect(() => {
    const box = logBoxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [logs]);

  const metric = metrics.length ? metrics[metrics.length - 1] : null;

  return (
    <Modal title="运行详情" onClose={onClose} width={680} footer={
      <div className="mac-row mac-end" style={{ gap: 8, width: "100%", justifyContent: "flex-end" }}>
        <Button variant="ghost" icon="refresh" onClick={() => setReloadKey((k) => k + 1)} disabled={loadingDetail}>刷新</Button>
        <Button variant="primary" icon="check" onClick={onClose}>关闭</Button>
      </div>
    }>
      {detailError && (
        <Callout tone="danger" icon="alert">运行详情刷新失败：{detailError}（当前展示为已加载内容）</Callout>
      )}

      <div className="mac-row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <Badge tone={STATE_TONE[detail.status] || "neutral"} label={STATE_CN[detail.status] || detail.status || "-"} />
        <span className="mac-chip">{TRIGGER_CN[detail.trigger_via] || detail.trigger_via || "-"}</span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-secondary)", wordBreak: "break-all" }}>{runId}</span>
        {running && <span className="mac-muted" style={{ fontSize: 12 }}>运行中，每 3 秒自动刷新…</span>}
      </div>

      {detail.status === "failed" && detail.error && (
        <Callout tone="danger" icon="alert">
          <div style={{ fontWeight: 600, marginBottom: 2 }}>失败原因</div>
          <div style={{ fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{detail.error}</div>
        </Callout>
      )}

      <Section title="运行信息" hint={loadingDetail ? <Skeleton width={56} height={12} /> : null}>
        <div className="mac-detail-row__inner" style={{ padding: "0 0 var(--sp-2)" }}>
          <Item label="运行实例" value={runId} mono />
          <Item label="任务标识" value={detail.task_key} mono />
          <Item label="所属项目" value={detail.project} />
          <Item label="触发方式" value={TRIGGER_CN[detail.trigger_via] || detail.trigger_via} />
          <Item label="开始时间" value={detail.started_at} />
          <Item label="结束时间" value={detail.finished_at || "进行中"} />
          <Item label="耗时" value={durationText(detail)} />
          <Item label="尝试次数" value={detail.attempt ?? 1} />
          <Item label="写入行数" value={detail.rows_total ?? 0} />
        </div>
      </Section>

      <Section title="任务上下文" hint={taskLoading ? <Skeleton width={56} height={12} /> : null}>
        {taskError ? (
          <Callout tone="warning" icon="alert">任务配置不可获取（可能已被删除）：{taskError}</Callout>
        ) : task ? (
          <div className="mac-detail-row__inner" style={{ padding: "0 0 var(--sp-2)" }}>
            <Item label="任务名称" value={task.name} />
            <Item label="源连接" value={task.source_conn_key} mono />
            <Item label="目标连接" value={task.target_conn_key} mono />
            <Item label="源对象" value={task.source_object} mono />
            <Item label="目标对象" value={task.target_object} mono />
            <Item label="写入模式" value={MODE_CN[task.mode] || task.mode} />
            {task.primary_keys?.length ? <Item label="主键" value={task.primary_keys.join(", ")} mono /> : null}
            {task.incremental?.field ? <Item label="增量字段" value={task.incremental.field} mono /> : null}
            <Item label="调度" value={task.schedule ? `${task.schedule}${task.schedule_enabled === false ? "（已停用）" : ""}` : "未配置"} />
            {task.schedule_next_run ? <Item label="下次运行" value={task.schedule_next_run} /> : null}
          </div>
        ) : (
          <div className="mac-muted">{taskLoading ? "加载中…" : "无任务上下文"}</div>
        )}
      </Section>

      {metric && (
        <Section title="资源与指标">
          <div className="mac-detail-row__inner" style={{ padding: "0 0 var(--sp-2)" }}>
            <Item label="CPU 占用" value={metric.cpu_percent != null ? `${metric.cpu_percent}%` : null} />
            <Item label="内存占用" value={metric.memory_mb != null ? `${metric.memory_mb} MB` : null} />
            <Item label="延迟" value={metric.latency_ms != null ? `${metric.latency_ms} ms` : null} />
            <Item label="执行耗时" value={metric.duration_s != null ? `${metric.duration_s} s` : null} />
            <Item label="数据行数" value={metric.data_rows} />
          </div>
        </Section>
      )}

      <Section title="运行日志" hint={<span className="mac-muted" style={{ fontSize: 12 }}>{logs.length} 条</span>}>
        {logsError ? (
          <Callout tone="danger" icon="alert">
            日志加载失败：{logsError}
            <div style={{ marginTop: 8 }}>
              <Button variant="ghost" icon="refresh" onClick={() => setReloadKey((k) => k + 1)}>重试</Button>
            </div>
          </Callout>
        ) : logs.length === 0 ? (
          <div className="mac-muted" style={{ fontSize: 12 }}>{logsLoading ? "日志加载中…" : "暂无日志"}</div>
        ) : (
          <div ref={logBoxRef} style={{ maxHeight: 220, overflow: "auto", background: "var(--bg-subtle)", border: "1px solid var(--border)", borderRadius: "var(--r-md)", padding: "var(--sp-2) var(--sp-3)" }}>
            <pre style={{ margin: 0, fontSize: 12, lineHeight: 1.7, fontFamily: "var(--font-mono)", color: "var(--text-secondary)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {logs.map((l, i) => `${l.ts} [${(l.level || "info").toUpperCase()}] ${l.line}`).join("\n")}
            </pre>
          </div>
        )}
      </Section>
    </Modal>
  );
}
