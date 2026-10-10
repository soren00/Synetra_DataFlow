import { useEffect, useState } from "react";
import { Button, Callout, Card, Empty, Row, Segmented, Table, Textarea } from "./ui";
import { api } from "../api/client";

/* AI 管理：技能包清单 / 查看 / 编辑。
   保存前由服务端用与一致性测试同源的校验器先校验，不通过即拒绝且不改动原文件；
   技能包内容对后续生成即时生效（代码层变更才需重启）。 */
export default function SkillManager() {
  const [skills, setSkills] = useState([]);
  const [name, setName] = useState("");
  const [meta, setMeta] = useState(null);
  const [contents, setContents] = useState({});
  const [file, setFile] = useState("SKILL.md");
  const [msg, setMsg] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = () =>
    api.get("/llm/skills")
      .then((sk) => {
        setSkills(sk || []);
        setError(null);
        return sk || [];
      })
      .catch((e) => { setError(e.message); return []; });

  const open = (n) => {
    setMsg(null);
    setName(n);
    return api.get(`/llm/skills/${n}`)
      .then((r) => { setMeta(r); setContents(r.contents || {}); setFile("SKILL.md"); })
      .catch((e) => setMsg({ tone: "danger", text: `加载失败：${e.message}` }));
  };

  useEffect(() => {
    load().then((sk) => { if (sk.length) open(sk[0].name); });
  }, []);

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.put(`/llm/skills/${name}`, { files: { [file]: contents[file] } });
      setMsg({ tone: "success", text: `已保存并即时生效（指纹 ${r.before} → ${r.after}）` });
      setMeta(r);
      load();
    } catch (e) {
      // 校验未通过（422）：原因由后端给出，原文件未被改动
      setMsg({ tone: "danger", text: `保存被拒绝：${e.message}` });
    } finally { setBusy(false); }
  };

  const files = meta?.files || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      {error && <div className="mac-callout mac-callout--warning">后端不可达（{error}）。</div>}
      {msg && <Callout tone={msg.tone}>{msg.text}</Callout>}

      <Callout tone="info">
        技能包内容修改后对后续生成即时生效；注入方式、提示词与校验回环等代码层改动需重启服务。
        保存前会先做一致性校验（函数清单同源、禁止项逐字一致、示例可求值），不通过则拒绝写盘。
      </Callout>

      <Card title="技能包" sub="随服务分发，供 AI 生成任务与脚本时注入" solid>
        {skills.length === 0
          ? <Empty icon="ai" title="暂无技能包" desc="随服务分发的技能包会出现在这里" />
          : <Table head={["技能包", "描述", "内容指纹", "最近修改", "操作"]}>
              {skills.map((s) => (
                <tr key={s.name}>
                  <td style={{ fontWeight: 500 }}>{s.name}</td>
                  <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>{s.description || "-"}</td>
                  <td className="num" style={{ fontSize: 12 }}>{s.fingerprint}</td>
                  <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>{s.updated_at || "-"}</td>
                  <td>
                    <Button variant="ghost" icon="edit" style={{ padding: 6 }}
                      onClick={() => open(s.name)} disabled={s.name === name}>编辑</Button>
                  </td>
                </tr>
              ))}
            </Table>}
      </Card>

      {meta && (
        <Card title={`编辑 ${name}`} sub={`${meta.applies_to || ""} · 指纹 ${meta.fingerprint}`} solid>
          <Row className="mac-end" style={{ justifyContent: "space-between" }}>
            <Segmented items={files.map((f) => ({ value: f, label: f }))} value={file} onChange={setFile} />
            <Row style={{ gap: 8 }}>
              <Button variant="ghost" icon="refresh" onClick={() => open(name)} disabled={busy}>放弃修改</Button>
              <Button variant="primary" icon="check" onClick={save} disabled={busy}>
                {busy ? "保存中…" : "保存"}
              </Button>
            </Row>
          </Row>
          <div style={{ height: 10 }} />
          <Textarea rows={18} value={contents[file] ?? ""} spellCheck={false}
            onChange={(e) => setContents((c) => ({ ...c, [file]: e.target.value }))}
            style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12, lineHeight: 1.6 }} />
          <span className="mac-muted" style={{ fontSize: 11 }}>
            {file === "examples.md"
              ? "示例文件同时是测试用例：其中标记块会被逐一求值校验。"
              : "主文件含函数清单与禁止项标记块，须与代码内权威白名单逐字一致。"}
          </span>
        </Card>
      )}
    </div>
  );
}
