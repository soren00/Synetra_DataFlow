import { useEffect, useState } from "react";
import { Badge, Button, Callout, Card, ConfirmDialog, Empty, Icon, Input, Modal, ModalFooter, Row, Segmented, Select, Table, Textarea } from "../components/ui";
import { api } from "../api/client";

const STATE_TONE = { success: "success", running: "info", failed: "danger", retrying: "warning", waiting: "warning" };
const STATE_CN = { success: "成功", running: "运行中", failed: "失败", retrying: "重试中", waiting: "等待重试", unscheduled: "未调度" };
const MODE_OPT = [["append", "全量"], ["incremental", "增量+水位"], ["upsert", "主键 Upsert"]];
const FILTER = [{ value: "all", label: "全部" }, { value: "success", label: "成功" }, { value: "running", label: "运行中" }, { value: "failed", label: "失败" }];
const FIRST_RUN_OPT = [["all", "首跑全量"], ["incremental", "首跑仅增量"]];
const SCHED_OPTIONS = [
  ["", "手动（不调度）"], ["*/10 * * * *", "每 10 分钟"], ["0 * * * *", "每小时"],
  ["0 2 * * *", "每天凌晨 2 点"], ["0 2 * * 1", "每周一凌晨 2 点"], ["custom", "自定义 cron…"],
];
// 规则类型说明（任务 4：转换规则可理解性）
const KIND_CN = { direct: "直连", constant: "常量", null_fill: "空值填充", cast: "类型转换", expression: "表达式" };
const KIND_HELP = {
  direct: "原样把源字段拷贝到目标字段。",
  constant: "目标字段固定写入给定常量值，与源字段无关。",
  null_fill: "源值为空（null）时，填入给定的默认填充值。",
  cast: "把源值转换为指定类型（如 int / float / str）。",
  expression: "用受限表达式对源字段求值后写入，支持源列名引用、切片与白名单函数。",
};

// 受限表达式的可用函数（与后端权威白名单同源；一致性由 tests/test_frontend_expr_help.py 守门，
// 改动此列表后必须同步 app/expr/whitelist.py，否则测试失败）
const EXPR_FUNCTIONS = [
  "str", "int", "float", "bool", "len", "abs", "round", "min", "max", "sum",
  "lower", "upper", "strip", "replace", "concat", "cast",
];
const EXPR_HELP =
  `可用函数（必须小写）：${EXPR_FUNCTIONS.join("、")}。` +
  "字段引用：直接写源列名（如 name、amount），不要写 row['name']。" +
  "取子串用切片：name[0]、phone[0:3]、phone[-4:]。" +
  "没有 SQL 写法：CONCAT / LEFT / RIGHT / SUBSTRING / COALESCE / TRIM / LENGTH / CASE WHEN / CAST AS 一律不可用。" +
  "示例：concat(id,'-',name)、round(amount,2)、lower(email)、concat(phone[0:3],'****',phone[7:])。";

// 解析后端保存时的字段级校验详情（形如「字段[email]: 表达式禁止属性访问(Attribute); …」）
function parseFieldErrors(detail) {
  const out = {};
  String(detail || "").split("; ").forEach((part) => {
    const m = part.match(/^字段\[(.+?)\]:\s*(.+)$/);
    if (m) out[m[1]] = m[2];
  });
  return out;
}

// 简单校验一个 5 字段 cron 是否合法（非空 + 恰好 5 段，空视为手动不校验）
function validCron(s) {
  if (!s || !s.trim()) return true;
  return s.trim().split(/\s+/).length === 5;
}

// 单元格取值：位置数组按下标，对象行按列名；空值显示空串而非统一横线
function cellVal(row, col, idx) {
  const v = Array.isArray(row) ? row[idx] : row[col];
  return typeof v === "undefined" ? "" : v;
}

/* 项目内任务列表：项目归属由上级（项目清单页）注入，表单不提供项目自由输入。 */
export default function Tasks({ project, onBack }) {
  const activeProject = project || "default";
  const [f, setF] = useState("all");
  const [q, setQ] = useState("");
  const [tasks, setTasks] = useState([]);
  const [runs, setRuns] = useState([]);
  const [conns, setConns] = useState([]);
  const [error, setError] = useState(null);
  const [show, setShow] = useState(false);
  const [editKey, setEditKey] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [runMsg, setRunMsg] = useState(null);
  const [detailTask, setDetailTask] = useState(null);
  const [form, setForm] = useState({
    name: "", source_conn_key: "", target_conn_key: "",
    source_object: "", target_object: "", mode: "upsert", primary_keys: "id",
    incremental_field: "updated_at", watermark_field: "updated_at", first_run_policy: "all", schedule: "",
  });
  const [fields, setFields] = useState([]);
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewMsg, setPreviewMsg] = useState(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [schedPick, setSchedPick] = useState("");
  const [confirmDel, setConfirmDel] = useState(null);
  const [fieldErrors, setFieldErrors] = useState({});  // 后端字段级校验详情（字段名 → 原因）
  // AI 生成表达式：{ index, prompt, busy, msg }，仅对当前正在生成的那条规则生效
  const [ai, setAi] = useState(null);

  const load = () => {
    // 任务与运行信息按当前项目加载，实现项目间隔离展示；连接保持全局（不做项目过滤）
    Promise.all([
      api.get("/tasks", { project: activeProject }),
      api.get("/runs", { page_size: 50, project: activeProject }),
      api.get("/connections"),
    ])
      .then(([ts, rs, cs]) => { setTasks(ts); setRuns(rs.runs || []); setConns(cs || []); setError(null); })
      .catch((e) => setError(e.message));
  };
  useEffect(load, [activeProject]);

  const sources = conns;
  const targets = conns;
  const srcConn = conns.find((c) => c.conn_key === form.source_conn_key);

  const doPreview = async () => {
    if (!form.source_conn_key || !form.source_object) { setPreviewMsg({ tone: "warning", text: "请先选择数据源并填写源对象" }); setPreviewOpen(true); return; }
    setPreviewing(true); setPreviewMsg(null); setPreview(null);
    try {
      let columns = [], rows = [];
      if (["mysql", "postgresql", "oracle", "sqlserver", "hive", "clickhouse"].includes(srcConn?.ctype)) {
        const sql = `SELECT * FROM ${form.source_object} LIMIT 10`;
        const r = await api.post(`/connections/${form.source_conn_key}/query`, { project: activeProject, sql, limit: 10 });
        columns = r.columns || []; rows = r.rows || [];
      } else if (srcConn?.ctype === "api") {
        const r = await api.post(`/connections/${form.source_conn_key}/sample`);
        const arr = r.records || r.rows || (Array.isArray(r) ? r : r.data);
        if (Array.isArray(arr) && arr[0]) { columns = Object.keys(arr[0]); rows = arr; }
      } else {
        setPreviewMsg({ tone: "warning", text: `类型 ${srcConn?.ctype} 暂不支持在线预览，请在下方手动配置字段转换。` });
      }
      setPreview({ columns, rows });
      if (columns.length && fields.length === 0) setFields(columns.map((c) => ({ source: c, target: c, kind: "direct" })));
      if (!columns.length && !rows.length && srcConn?.ctype !== "csv") setPreviewMsg((p) => p || { tone: "warning", text: "未取到样例数据（可能连接不可达或无数据）。" });
      setPreviewOpen(true);
    } catch (e) { setPreviewMsg({ tone: "danger", text: `预览失败：${e.message}` }); setPreviewOpen(true); }
    finally { setPreviewing(false); }
  };

  const setRule = (i, k, v) => setFields((fs) => fs.map((fsx, j) => (j === i ? { ...fsx, [k]: v } : fsx)));

  // AI 生成表达式：只回填建议，不自动保存；失败保留用户已填内容
  const runAiExpression = async () => {
    if (!ai || !ai.prompt.trim() || ai.busy) return;
    const i = ai.index;
    const rule = fields[i] || {};
    const columns = (preview?.columns || []).filter(Boolean);
    setAi((s) => ({ ...s, busy: true, msg: null }));
    try {
      const r = await api.post("/llm/expression", {
        prompt: ai.prompt.trim(),
        source_field: rule.source || "",
        target_field: rule.target || rule.source || "",
        columns,
      });
      if (r.ok) {
        setRule(i, "expression", r.expression);
        setAi((s) => ({ ...s, busy: false, msg: { tone: "success", text: `已生成并通过白名单校验：${r.expression}` } }));
      } else {
        setAi((s) => ({
          ...s, busy: false,
          msg: { tone: "danger", text: `生成未通过校验：${(r.issues || []).join("；")}（已保留你填写的表达式）` },
        }));
      }
    } catch (e) {
      // 未配置模型为 503，提示即服务端引导文案；不修改用户已填内容
      setAi((s) => ({ ...s, busy: false, msg: { tone: "danger", text: `生成失败：${e.message}` } }));
    }
  };

  const setSchedule = (v) => {
    setSchedPick(v);
    if (v === "custom") return; // 保留当前自定义文本
    setForm((frm) => ({ ...frm, schedule: v }));
  };

  const matchSchedPick = (s) => SCHED_OPTIONS.find(([v]) => v && v !== "custom" && v === s);
  const openEdit = (t) => {
    const inc = t.incremental || {};
    setForm({
      name: t.name,
      source_conn_key: t.source_conn_key || "", target_conn_key: t.target_conn_key || "",
      source_object: t.source_object || "", target_object: t.target_object || "",
      mode: t.mode || "append", primary_keys: (t.primary_keys || []).join(","),
      incremental_field: inc.field || "", watermark_field: inc.watermark_field || "",
      first_run_policy: inc.first_run_policy || "all", schedule: t.schedule || "",
    });
    setFields((t.fields || []).map((x) => ({ ...x })));
    setSchedPick(matchSchedPick(t.schedule) ? t.schedule : (t.schedule ? "custom" : ""));
    setEditKey(t.task_key); setMsg(null); setShow(true);
  };

  const submit = async () => {
    setBusy(true); setMsg(null); setFieldErrors({});
    if (!validCron(form.schedule)) { setMsg({ tone: "danger", text: "自定义 cron 需为 5 段（分 时 日 月 周），例如 0 2 * * *" }); setBusy(false); return; }
    const cfg = {
      project: activeProject, name: form.name,
      source_conn_key: form.source_conn_key, target_conn_key: form.target_conn_key,
      source_object: form.source_object, target_object: form.target_object,
      mode: form.mode,
      primary_keys: form.mode === "upsert" ? form.primary_keys.split(",").map((s) => s.trim()).filter(Boolean) : [],
      incremental: form.mode === "incremental"
        ? { field: form.incremental_field, watermark_field: form.watermark_field, first_run_policy: form.first_run_policy }
        : { field: "", watermark_field: "", first_run_policy: "all" },
      fields: fields.filter((x) => x.source || x.constant_value || x.expression).map((x) => ({
        source: x.source, target: x.target || x.source, kind: x.kind,
        cast_to: x.cast_to || null, null_default: x.null_default || null,
        constant_value: x.constant_value || null, expression: x.expression || null,
      })),
      schedule: form.schedule,
    };
    try {
      await api.post("/tasks", { project: activeProject, name: form.name, config: cfg, task_key: editKey || null });
      setShow(false); setEditKey(null); load();
    } catch (e) {
      // 字段级校验详情（如表达式违规）就近展示在对应规则行，避免被通用提示吞掉
      setFieldErrors(parseFieldErrors(e.message));
      setMsg({ tone: "danger", text: `${editKey ? "保存" : "新建"}失败：${e.message}` });
    }
    finally { setBusy(false); }
  };

  const runTask = async (tk) => {
    setRunMsg(null);
    try {
      const r = await api.post(`/tasks/${tk}/run`, {});
      setRunMsg({ task: tk, status: r.status, detail: r.detail || r.message || (r.status === "failed" ? "执行失败，详情见运行日志" : "") });
      load();
    } catch (e) { setRunMsg({ task: tk, status: "failed", detail: e.message }); }
  };

  const deleteTask = async (t) => {
    setBusy(true); setMsg(null);
    try {
      await api.del(`/tasks/${t.task_key}`);
      // 若被删除的正是当前展示详情的任务，清空相关状态
      if (detailTask?.name === t.name) setDetailTask(null);
      setConfirmDel(null);
      load();
    } catch (e) { setMsg({ tone: "danger", text: `删除失败：${e.message}` }); setConfirmDel(null); }
    finally { setBusy(false); }
  };

  const rowFor = (tk) => runs.find((x) => x.task_key === tk);
  const runsFor = (tk) => runs.filter((x) => x.task_key === tk);
  const list = tasks.filter((t) => {
    const status = rowFor(t.task_key)?.status || "unscheduled";
    return (f === "all" ? true : status === f) && (t.name || "").includes(q);
  });

  return (
    <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <div className="mac-row mac-end" style={{ justifyContent: "space-between" }}>
        <div>
          <Row style={{ gap: 8, alignItems: "center" }}>
            {onBack && (
              <Button variant="ghost" onClick={onBack} title="返回项目清单">
                <Icon name="chevronLeft" size={14} />
                返回项目
              </Button>
            )}
            <h1 className="mac-h1">{activeProject}</h1>
          </Row>
          <div className="mac-muted">项目内 ETL 任务 · 选择源/目标、增量水位、主键 upsert · 共 {tasks.length} 个任务</div>
        </div>
        <Row>
          <Button variant="ghost" icon="refresh" onClick={load}>刷新</Button>
          <Button variant="primary" icon="plus" onClick={() => {
            setEditKey(null);
            setForm({ ...form, name: "", source_conn_key: sources[0]?.conn_key || "", target_conn_key: targets[0]?.conn_key || "" });
            setFields([]); setSchedPick(""); setMsg(null); setShow(true);
          }}>新建任务</Button>
        </Row>
      </div>

      {error && <div className="mac-callout mac-callout--warning">后端不可达（{error}）。</div>}
      {msg && !show && <Callout tone={msg.tone}>{msg.text}</Callout>}
      {runMsg && <Callout tone={runMsg.status === "success" ? "info" : "warning"}>
        <b>任务 {runMsg.task.slice(0, 8)}：</b>运行状态 {STATE_CN[runMsg.status] || runMsg.status}。{runMsg.detail || ""}
      </Callout>}
      {detailTask && <Callout tone="info">
        <b>最近运行明细 {detailTask.name}：</b>状态 {STATE_CN[detailTask.run?.status] || "-"} · 触发 {detailTask.run?.trigger_via || "-"} ·
        行数 {detailTask.run?.rows_total ?? "-"} · 开始 {detailTask.run?.started_at || "-"} · 完成 {detailTask.run?.finished_at || "-"} ·
        错误 {detailTask.run?.error || "无"}
      </Callout>}

      <Card solid>
        <Row className="mac-end">
          <Segmented items={FILTER} value={f} onChange={setF} />
          <div style={{ flex: 1 }} />
          <Input placeholder="搜索任务…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 220 }} />
        </Row>
        <div style={{ height: 14 }} />
        {tasks.length === 0
          ? <Empty icon="tasks" title="暂无任务" desc="点击右上角「新建任务」，选择源/目标连接与抽取模式" />
          : <Table head={["任务", "源 → 目标", "模式", "状态", "触发", "行数", "操作"]}>
              {list.map((t) => {
                const run = rowFor(t.task_key);
                const status = run?.status || "unscheduled";
                const modeCN = Object.fromEntries(MODE_OPT)[(t.mode || "").toLowerCase()] || t.mode || "全量";
                return (
                  <tr key={t.task_key}>
                    <td><span style={{ fontWeight: 500 }}>{t.name}</span></td>
                    <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>
                      {String(t.source_object)} <Icon name="chevron" size={12} style={{ verticalAlign: -2 }} /> {String(t.target_object)}
                    </td>
                    <td><span className="mac-chip">{modeCN}</span></td>
                    <td><Badge tone={STATE_TONE[status] || "neutral"} label={STATE_CN[status] || status} /></td>
                    <td><span className="mac-chip">{t.schedule ? "定时" : "手动"}</span></td>
                    <td className="num">{run?.rows_total ?? "-"}</td>
                    <td>
                      <Row style={{ gap: 4 }}>
                        <Button variant="ghost" icon="play" onClick={() => runTask(t.task_key)} style={{ padding: 6 }} title="触发运行">运行</Button>
                        <Button variant="ghost" icon="edit" onClick={() => openEdit(t)} style={{ padding: 6 }} title="编辑任务">编辑</Button>
                        <Button variant="ghost" icon="eye" onClick={() => setDetailTask({ name: t.name, run: runsFor(t.task_key)[0] || {} })} style={{ padding: 6 }} title="查看最近一次运行明细">明细</Button>
                        <Button variant="ghost" icon="trash" onClick={() => { setMsg(null); setConfirmDel(t); }} style={{ padding: 6, color: "var(--danger)" }} title="删除任务">删除</Button>
                      </Row>
                    </td>
                  </tr>
                );
              })}
            </Table>}
      </Card>

      {confirmDel && (
        <ConfirmDialog
          title="删除任务"
          message={<span>确认删除任务 <b>{confirmDel.name}</b>？此操作不可撤销。</span>}
          okText="删除"
          busy={busy}
          onOk={() => deleteTask(confirmDel)}
          onCancel={() => { setConfirmDel(null); setMsg(null); }}
        />
      )}

      {show && (
        <Modal title={editKey ? "编辑任务" : "新建任务"} onClose={() => { setShow(false); setEditKey(null); setMsg(null); }} width={620} footer={
          <ModalFooter onCancel={() => { setShow(false); setEditKey(null); setMsg(null); }} onOk={submit} busy={busy} okText={editKey ? "保存修改" : "创建任务"} />
        }>
          {msg && <Callout tone={msg.tone}>{msg.text}</Callout>}
          <div className="mac-field">
            <span className="mac-label">任务名称</span>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="如 订单增量同步" />
          </div>
          <Row style={{ gap: 10 }}>
            <div className="mac-field" style={{ flex: 1 }}>
              <span className="mac-label">数据源</span>
              <Select value={form.source_conn_key} onChange={(e) => setForm({ ...form, source_conn_key: e.target.value })}>
                {sources.length === 0 && <option value="">（无可用的数据源，请先新建）</option>}
                {sources.map((s) => <option key={s.conn_key} value={s.conn_key}>{s.name}</option>)}
              </Select>
            </div>
            <div className="mac-field" style={{ flex: 1 }}>
              <span className="mac-label">源对象</span>
              <Input value={form.source_object} onChange={(e) => setForm({ ...form, source_object: e.target.value })} placeholder="orders" />
            </div>
          </Row>
          <Row style={{ gap: 10 }}>
            <div className="mac-field" style={{ flex: 1 }}>
              <span className="mac-label">数据目标</span>
              <Select value={form.target_conn_key} onChange={(e) => setForm({ ...form, target_conn_key: e.target.value })}>
                {targets.length === 0 && <option value="">（无可用的目标，请先新建）</option>}
                {targets.map((t) => <option key={t.conn_key} value={t.conn_key}>{t.name}</option>)}
              </Select>
            </div>
            <div className="mac-field" style={{ flex: 1 }}>
              <span className="mac-label">目标对象</span>
              <Input value={form.target_object} onChange={(e) => setForm({ ...form, target_object: e.target.value })} placeholder="orders_dw" />
            </div>
          </Row>

          <Row className="mac-end" style={{ gap: 8, alignItems: "center" }}>
            <Button variant="ghost" icon="eye" onClick={doPreview} disabled={previewing}>{previewing ? "预览中…" : "查询预览 10 条"}</Button>
          </Row>

          <Row className="mac-end" style={{ gap: 8, alignItems: "center", marginTop: 4 }}>
            <span className="mac-label" style={{ margin: 0 }}>字段转换</span>
            <div style={{ flex: 1 }} />
            <Button variant="ghost" onClick={() => setFields((fs) => [...fs, { source: "", target: "", kind: "direct" }])}>添加规则</Button>
          </Row>
          {fields.length === 0
            ? <span className="mac-muted" style={{ fontSize: 12 }}>未配置字段映射。点「查询预览」后自动按源列生成直连映射。</span>
            : <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {fields.map((r, i) => (
                  <div key={i} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {/* 第一行：字段选择与规则类型（表达式输入不在此行，见下） */}
                    <Row style={{ gap: 6, alignItems: "center" }}>
                      <Input value={r.source} onChange={(e) => setRule(i, "source", e.target.value)} placeholder="源字段" style={{ width: 130 }} />
                      <Select value={r.kind} onChange={(e) => setRule(i, "kind", e.target.value)} style={{ width: 106 }}>
                        {Object.entries(KIND_CN).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </Select>
                      <Input value={r.target} onChange={(e) => setRule(i, "target", e.target.value)} placeholder="目标字段" style={{ width: 130 }} />
                      {r.kind === "cast" && <Input value={r.cast_to || ""} onChange={(e) => setRule(i, "cast_to", e.target.value)} placeholder="类型" style={{ width: 90 }} />}
                      {r.kind === "null_fill" && <Input value={r.null_default || ""} onChange={(e) => setRule(i, "null_default", e.target.value)} placeholder="填充值" style={{ width: 100 }} />}
                      {r.kind === "constant" && <Input value={r.constant_value || ""} onChange={(e) => setRule(i, "constant_value", e.target.value)} placeholder="常量值" style={{ width: 100 }} />}
                      <Button variant="ghost" style={{ color: "var(--danger)", marginLeft: "auto" }} onClick={() => setFields((fs) => fs.filter((_, j) => j !== i))}>删除</Button>
                    </Row>
                    {/* 第二行：表达式独占一行，获得完整输入宽度 */}
                    {r.kind === "expression" && (
                      <Row style={{ gap: 6, alignItems: "center", flexWrap: "nowrap" }}>
                        <Input value={r.expression || ""} onChange={(e) => setRule(i, "expression", e.target.value)}
                          placeholder="表达式" style={{ flex: 1, minWidth: 0 }} />
                        <Button variant="ghost" icon="ai"
                          onClick={() => setAi({ index: i, prompt: "", busy: false, msg: null })}
                          title="按提示词生成表达式">AI 生成</Button>
                      </Row>
                    )}
                    {r.kind === "expression" && (
                      <span className="mac-muted" style={{ fontSize: 11, lineHeight: 1.5, display: "block", wordBreak: "break-word" }}>
                        {EXPR_HELP}
                      </span>
                    )}
                    {fieldErrors[r.target || r.source] && (
                      <span style={{ fontSize: 11, color: "var(--danger)", display: "block", wordBreak: "break-word" }}>
                        表达式校验未通过：{fieldErrors[r.target || r.source]}
                      </span>
                    )}
                  </div>
                ))}
              </div>}

          <div className="mac-field">
            <span className="mac-label">抽取模式</span>
            <Select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}>
              {MODE_OPT.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </div>
          {form.mode === "incremental" && (
            <Row style={{ gap: 10 }}>
              <div className="mac-field" style={{ flex: 1 }}>
                <span className="mac-label">增量字段</span>
                <Input value={form.incremental_field} onChange={(e) => setForm({ ...form, incremental_field: e.target.value })} placeholder="updated_at" />
              </div>
              <div className="mac-field" style={{ flex: 1 }}>
                <span className="mac-label">水位字段</span>
                <Input value={form.watermark_field} onChange={(e) => setForm({ ...form, watermark_field: e.target.value })} placeholder="updated_at" />
              </div>
            </Row>
          )}
          {form.mode === "incremental" && (
            <div className="mac-field">
              <span className="mac-label">首跑策略</span>
              <Select value={form.first_run_policy} onChange={(e) => setForm({ ...form, first_run_policy: e.target.value })}>
                {FIRST_RUN_OPT.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </Select>
            </div>
          )}
          {form.mode === "upsert" && (
            <div className="mac-field">
              <span className="mac-label">主键字段（可多值，逗号分隔）</span>
              <Input value={form.primary_keys} onChange={(e) => setForm({ ...form, primary_keys: e.target.value })} placeholder="id" />
            </div>
          )}

          <div className="mac-field">
            <span className="mac-label">定时频率</span>
            <Select value={schedPick} onChange={(e) => setSchedule(e.target.value)}>
              {SCHED_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </div>
          {schedPick === "custom" && (
            <div className="mac-field">
              <span className="mac-label">自定义 cron（分 时 日 月 周，留空为手动触发）</span>
              <Input value={form.schedule} onChange={(e) => setForm({ ...form, schedule: e.target.value })} placeholder="0 2 * * *" />
            </div>
          )}
        </Modal>
      )}

      {previewOpen && (
        <Modal title="数据预览" onClose={() => setPreviewOpen(false)} width={680}>
          {previewMsg && <Callout tone={previewMsg.tone} style={{ padding: 8 }}>{previewMsg.text}</Callout>}
          {preview && preview.columns.length > 0 ? (
            <div className="mac-scroll" style={{ maxHeight: 360, overflow: "auto", border: "1px solid var(--border)", borderRadius: "var(--r-lg)" }}>
              <Table head={preview.columns}>
                {preview.rows.map((row, i) => (
                  <tr key={i}>
                    {preview.columns.map((c, j) => <td key={c} className="num">{String(cellVal(row, c, j))}</td>)}
                  </tr>
                ))}
              </Table>
            </div>
          ) : <span className="mac-muted">暂无样例数据。</span>}
          <Row style={{ marginTop: 12, justifyContent: "flex-end" }}>
            <Button variant="primary" onClick={() => setPreviewOpen(false)}>关闭</Button>
          </Row>
        </Modal>
      )}

      {ai && (
        <Modal title="AI 生成表达式" onClose={() => setAi(null)} width={540}
          footer={
            <ModalFooter onCancel={() => setAi(null)} onOk={runAiExpression} busy={ai.busy}
              okText="生成" cancelText="关闭" />
          }>
          {ai.msg && <Callout tone={ai.msg.tone}>{ai.msg.text}</Callout>}
          <div className="mac-field">
            <span className="mac-label">字段上下文</span>
            <span className="mac-muted" style={{ fontSize: 12 }}>
              源字段：{fields[ai.index]?.source || "（未填）"} · 目标字段：
              {fields[ai.index]?.target || fields[ai.index]?.source || "（未填）"}
            </span>
            <span className="mac-muted" style={{ fontSize: 11, wordBreak: "break-word" }}>
              可引用源列：{(preview?.columns || []).join("、") || "（点「查询预览 10 条」后自动带出）"}
            </span>
          </div>
          <div className="mac-field">
            <span className="mac-label">需求提示词</span>
            <Textarea rows={3} value={ai.prompt} autoFocus
              onChange={(e) => setAi((s) => ({ ...s, prompt: e.target.value }))}
              placeholder="如：手机号中间四位打码" />
          </div>
          <span className="mac-muted" style={{ fontSize: 11 }}>
            生成结果只回填到表达式输入框，保存时仍走白名单校验；不会自动保存任务。
          </span>
        </Modal>
      )}
    </div>
  );
}