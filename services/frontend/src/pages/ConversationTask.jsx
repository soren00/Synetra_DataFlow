import { useEffect, useRef, useState } from "react";
import { Badge, Button, Callout, Card, Empty, Icon, Modal, ModalFooter, Pager, Row, Segmented, Select, Table, Textarea } from "../components/ui";
import { api } from "../api/client";

const modeCN = { append: "全量", upsert: "主键 Upsert", incremental: "增量+水位" };
const schemaCN = { catalog: "元数据 Catalog", information_schema: "实时 information_schema", inferred: "LLM 推断" };
const REC_FILTER = [
  { value: "all", label: "全部" },
  { value: "success", label: "创建成功" },
  { value: "failed", label: "创建失败" },
];
const REC_TONE = { success: "success", failed: "danger" };
const REC_CN = { success: "创建成功", failed: "创建失败" };

// 单元格取值：位置数组按下标，对象行按列名
function cellVal(row, col, idx) {
  const v = Array.isArray(row) ? row[idx] : row[col];
  return typeof v === "undefined" ? "" : v;
}

export default function ConversationTask() {
  const [conns, setConns] = useState([]);
  const [projects, setProjects] = useState([]);   // 项目清单（来自项目管理接口）
  const [project, setProject] = useState("");     // 所属项目（可选）
  const [srcKey, setSrcKey] = useState("");       // 数据源连接
  const [tgtKey, setTgtKey] = useState("");       // 目标数据库连接
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);   // 聊天消息流 {role, text}
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [draft, setDraft] = useState(null);       // TaskConfig JSON
  const [warnings, setWarnings] = useState([]);
  const [sample, setSample] = useState(null);
  const [schemaSource, setSchemaSource] = useState("");
  const [inferred, setInferred] = useState(false);
  const [msg, setMsg] = useState(null);
  const [modal, setModal] = useState(null);   // 创建结果弹窗 {kind, title, body}
  const [saved, setSaved] = useState(false);
  const [history, setHistory] = useState([]);  // 多轮会话历史（服务端产出，下一次请求原样回传）
  const [exprIssues, setExprIssues] = useState([]);  // 字段级表达式校验结果
  const [prompts, setPrompts] = useState([]);    // 本会话的提示词链（首次描述 + 历次修订指令）
  const [view, setView] = useState("chat");      // chat | records（记录页为页内子视图）
  // 记录页：服务端分页 + 状态筛选（契约与 /runs 一致）
  const [recs, setRecs] = useState([]);
  const [recStatus, setRecStatus] = useState("all");
  const [recPage, setRecPage] = useState(1);
  const [recPageSize, setRecPageSize] = useState(20);
  const [recTotal, setRecTotal] = useState(0);
  const [recOpen, setRecOpen] = useState(null);
  const [recMsg, setRecMsg] = useState(null);
  const feedRef = useRef(null);

  const load = () => {
    Promise.all([
      api.get("/connections"),
      api.get("/projects").catch(() => []),  // 项目清单不可得时降级为空，不阻断对话
    ])
      .then(([cs, ps]) => { setConns(cs || []); setProjects(ps || []); setError(null); })
      .catch((e) => setError(e.message));
  };
  useEffect(load, []);

  const loadRecords = () => {
    const params = { page: recPage, page_size: recPageSize };
    if (recStatus !== "all") params.status = recStatus;
    api.get("/llm/generations", params)
      .then((r) => {
        setRecs(r.records || []);
        setRecTotal(r.total ?? 0);
        if (r.page && r.page !== recPage) setRecPage(r.page);  // 后端回夹页码
        setRecMsg(null);
      })
      .catch((e) => setRecMsg({ tone: "danger", text: `记录加载失败：${e.message}` }));
  };
  useEffect(() => { if (view === "records") loadRecords(); }, [view, recStatus, recPage, recPageSize]);

  const srcOptions = conns.filter((c) => c.direction !== "target");
  const tgtOptions = conns.filter((c) => c.direction !== "source");
  const tgtAll = tgtOptions.length ? tgtOptions : conns; // 未设方向时回退全部

  const push = (role, text) => setMessages((m) => [...m, { role, text }]);
  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setBusy(true); setMsg(null); setSaved(false);
    push("user", text);
    setPrompts((ps) => [...ps, text]);  // 留痕：本会话的提示词链
    setInput("");
    const connList = [srcKey, tgtKey].filter(Boolean)
      .map((k) => {
        const c = conns.find((x) => x.conn_key === k);
        return { conn_key: k, name: c?.name || k, ctype: c?.ctype, project: c?.project };
      });
    try {
      const isRevise = Boolean(draft);
      const body = isRevise
        ? { draft, instruction: text, project: project || null, connections: connList, history }
        : { description: text, project: project || null, connections: connList,
            source_conn_key: srcKey || null, target_conn_key: tgtKey || null, history };
      const r = isRevise
        ? await api.post("/llm/tasks/draft/revise", body)
        : await api.post("/llm/tasks/draft", body);
      setDraft(r.draft);
      setHistory(r.history || []);   // 多轮会话：保存服务端返回的模型可见历史，下一轮原样回传
      setExprIssues(r.expression_issues || []);
      setWarnings(r.warnings || []);
      setSample(r.sample || {});
      setSchemaSource(r.schema_source || "");
      setInferred(r.inferred_fields || false);
      const m = modeCN[r.draft.mode] || r.draft.mode;
      push("assistant", isRevise
        ? `已修订草稿「${r.draft.name}」（${m}），字段映射 ${r.draft.fields?.length || 0} 条。`
        : `已生成草稿：从 ${r.draft.source_object} → ${r.draft.target_object}，模式 ${m}。请在右侧核对后确认创建。`);
      setMsg({ tone: "success", text: isRevise ? "已按指令生成修订稿。" : "草稿已生成，请核对字段映射与样例后确认创建。" });
    } catch (e) {
      push("assistant", `生成失败：${e.message}`);
      setMsg({ tone: "danger", text: `生成失败：${e.message}` });
    } finally { setBusy(false); }
  };

  /* 重置：清空对话流、草稿、校验告警、样例与已选连接上下文，回到初始态 */
  const reset = () => {
    setMessages([]);
    setDraft(null);
    setHistory([]);
    setExprIssues([]);
    setWarnings([]);
    setSample(null);
    setSchemaSource("");
    setInferred(false);
    setMsg(null);
    setSaved(false);
    setModal(null);
    setInput("");
    setPrompts([]);
    setProject("");
    setSrcKey("");
    setTgtKey("");
    setView("chat");
  };

  const confirmCreate = async () => {
    setBusy(true);
    const p = project || projects[0]?.name || "default";
    try {
      // 携带提示词链：无论创建成功或失败，服务端都会写一条记录
      await api.post("/tasks", {
        project: p, name: draft.name || "nl-task", config: draft,
        ai_trace: { prompts, draft },
      });
      setSaved(true);
      setModal({ kind: "success", title: "创建成功",
        body: `任务「${draft.name}」已创建，可到「项目」中该项目下的任务列表查看并运行。` });
    } catch (e) {
      setModal({ kind: "danger", title: "创建失败", body: `任务创建失败：${e.message}` });
    } finally { setBusy(false); }
  };

  if (view === "records") {
    return (
      <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
        <div className="mac-row mac-end" style={{ justifyContent: "space-between" }}>
          <div>
            <Row style={{ gap: 8, alignItems: "center" }}>
              <Button variant="ghost" onClick={() => setView("chat")} title="返回 AI ETL 任务">
                <Icon name="chevronLeft" size={14} />
                返回 AI ETL
              </Button>
              <h1 className="mac-h1">生成与创建记录</h1>
            </Row>
            <div className="mac-muted">每次「确认创建任务」都会留痕（无论成功或失败），含本次输入的提示词链</div>
          </div>
          <Button variant="ghost" icon="refresh" onClick={loadRecords}>刷新</Button>
        </div>

        {recMsg && <Callout tone={recMsg.tone}>{recMsg.text}</Callout>}

        <Card solid>
          <Row className="mac-end">
            <Segmented items={REC_FILTER} value={recStatus}
              onChange={(v) => { setRecStatus(v); setRecPage(1); }} />
          </Row>
          <div style={{ height: 14 }} />
          {recs.length === 0
            ? <Empty icon="ai" title="暂无记录" desc="在 AI ETL 任务页确认创建任务后，这里会出现留痕记录" />
            : <>
                <Table head={["时间", "状态", "任务", "项目", "操作者", "模型", "操作"]}>
                  {recs.map((r) => (
                    <tr key={r.id}>
                      <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>{r.created_at || "-"}</td>
                      <td><Badge tone={REC_TONE[r.status] || "neutral"} label={REC_CN[r.status] || r.status} /></td>
                      <td>{r.task_name || "-"}</td>
                      <td>{r.project || "-"}</td>
                      <td>{r.actor || "-"}</td>
                      <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>{r.model || "-"}</td>
                      <td>
                        <Button variant="ghost" icon="eye" style={{ padding: 6 }} title="查看提示词与创建结果"
                          onClick={() => setRecOpen(recOpen?.id === r.id ? null : r)}>详情</Button>
                      </td>
                    </tr>
                  ))}
                </Table>

                {recOpen && (
                  <div style={{
                    marginTop: 10, padding: 12, border: "1px solid var(--border)",
                    borderRadius: "var(--r-lg)", background: "var(--surface-2)",
                    display: "flex", flexDirection: "column", gap: 8,
                  }}>
                    <span className="mac-label">提示词链（{recOpen.prompts?.length || 0} 条）</span>
                    {(recOpen.prompts || []).length === 0
                      ? <div className="mac-muted" style={{ fontSize: 12 }}>未记录提示词。</div>
                      : <ol style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                          {recOpen.prompts.map((t, i) => (
                            <li key={i} style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{t}</li>
                          ))}
                        </ol>}
                    {recOpen.status === "failed" && (
                      <Callout tone="danger" icon="alert">失败原因：{recOpen.error || "-"}</Callout>
                    )}
                    {recOpen.draft?.source_object && (
                      <span className="mac-muted" style={{ fontSize: 12 }}>
                        草稿：{recOpen.draft.source_object} → {recOpen.draft.target_object}（{modeCN[recOpen.draft.mode] || recOpen.draft.mode}）
                      </span>
                    )}
                    <span className="mac-muted" style={{ fontSize: 12 }}>
                      任务标识：{recOpen.task_key || "（未创建）"} · 技能包：{(recOpen.skills || []).join("、") || "-"} ·
                      会话：{recOpen.conversation_id || "-"}
                    </span>
                    {recOpen.status === "failed" && (
                      <span className="mac-muted" style={{ fontSize: 11 }}>
                        失败记录中的草稿仅为诊断快照，不等同于任务落库，不可运行。
                      </span>
                    )}
                  </div>
                )}

                <Pager page={recPage} pageSize={recPageSize} total={recTotal}
                  onPageChange={setRecPage}
                  onPageSizeChange={(n) => { setRecPageSize(n); setRecPage(1); }} />
              </>}
        </Card>
      </div>
    );
  }

  return (
    <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <div className="mac-row mac-end" style={{ justifyContent: "space-between" }}>
        <div>
          <h1 className="mac-h1">AI ETL 任务</h1>
          <div className="mac-muted">用自然语言描述抽取需求，生成 TaskConfig 草稿，人工核对后落库</div>
        </div>
        <Row>
          <Button variant="ghost" icon="refresh" onClick={reset} title="清空对话、草稿与已选连接，回到初始态">重置</Button>
          <Button variant="ghost" icon="eye" onClick={() => setView("records")} title="查看每次创建的留痕记录">查看生成记录</Button>
        </Row>
      </div>

      {error && <div className="mac-callout mac-callout--warning">后端不可达（{error}）。</div>}
      {msg && <Callout tone={msg.tone}>{msg.text}</Callout>}

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
        {/* 上：聊天窗口 */}
        <Card title="对话" sub="先选择连接，再自然语言描述需求；有草稿后输入即修订" solid>
          <Row style={{ gap: 8 }}>
            <div className="mac-field" style={{ flex: 1 }}>
              <span className="mac-label">所属项目（可选）</span>
              <Select value={project} onChange={(e) => setProject(e.target.value)}>
                <option value="">（不指定）</option>
                {projects.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
              </Select>
            </div>
            <div className="mac-field" style={{ flex: 1 }}>
              <span className="mac-label">数据源</span>
              <Select value={srcKey} onChange={(e) => setSrcKey(e.target.value)}>
                <option value="">未选择</option>
                {srcOptions.map((c) => <option key={c.conn_key} value={c.conn_key}>{c.name}（{c.ctype}）</option>)}
              </Select>
            </div>
            <div className="mac-field" style={{ flex: 1 }}>
              <span className="mac-label">目标数据库</span>
              <Select value={tgtKey} onChange={(e) => setTgtKey(e.target.value)}>
                <option value="">未选择</option>
                {tgtAll.map((c) => <option key={c.conn_key} value={c.conn_key}>{c.name}（{c.ctype}）</option>)}
              </Select>
            </div>
          </Row>

          <div ref={feedRef} style={{
            maxHeight: 520, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8,
            padding: 12, border: "1px solid var(--border)", borderRadius: "var(--r-lg)",
            background: "var(--surface-2)", marginTop: 8,
          }}>
            {messages.length === 0 && !busy && (
              <div className="mac-muted" style={{ fontSize: 12, padding: 6, cursor: "pointer" }}
                onClick={() => setInput("每天从 orders 增量抽已支付订单到 orders_dw，主键 id")}>
                示例（点击填入）：每天从 orders 增量抽已支付订单到 orders_dw，主键 id
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} style={{ alignSelf: m.role === "user" ? "flex-end" : "flex-start", maxWidth: "90%" }}>
                <div style={{
                  background: m.role === "user" ? "var(--accent)" : "var(--surface)",
                  color: m.role === "user" ? "#fff" : "var(--text)",
                  padding: "8px 12px", borderRadius: 12, fontSize: 13,
                  whiteSpace: "pre-wrap", boxShadow: "var(--shadow-xs)",
                }}>{m.text}</div>
              </div>
            ))}
            {busy && (
              <div style={{ alignSelf: "flex-start" }}>
                <div className="mac-muted" style={{
                  display: "flex", alignItems: "center", gap: 8,
                  background: "var(--surface)", padding: "8px 12px", borderRadius: 12,
                  fontSize: 13, boxShadow: "var(--shadow-xs)",
                }}>
                  <span className="mac-spinner" />
                  {draft ? "正在修订草稿…" : "正在生成草稿…"}
                </div>
              </div>
            )}
          </div>

          <Row style={{ marginTop: 8, gap: 8 }}>
            <Textarea rows={12} value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
              placeholder={draft ? "输入修订指令，如「改成全量」「水位用 create_time」…" : "描述抽取需求…"}
              style={{ flex: 1 }} />
            <Button variant="primary" icon={draft ? "refresh" : "ai"} onClick={send} disabled={busy || !input.trim()}>
              {busy ? "处理中…" : (draft ? "修订" : "生成")}
            </Button>
          </Row>
          {conns.length === 0 && <div className="mac-muted" style={{ fontSize: 12, marginTop: 8 }}>暂无数据连接，请先在「连接管理」创建源/目标连接。</div>}
        </Card>

        {/* 下：草稿卡 */}
        <Card title="草稿卡" sub={`schema 来源：${schemaCN[schemaSource] || schemaSource || "-"}${inferred ? "（字段为推断，待核对）" : ""}`} solid
          actions={draft && <Badge tone={inferred ? "warning" : "success"} label={modeCN[draft.mode] || draft.mode} />}>
          {!draft ? (
            <Empty icon="ai" title="尚未生成草稿" desc="在上方对话窗描述需求并发送，草稿会展示在这里。" />
          ) : (
            <>
              <div className="mac-detail-row__inner" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
                {[
                  { label: "任务名", value: draft.name },
                  { label: "源", value: `${draft.source_object}（${draft.source_conn_key.slice(0, 8)}…）` },
                  { label: "目标", value: `${draft.target_object}（${draft.target_conn_key.slice(0, 8)}…）` },
                  { label: "主键", value: (draft.primary_keys || []).join(",") || "-" },
                  { label: "增量字段", value: draft.incremental?.field || "-" },
                  { label: "水位字段", value: draft.incremental?.watermark_field || "-" },
                ].map((it) => (
                  <div key={it.label} className="mac-detail__item">
                    <span className="mac-detail__label">{it.label}</span>
                    <span className="mac-detail__value">{String(it.value ?? "-")}</span>
                  </div>
                ))}
              </div>

              {warnings.length > 0 && (
                <Callout tone="warning" icon="alert" style={{ marginTop: 8 }}>
                  {warnings.map((w, i) => <div key={i}>{w}</div>)}
                </Callout>
              )}

              {exprIssues.length > 0 && (
                <Callout tone="danger" icon="alert" style={{ marginTop: 8 }}>
                  {exprIssues.map((it, i) => (
                    <div key={i}>字段「{it.target || it.field}」表达式校验未通过：{it.message}</div>
                  ))}
                </Callout>
              )}

              <div style={{ marginTop: 12 }}>
                <span className="mac-label">字段映射（{draft.fields?.length || 0} 条）</span>
                {(draft.fields || []).length === 0
                  ? <div className="mac-muted" style={{ fontSize: 12, marginTop: 6 }}>未生成字段映射。</div>
                  : <Table head={["源字段", "目标字段", "处理", "类型", "表达式"]}>
                      {draft.fields.map((f, i) => {
                        const issue = exprIssues.find((x) => x.target === (f.target || f.source));
                        return (
                          <tr key={i} style={issue ? { background: "var(--danger-bg)" } : undefined}>
                            <td>{f.source}</td>
                            <td>{f.target || f.source}</td>
                            <td><span className="mac-chip">{f.kind}</span></td>
                            <td>{f.cast_to || "-"}</td>
                            <td style={issue ? { color: "var(--danger)" } : undefined}>
                              {f.expression || "-"}
                              {issue ? `（校验未通过：${issue.message}）` : ""}
                            </td>
                          </tr>
                        );
                      })}
                    </Table>}
              </div>

              <div style={{ marginTop: 12 }}>
                <span className="mac-label">源表样例（{sample?.available ? (sample.rows || []).length : 0} 行）</span>
                {sample?.available && (sample.rows || []).length > 0 ? (
                  <div className="mac-scroll" style={{ maxHeight: 220, overflow: "auto", border: "1px solid var(--border)", borderRadius: "var(--r-lg)", marginTop: 6 }}>
                    <Table head={sample.columns}>
                      {sample.rows.map((row, i) => (
                        <tr key={i}>
                          {sample.columns.map((c, j) => <td key={c} className="num">{String(cellVal(row, c, j))}</td>)}
                        </tr>
                      ))}
                    </Table>
                  </div>
                ) : <div className="mac-muted" style={{ fontSize: 12, marginTop: 6 }}>{sample?.note || "预览不可用，不阻断草稿展示。"}</div>}
              </div>

              <Row style={{ marginTop: 12, justifyContent: "flex-end" }}>
                <Button variant="primary" icon="check" onClick={confirmCreate} disabled={busy || saved}>确认创建任务</Button>
              </Row>
            </>
          )}
        </Card>
      </div>

      {saved && <Empty icon="check" title="已创建" desc="前往「ETL 任务」列表查看并运行该任务。" />}

      {modal && (
        <Modal title={modal.title} onClose={() => setModal(null)}
          footer={
            <ModalFooter onCancel={() => setModal(null)} okText="关闭" onOk={() => setModal(null)}
              okVariant={modal.kind === "success" ? "primary" : "danger"} />
          }>
          <Callout tone={modal.kind === "success" ? "success" : "danger"}
            icon={modal.kind === "success" ? "check" : "alert"}>
            {modal.body}
          </Callout>
        </Modal>
      )}
    </div>
  );
}
