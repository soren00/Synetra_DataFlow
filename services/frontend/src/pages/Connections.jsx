import { Fragment, useEffect, useState } from "react";
import { Badge, Button, Callout, Card, ConfirmDialog, Empty, Icon, Input, Modal, ModalFooter, Row, Select, Table } from "../components/ui";
import { api } from "../api/client";

const CTYPES = [
  ["mysql", "MySQL"], ["postgresql", "PostgreSQL"], ["oracle", "Oracle"], ["sqlserver", "SQL Server"],
  ["api", "REST/API"], ["csv", "CSV"], ["object_storage", "对象存储"], ["hive", "Hive"], ["clickhouse", "ClickHouse"],
];

// 返回 [字段key, 标签, 占位提示]；api 类型走下方结构化区块
function metaFields(ctype) {
  switch (ctype) {
    case "mysql": case "postgresql": case "oracle": case "sqlserver": case "hive": case "clickhouse":
      return [
        ["host", "主机", "常为 127.0.0.1 / localhost"],
        ["port", "端口", "如 3306（MySQL）/ 5432（PG）"],
        ["db", "库名", "数据库名"],
        ["user", "账号", "登录账号"],
      ];
    case "csv": return [["path", "文件路径", "/data/xxx.csv"]];
    case "object_storage": return [["bucket", "存储桶", "bucket-name"], ["endpoint", "Endpoint", "http://minio:9000"], ["region", "Region", "us-east-1"]];
    case "api": return [];
    default: return [];
  }
}

const METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"];
const AUTH_OPTS = [["none", "无认证"], ["bearer", "Bearer Token"], ["basic", "Basic Auth"]];
const kv = { key: "", value: "" };
const toDict = (arr) => Object.fromEntries(arr.filter((x) => x.key && x.key.trim()).map((x) => [x.key.trim(), x.value]));
const dictToArr = (d) => Object.entries(d || {}).map(([k, v]) => ({ key: k, value: v }));

export default function Connections() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(null);
  const [items, setItems] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [form, setForm] = useState({ project: "demo", name: "", ctype: "mysql", direction: "source", secret: "", meta: {} });
  const [testResult, setTestResult] = useState({});
  const [modalTest, setModalTest] = useState(null);
  const [testing, setTesting] = useState(false);
  const [edit, setEdit] = useState(null);
  const [apiParams, setApiParams] = useState([]);
  const [apiHeaders, setApiHeaders] = useState([]);
  const [confirmDel, setConfirmDel] = useState(null);   // 待删除的连接
  const [delBusy, setDelBusy] = useState(false);
  const [pageMsg, setPageMsg] = useState(null);         // 列表层操作结果（删除成功/失败）

  const load = () => {
    setLoading(true);
    api.get("/connections")
      .then((rows) => { setItems(rows); setError(null); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const setMeta = (k, v) => setForm((f) => ({ ...f, meta: { ...f.meta, [k]: v } }));

  const submit = async () => {
    setBusy(true); setMsg(null);
    let meta = { ...form.meta };
    if (form.ctype === "api") {
      const p = toDict(apiParams);
      const h = toDict(apiHeaders);
      if (p && Object.keys(p).length) meta = { ...meta, params: p };
      else delete meta.params;
      if (h && Object.keys(h).length) meta = { ...meta, headers: h };
      else delete meta.headers;
    }
    const body = { project: form.project, name: form.name, ctype: form.ctype, direction: "both", meta, secret: form.secret || null };
    try {
      if (edit) await api.put(`/connections/${edit}`, body);
      else await api.post("/connections", body);
      setShow(false); setEdit(null); load();
    } catch (e) { setMsg({ tone: "danger", text: `${edit ? "保存" : "新建"}失败：${e.message}` }); }
    finally { setBusy(false); }
  };

  const openCreate = () => { setEdit(null); setForm({ project: "demo", name: "", ctype: "mysql", direction: "source", secret: "", meta: {} }); setApiParams([]); setApiHeaders([]); setMsg(null); setModalTest(null); setShow(true); };
  const openEdit = (c) => {
    setEdit(c.conn_key);
    const m = { ...(c.meta || {}) };
    setForm({ project: c.project, name: c.name, ctype: c.ctype, direction: c.direction, secret: "", meta: m });
    setApiParams(dictToArr(m.params));
    setApiHeaders(dictToArr(m.headers));
    setMsg(null); setModalTest(null); setShow(true);
  };

  const testConn = async (ck) => {
    try {
      const r = await api.post(`/connections/${ck}/test`);
      setTestResult((p) => ({ ...p, [ck]: r }));
    } catch (e) { setTestResult((p) => ({ ...p, [ck]: { ok: false, detail: e.message } })); }
  };

  const testForm = async () => {
    setTesting(true); setModalTest(null);
    try {
      const r = await api.post("/connections/test", { project: form.project, name: form.name, ctype: form.ctype, direction: form.direction, meta: form.meta, secret: form.secret || null });
      setModalTest(r);
    } catch (e) { setModalTest({ ok: false, detail: e.message }); }
    finally { setTesting(false); }
  };

  const deleteConn = async (c) => {
    setDelBusy(true); setPageMsg(null);
    try {
      await api.del(`/connections/${c.conn_key}`);
      setConfirmDel(null);
      setPageMsg({ tone: "success", text: `已删除连接「${c.name}」。` });
      load();
    } catch (e) {
      // 被任务引用时后端拒绝（409），原因需完整展示，故不关闭确认框
      setConfirmDel(null);
      setPageMsg({ tone: "danger", text: `删除失败：${e.message}` });
    } finally { setDelBusy(false); }
  };

  const list = items.filter((c) => c.name.includes(q));

  return (
    <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <div className="mac-row mac-end" style={{ justifyContent: "space-between" }}>
        <div><h1 className="mac-h1">连接管理</h1><div className="mac-muted">{items.length} 个数据连接</div></div>
        <Row>
          <Button variant="ghost" icon="refresh" onClick={load}>刷新</Button>
          <Button variant="primary" icon="plus" onClick={openCreate}>新建连接</Button>
        </Row>
      </div>

      {error && <div className="mac-callout mac-callout--warning">后端不可达（{error}）。</div>}
      {pageMsg && <Callout tone={pageMsg.tone}>{pageMsg.text}</Callout>}

      <Card solid>
        <Row className="mac-end">
          <div style={{ flex: 1 }} />
          <Input placeholder="搜索连接…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 220 }} />
        </Row>
        <div style={{ height: 14 }} />
        {loading && list.length === 0
          ? <div className="mac-muted" style={{ padding: 20 }}>加载中…</div>
          : list.length === 0
          ? <Empty icon="plug" title="暂无连接" desc="点击右上角「新建连接」创建数据源或目标" />
          : <Table head={["名称", "类型", "地址", "连通性", "操作"]}>
              {list.map((c) => {
                const ck = c.conn_key;
                const tr = testResult[ck];
                return (
                  <Fragment key={ck}>
                    <tr>
                      <td><span style={{ fontWeight: 500 }}>{c.name}</span></td>
                      <td><span className="mac-chip">{c.ctype}</span></td>
                      <td className="num" style={{ color: "var(--text-secondary)" }}>{c.meta?.host || c.meta?.url || c.meta?.bucket || c.meta?.path || "-"}</td>
                      <td>{tr ? <Badge tone={tr.ok ? "success" : "danger"} label={tr.ok ? "接通" : "失败"} dot title={tr.detail} /> : <Badge tone="neutral" label="未测" />}</td>
                      <td>
                        <Row style={{ gap: 4 }}>
                          <Button variant="ghost" onClick={() => testConn(ck)} style={{ padding: 6 }} title="测试连接">测试</Button>
                          <Button variant="ghost" icon="edit" onClick={() => openEdit(c)} style={{ padding: 6 }} title="编辑连接">编辑</Button>
                          <Button variant="ghost" icon="trash"
                            onClick={() => { setPageMsg(null); setConfirmDel(c); }}
                            style={{ padding: 6, color: "var(--danger)" }} title="删除连接">删除</Button>
                          <Button variant="ghost" icon="chevron"
                            className={`mac-btn--chevron${open === ck ? " mac-btn--open" : ""}`}
                            onClick={() => setOpen(open === ck ? null : ck)}
                            style={{ padding: 6 }} aria-label="展开详情" aria-expanded={open === ck} />
                        </Row>
                      </td>
                    </tr>
                    {open === ck && (
                      <tr className="mac-detail-row">
                        <td colSpan={5}>
                          <div className="mac-detail-row__inner">
                            {[{ label: "连接标识", value: ck }, { label: "方向", value: "双方" },
                              { label: "类型", value: c.ctype }, { label: "所属项目", value: c.project },
                              ...Object.entries(c.meta || {}).map(([k, v]) => ({ label: k, value: v }))]
                              .map((it) => (
                                <div key={it.label} className="mac-detail__item">
                                  <span className="mac-detail__label">{it.label}</span>
                                  <span className="mac-detail__value">{String(it.value ?? "-")}</span>
                                </div>
                              ))}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </Table>}
      </Card>

      {show && (
        <Modal title={edit ? "编辑连接" : "新建连接"} onClose={() => setShow(false)} footer={
          <ModalFooter
            leading={<Button variant="ghost" icon="plug" onClick={testForm} disabled={testing}>{testing ? "测试中…" : "测试连接"}</Button>}
            onCancel={() => setShow(false)} onOk={submit} busy={busy} okText={edit ? "保存修改" : "创建连接"} />
        }>
          {msg && <Callout tone={msg.tone}>{msg.text}</Callout>}
          <Row style={{ gap: 10 }}>
            <div className="mac-field" style={{ flex: 1 }}>
              <span className="mac-label">项目</span>
              <Input value={form.project} onChange={(e) => setForm({ ...form, project: e.target.value })} />
            </div>
            <div className="mac-field" style={{ flex: 2 }}>
              <span className="mac-label">名称</span>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="如 商户库MySQL" />
            </div>
          </Row>
          <div className="mac-field">
            <span className="mac-label">类型</span>
            <Select value={form.ctype} onChange={(e) => setForm({ ...form, ctype: e.target.value, meta: {} })}>
              {CTYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </div>
          {metaFields(form.ctype).map(([k, label, placeholder]) => (
            <div className="mac-field" key={k}>
              <span className="mac-label">{label}</span>
              <Input value={form.meta[k] || ""} onChange={(e) => setMeta(k, e.target.value)} placeholder={placeholder} />
            </div>
          ))}
          {form.ctype === "api" && (
            <>
              <Row style={{ gap: 10 }}>
                <div className="mac-field" style={{ flex: 2 }}>
                  <span className="mac-label">URL</span>
                  <Input value={form.meta.url || ""} onChange={(e) => setMeta("url", e.target.value)} placeholder="https://api.example.com/orders" />
                </div>
                <div className="mac-field" style={{ flex: 1 }}>
                  <span className="mac-label">样例行数</span>
                  <Input value={form.meta.limit || ""} onChange={(e) => setMeta("limit", e.target.value)} placeholder="5" />
                </div>
              </Row>
              <Row style={{ gap: 10 }}>
                <div className="mac-field" style={{ flex: 1 }}>
                  <span className="mac-label">方法</span>
                  <Select value={form.meta.method || "GET"} onChange={(e) => setMeta("method", e.target.value)}>
                    {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
                  </Select>
                </div>
                <div className="mac-field" style={{ flex: 1 }}>
                  <span className="mac-label">认证</span>
                  <Select value={form.meta.auth || "none"} onChange={(e) => setMeta("auth", e.target.value)}>
                    {AUTH_OPTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </Select>
                </div>
              </Row>
              <div className="mac-field">
                <span className="mac-label">Query Params</span>
                {apiParams.map((p, i) => (
                  <Row key={i} style={{ gap: 6, alignItems: "center", marginBottom: 4 }}>
                    <Input value={p.key} onChange={(e) => setApiParams((a) => a.map((x, j) => j === i ? { ...x, key: e.target.value } : x))} placeholder="参数名" style={{ width: 180 }} />
                    <Input value={p.value} onChange={(e) => setApiParams((a) => a.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} placeholder="参数值" style={{ flex: 1 }} />
                    <Button variant="ghost" icon="more" onClick={() => setApiParams((a) => a.filter((_, j) => j !== i))} style={{ padding: 6 }} title="删除" />
                  </Row>
                ))}
                <Button variant="ghost" icon="plus" onClick={() => setApiParams((a) => [...a, { ...kv }])}>添加参数</Button>
              </div>
              <div className="mac-field">
                <span className="mac-label">Headers</span>
                {apiHeaders.map((p, i) => (
                  <Row key={i} style={{ gap: 6, alignItems: "center", marginBottom: 4 }}>
                    <Input value={p.key} onChange={(e) => setApiHeaders((a) => a.map((x, j) => j === i ? { ...x, key: e.target.value } : x))} placeholder="头名" style={{ width: 180 }} />
                    <Input value={p.value} onChange={(e) => setApiHeaders((a) => a.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} placeholder="头值" style={{ flex: 1 }} />
                    <Button variant="ghost" icon="more" onClick={() => setApiHeaders((a) => a.filter((_, j) => j !== i))} style={{ padding: 6 }} title="删除" />
                  </Row>
                ))}
                <Button variant="ghost" icon="plus" onClick={() => setApiHeaders((a) => [...a, { ...kv }])}>添加 Header</Button>
              </div>
            </>
          )}
          <div className="mac-field">
            <span className="mac-label">{["mysql", "postgresql", "oracle", "sqlserver", "hive", "clickhouse"].includes(form.ctype) ? "密码" : form.ctype === "api" ? "Token（密钥）" : "密钥（可选）"}</span>
            <Input type="password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} placeholder={["mysql", "postgresql", "oracle", "sqlserver", "hive", "clickhouse"].includes(form.ctype) ? "数据库账号密码" : "落库后将加密存储，仅返回引用"} />
            {["mysql", "postgresql", "oracle", "sqlserver", "hive", "clickhouse"].includes(form.ctype) && <span className="mac-muted" style={{ fontSize: 11 }}>连接时用「账号 + 密码」鉴权；密码加密存储，仅存引用。</span>}
          </div>
        </Modal>
      )}

      {confirmDel && (
        <ConfirmDialog
          title="删除连接"
          message={<span>确认删除连接 <b>{confirmDel.name}</b>？此操作不可撤销；若仍有 ETL 任务引用该连接，删除会被拒绝。</span>}
          okText="删除"
          busy={delBusy}
          onOk={() => deleteConn(confirmDel)}
          onCancel={() => { setConfirmDel(null); }}
        />
      )}

      {modalTest && (
        <Modal title="连接测试结果" onClose={() => setModalTest(null)} width={420} footer={
          <div className="mac-row mac-end" style={{ gap: 8, width: "100%", justifyContent: "flex-end" }}>
            <Button variant="primary" icon="check" onClick={() => setModalTest(null)}>知道了</Button>
          </div>
        }>
          <Callout tone={modalTest.ok ? "success" : "danger"}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{modalTest.ok ? "连接成功" : "连接失败"}</div>
            <div className="mac-muted" style={{ fontSize: 12, wordBreak: "break-all" }}>
              {modalTest.detail || (modalTest.ok ? "已成功连通数据源。" : "未能连通，请检查主机、端口、账号或密钥配置。")}
            </div>
          </Callout>
        </Modal>
      )}
    </div>
  );
}