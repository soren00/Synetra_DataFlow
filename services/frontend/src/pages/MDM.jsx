import { Fragment, useEffect, useState } from "react";
import { api } from "../api/client";
import { Badge, Button, Callout, Card, Empty, Input, Modal, ModalFooter, Table, Textarea } from "../components/ui";

const SYNC_TONE = { success: "success", warning: "warning" };

export default function MDM() {
  const [open, setOpen] = useState(null);
  const [models, setModels] = useState([]);
  const [error, setError] = useState(null);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [form, setForm] = useState({ entity: "", label: "", fields: "id,int,required,unique" });

  const load = () => { api.get("/mdm/models").then(setModels).catch((e) => setError(e.message)); };
  useEffect(load, []);

  const submit = async () => {
    setBusy(true); setMsg(null);
    const fields = form.fields.split("\n").map((r) => r.trim()).filter(Boolean).map((r) => {
      const [name, type = "string", ...flags] = r.split(",").map((s) => s.trim());
      const f = { name, type: type || "string", required: false, unique: false, format: null };
      for (const flag of flags) {
        if (flag === "required") f.required = true;
        else if (flag === "unique") f.unique = true;
        else if (flag.startsWith("format=")) f.format = flag.slice(7);
      }
      return f;
    });
    try {
      await api.post("/mdm/models", { entity: form.entity, label: form.label, fields });
      setShow(false); load();
    } catch (e) { setMsg({ tone: "danger", text: `创建失败：${e.message}` }); }
    finally { setBusy(false); }
  };

  return (
    <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <div className="mac-row mac-end" style={{ justifyContent: "space-between" }}>
        <div><h1 className="mac-h1">主数据管理</h1><div className="mac-muted">模型定义 · 记录维护 · 质量校验 · 同步分发</div></div>
        <Button variant="primary" icon="plus" onClick={() => { setForm({ entity: "", label: "", fields: "id,int,required,unique" }); setMsg(null); setShow(true); }}>定义实体</Button>
      </div>

      {error && <div className="mac-callout mac-callout--warning">后端不可达（{error}）。</div>}

      <Card solid title="主数据实体">
        <Table head={["实体", "标签", "字段数", "同步状态", ""]}>
          {models.length === 0
            ? <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-tertiary)" }}>暂无主数据实体，点击右上角「定义实体」</td></tr>
            : models.map((m) => {
                const entity = m.entity;
                const sync = m.sync || "success";
                return (
                  <Fragment key={entity}>
                    <tr>
                      <td className="num" style={{ fontWeight: 600 }}>{entity}</td>
                      <td>{m.label || "-"}</td>
                      <td className="num">{m.fields?.length ?? "-"}</td>
                      <td><Badge tone={SYNC_TONE[sync] || "neutral"} label={sync === "success" ? "正常" : "告警"} /></td>
                      <td>
                        <Button variant="ghost" icon="chevron"
                          className={`mac-btn--chevron${open === entity ? " mac-btn--open" : ""}`}
                          onClick={() => setOpen(open === entity ? null : entity)}
                          style={{ padding: 6 }} aria-label="展开详情" aria-expanded={open === entity} />
                      </td>
                    </tr>
                    {open === entity && (
                      <tr className="mac-detail-row">
                        <td colSpan={5}>
                          <div className="mac-detail-row__inner">
                            {[{ label: "实体标识", value: entity }, { label: "字段数", value: String(m.fields?.length ?? 0) },
                              { label: "质量规则", value: (m.fields || []).filter((x) => x.required || x.unique).length + " 条" },
                              { label: "同步状态", value: sync === "success" ? "正常" : "告警" }]
                              .map((it) => (
                                <div key={it.label} className="mac-detail__item">
                                  <span className="mac-detail__label">{it.label}</span>
                                  <span className="mac-detail__value">{it.value}</span>
                                </div>
                              ))}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
        </Table>
      </Card>

      {show && (
        <Modal title="定义实体" onClose={() => setShow(false)} width={560} footer={
          <ModalFooter onCancel={() => setShow(false)} onOk={submit} busy={busy} okText="创建实体" />
        }>
          {msg && <Callout tone={msg.tone}>{msg.text}</Callout>}
          <div className="mac-field"><span className="mac-label">实体标识</span>
            <Input value={form.entity} onChange={(e) => setForm({ ...form, entity: e.target.value })} placeholder="customer" /></div>
          <div className="mac-field"><span className="mac-label">中文标签</span>
            <Input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} placeholder="客户" /></div>
          <div className="mac-field"><span className="mac-label">字段定义（每行：名,类型,规则,规则…）</span>
            <Textarea rows={6} value={form.fields} onChange={(e) => setForm({ ...form, fields: e.target.value })}
              placeholder={"id,int,required,unique\nemail,string,unique,format=email\nname,string,required"} />
            <span className="mac-muted" style={{ fontSize: 12 }}>规则：required / unique / format=email|phone|date</span></div>
        </Modal>
      )}
    </div>
  );
}