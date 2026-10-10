import { useEffect, useState } from "react";
import { Badge, Button, Callout, Card, Empty, Input, Modal, Row, Select, Table } from "../components/ui";
import { api } from "../api/client";

// 常用频率预设（与 ETL 任务表单一致）：选择预设即生成 cron，custom 走自定义输入
const CRON_PRESETS = [
  ["", "手动（不调度）"], ["*/10 * * * *", "每 10 分钟"], ["0 * * * *", "每小时"],
  ["0 2 * * *", "每天凌晨 2 点"], ["0 2 * * 1", "每周一凌晨 2 点"], ["custom", "自定义 cron…"],
];

// 简单校验一个 5 字段 cron 是否合法（非空 + 恰好 5 段，空视为手动不校验）
function validCron(s) {
  if (!s || !s.trim()) return true;
  return s.trim().split(/\s+/).length === 5;
}

export default function Schedules() {
  const [items, setItems] = useState([]);
  const [error, setError] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busyKey, setBusyKey] = useState(null);
  const [editing, setEditing] = useState(null);
  const [cronPick, setCronPick] = useState("");
  const [cronDraft, setCronDraft] = useState("");

  const load = () => {
    api.get("/schedules")
      .then((rows) => { setItems(rows || []); setError(null); })
      .catch((e) => setError(e.message));
  };
  useEffect(load, []);

  const toggle = async (s, target) => {
    setBusyKey(s.task_key); setMsg(null);
    try {
      await api.patch(`/schedules/${s.task_key}`, { enabled: target });
      setMsg({ tone: "success", text: `已${target ? "启用" : "停用"}「${s.name}」` });
      load();
    } catch (e) { setMsg({ tone: "danger", text: `操作失败：${e.message}` }); }
    finally { setBusyKey(null); }
  };

  const openCronEditor = (s) => {
    const matched = CRON_PRESETS.some(([v]) => v && v !== "custom" && v === s.schedule);
    setEditing(s);
    setCronPick(matched ? s.schedule : (s.schedule ? "custom" : ""));
    setCronDraft(s.schedule || "");
  };

  const applyCron = async () => {
    const next = cronDraft.trim();
    if (next && next.split(/\s+/).length !== 5) { setMsg({ tone: "danger", text: "cron 需为 5 字段（分 时 日 月 周）" }); return; }
    setBusyKey(editing.task_key); setMsg(null);
    try {
      await api.patch(`/schedules/${editing.task_key}`, { schedule: next });
      setMsg({ tone: "success", text: `已更新「${editing.name}」的 cron` });
      setEditing(null);
      load();
    } catch (e) { setMsg({ tone: "danger", text: `更新失败：${e.message}` }); }
    finally { setBusyKey(null); }
  };

  return (
    <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <div className="mac-row mac-end" style={{ justifyContent: "space-between" }}>
        <div><h1 className="mac-h1">定时任务</h1><div className="mac-muted">展示/启停各任务的 cron 计划与上次、下次运行时间</div></div>
        <Row>
          <Button variant="ghost" icon="refresh" onClick={load}>刷新</Button>
          <Button variant="primary" icon="plus" onClick={() => { setError("请在「ETL 任务」中新建并配置定时。"); }}>新建</Button>
        </Row>
      </div>

      {error && <div className="mac-callout mac-callout--warning">{String(error)}</div>}
      {msg && <Callout tone={msg.tone}>{msg.text}</Callout>}

      <Card solid>
        {items.length === 0
          ? <Empty icon="time" title="暂无定时任务" desc="至少为某任务配置 cron 后，此处即列出其调度计划" />
          : <Table head={["任务", "Cron", "启用", "上次运行", "下次运行", "操作"]}>
              {items.map((s) => (
                <tr key={s.task_key}>
                  <td>
                    <span style={{ fontWeight: 500 }}>{s.name}</span>
                    <div className="mac-muted" style={{ fontSize: 11 }}>{s.project}</div>
                  </td>
                  <td><code style={{ fontSize: 12 }}>{s.schedule}</code></td>
                  <td>
                    <Badge dot tone={s.schedule_enabled ? "success" : "neutral"} label={s.schedule_enabled ? "已启用" : "已停用"} />
                  </td>
                  <td className="num" style={{ fontSize: 12 }}>{s.last_run_at || "-"}</td>
                  <td className="num" style={{ fontSize: 12 }}>{s.schedule_next_run || "-"}</td>
                  <td>
                    <Row style={{ gap: 4 }}>
                      {s.schedule_enabled
                        ? <Button variant="ghost" disabled={busyKey === s.task_key} onClick={() => toggle(s, false)} style={{ padding: 6 }} title="停用调度">停用</Button>
                        : <Button variant="primary" disabled={busyKey === s.task_key} onClick={() => toggle(s, true)} style={{ padding: 6 }} title="启用调度">启用</Button>}
                      <Button variant="ghost" icon="edit" onClick={() => openCronEditor(s)} style={{ padding: 6 }} title="修改 cron">改cron</Button>
                    </Row>
                  </td>
                </tr>
              ))}
            </Table>}
      </Card>

      {editing && (
        <Modal title={`修改 cron：${editing.name}`} onClose={() => setEditing(null)}>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
            <div>
              <span className="mac-label">常用频率</span>
              <Select
                value={cronPick}
                onChange={(e) => {
                  const v = e.target.value;
                  setCronPick(v);
                  if (v !== "custom") setCronDraft(v);
                }}
              >
                {CRON_PRESETS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
              </Select>
            </div>
            {cronPick === "custom" && (
              <div>
                <span className="mac-label">自定义 cron（分 时 日 月 周）</span>
                <Input value={cronDraft} onChange={(e) => setCronDraft(e.target.value)} placeholder="0 2 * * *" />
              </div>
            )}
          </div>
          <footer className="mac-modal__foot">
            <div className="mac-row mac-end" style={{ gap: 8, justifyContent: "flex-end" }}>
              <Button variant="ghost" onClick={() => setEditing(null)} disabled={busyKey === editing.task_key}>取消</Button>
              <Button variant="primary" icon="check" onClick={applyCron} disabled={busyKey === editing.task_key}>{busyKey === editing.task_key ? "处理中…" : "确定"}</Button>
            </div>
          </footer>
        </Modal>
      )}
    </div>
  );
}