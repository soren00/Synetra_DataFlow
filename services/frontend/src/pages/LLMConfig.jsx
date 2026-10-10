import { useEffect, useState } from "react";
import { Badge, Button, Callout, Card, ConfirmDialog, Icon, Input, Row, Segmented, Select } from "../components/ui";
import { api, getSession } from "../api/client";
import SkillManager from "../components/SkillManager";

const TABS = [{ value: "config", label: "模型配置" }, { value: "skills", label: "技能包" }];
const PROVIDERS = [["deepseek", "DeepSeek"]];
const DEFAULT_MODELS = [["deepseek-chat", "deepseek-chat"], ["deepseek-reasoner", "deepseek-reasoner"]];

export default function LLMConfig() {
  // 会话在组件内读取：模块级读取会在「登录前加载模块」时永久为 null，导致登录后看不到本页
  const session = (typeof window !== "undefined" && getSession()) || null;
  const [cfg, setCfg] = useState(null);
  const [form, setForm] = useState({ provider: "deepseek", base_url: "", model: "", api_key: "" });
  const [models, setModels] = useState([]);
  const [error, setError] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const [tab, setTab] = useState("config");

  const load = () => {
    api.get("/llm/config")
      .then((r) => {
        setCfg(r);
        setForm((f) => ({ ...f, provider: r.provider, base_url: r.base_url, model: r.model, api_key: "" }));
        setError(null);
      })
      .catch((e) => setError(e.message));
  };
  useEffect(load, []);

  const doTest = async () => {
    setTesting(true); setTestResult(null); setMsg(null);
    try {
      const r = await api.post("/llm/config/test", {
        provider: form.provider, base_url: form.base_url, model: form.model,
        api_key: form.api_key || null,
      });
      setTestResult(r);
      if (r.ok && r.models?.length) {
        setModels(r.models);
        if (!form.model) setForm((f) => ({ ...f, model: r.models[0] })); // 仅空值时填充，保留用户输入
      }
    } catch (e) { setTestResult({ ok: false, reason: e.message }); }
    finally { setTesting(false); }
  };

  const doSave = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.put("/llm/config", {
        provider: form.provider, base_url: form.base_url, model: form.model,
        api_key: form.api_key || null,
      });
      setMsg({ tone: "success", text: `配置已保存并热切换生效，API Key 掩码 ${r.api_key_masked || "（未配置）"}` });
      setForm((f) => ({ ...f, api_key: "" }));
      load();
    } catch (e) { setMsg({ tone: "danger", text: `保存失败：${e.message}` }); }
    finally { setBusy(false); }
  };

  const doRevoke = async () => {
    setBusy(true); setMsg(null);
    try {
      await api.del("/llm/config/key");
      setMsg({ tone: "warning", text: "API Key 已吊销，生成请求将返回 503 引导。" });
      setForm((f) => ({ ...f, api_key: "" }));
      setConfirmRevoke(false);
      load();
    } catch (e) { setMsg({ tone: "danger", text: `吊销失败：${e.message}` }); setConfirmRevoke(false); }
    finally { setBusy(false); }
  };

  if (!session || session.role !== "admin") {
    return (
      <div className="mac-fade">
        <h1 className="mac-h1">AI 管理</h1>
        <Callout tone="warning" icon="shield">该页面仅管理员可见；当前会话无权限（后端亦返回 403）。</Callout>
      </div>
    );
  }

  const modelOptions = models.length ? models.map((m) => [m, m]) : DEFAULT_MODELS;

  return (
    <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <div className="mac-row mac-end" style={{ justifyContent: "space-between" }}>
        <div><h1 className="mac-h1">AI 管理</h1><div className="mac-muted">管理员专属 · 模型配置与技能包（AI 生成资产）</div></div>
        <Row style={{ gap: 8, alignItems: "center" }}>
          <Segmented items={TABS} value={tab} onChange={setTab} />
          {tab === "config" && <Button variant="ghost" icon="refresh" onClick={load}>刷新</Button>}
        </Row>
      </div>

      {tab === "skills" ? <SkillManager /> : (
        <>
          {error && <div className="mac-callout mac-callout--warning">后端不可达（{error}）。</div>}
          {msg && <Callout tone={msg.tone}>{msg.text}</Callout>}

      <Card title="生效配置" sub="存储值优先，环境变量兜底；保存即热切换，不影响既有任务与脚本" solid>
        <div className="mac-detail-row__inner" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
          {[
            { label: "供应商", value: cfg?.provider || "-" },
            { label: "Base URL", value: cfg?.base_url || "-" },
            { label: "模型", value: cfg?.model || "-" },
            { label: "API Key", value: cfg?.api_key_masked || (cfg?.has_api_key ? "已配置" : "未配置") },
            { label: "最近更新", value: cfg?.updated_at ? `${cfg.updated_at}（${cfg.updated_by}）` : "-" },
          ].map((it) => (
            <div key={it.label} className="mac-detail__item">
              <span className="mac-detail__label">{it.label}</span>
              <span className="mac-detail__value">{String(it.value ?? "-")}</span>
            </div>
          ))}
        </div>
      </Card>

      <Card title="编辑配置" sub="API Key 明文仅在本次输入时展示，保存后加密存储" solid>
        <div className="mac-field">
          <span className="mac-label">供应商</span>
          <Select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}>
            {PROVIDERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </Select>
        </div>
        <div className="mac-field">
          <span className="mac-label">Base URL</span>
          <Input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder="https://api.deepseek.com/v1" />
        </div>
        <div className="mac-field">
          <span className="mac-label">模型</span>
          <Input list="llm-model-options" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })}
            placeholder="如 deepseek-chat（可输入，也可从建议中选择）" />
          <datalist id="llm-model-options">
            {modelOptions.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </datalist>
          <span className="mac-muted" style={{ fontSize: 11 }}>模型名可自由输入；「测试连通」成功后按返回的模型列表提供下拉建议。</span>
        </div>
        <div className="mac-field">
          <span className="mac-label">API Key（明文仅本次输入展示）</span>
          <Input type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            placeholder={cfg?.has_api_key ? `已配置 ${cfg.api_key_masked}，留空保持原 Key` : "sk-..."} />
        </div>

        <Row style={{ gap: 8, alignItems: "center" }}>
          <Button variant="ghost" icon="plug" onClick={doTest} disabled={testing}>{testing ? "测试中…" : "测试连通"}</Button>
          <Button variant="primary" icon="check" onClick={doSave} disabled={busy}>{busy ? "保存中…" : "保存"}</Button>
          <Button variant="danger" icon="trash" onClick={() => { setMsg(null); setConfirmRevoke(true); }} disabled={busy || !cfg?.has_api_key}>吊销 Key</Button>
        </Row>

        {testResult && (
          <div style={{ marginTop: 10 }}>
            {testResult.ok
              ? <Callout tone="success">连通成功，可用模型：{testResult.models?.join("、") || "-"}</Callout>
              : <Callout tone="danger">连通失败：{testResult.reason}</Callout>}
          </div>
        )}
      </Card>

      {confirmRevoke && (
        <ConfirmDialog
          title="吊销 API Key"
          message="确认吊销已保存的 API Key？此后生成/测试请求将提示未配置。"
          okText="吊销"
          busy={busy}
          onOk={doRevoke}
          onCancel={() => { setConfirmRevoke(false); setMsg(null); }}
        />
      )}
        </>
      )}
    </div>
  );
}
