import { useEffect, useState } from "react";
import { Button, Callout, Card, ConfirmDialog, Empty, Input, Modal, ModalFooter, Row, Table } from "../components/ui";
import { api } from "../api/client";
import Tasks from "./Tasks";

/* 项目清单：ETL 任务按项目组织；项目只管理任务（连接/调度/血缘不在此管理）。 */
function ProjectList({ onOpen }) {
  const [projects, setProjects] = useState([]);
  const [error, setError] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [show, setShow] = useState(false);
  const [name, setName] = useState("");
  const [confirmDel, setConfirmDel] = useState(null);

  const load = () => {
    api.get("/projects")
      .then((ps) => { setProjects(ps || []); setError(null); })
      .catch((e) => setError(e.message));
  };
  useEffect(load, []);

  const create = async () => {
    setBusy(true); setMsg(null);
    try {
      await api.post("/projects", { name });
      setShow(false); setName(""); load();
    } catch (e) {
      setMsg({ tone: "danger", text: `新建失败：${e.message}` });
    } finally { setBusy(false); }
  };

  const remove = async (p) => {
    setBusy(true); setMsg(null);
    try {
      await api.del(`/projects/${encodeURIComponent(p.name)}`);
      setConfirmDel(null); load();
    } catch (e) {
      // 非空项目由后端拒绝（409），就近反馈原因，不级联删除任务
      setMsg({ tone: "danger", text: `删除失败：${e.message}` });
      setConfirmDel(null);
    } finally { setBusy(false); }
  };

  return (
    <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <div className="mac-row mac-end" style={{ justifyContent: "space-between" }}>
        <div>
          <h1 className="mac-h1">项目</h1>
          <div className="mac-muted">先建项目、再进项目创建 ETL 任务 · 共 {projects.length} 个项目</div>
        </div>
        <Row>
          <Button variant="ghost" icon="refresh" onClick={load}>刷新</Button>
          <Button variant="primary" icon="plus" onClick={() => { setName(""); setMsg(null); setShow(true); }}>新建项目</Button>
        </Row>
      </div>

      {error && <div className="mac-callout mac-callout--warning">后端不可达（{error}）。</div>}
      {msg && !show && <Callout tone={msg.tone}>{msg.text}</Callout>}

      <Card solid>
        {projects.length === 0
          ? <Empty icon="tasks" title="暂无项目" desc="点击右上角「新建项目」，进入项目后再新建 ETL 任务" />
          : <Table head={["项目", "任务数", "创建时间", "创建人", "操作"]}>
              {projects.map((p) => (
                <tr key={p.name}>
                  <td>
                    <button
                      className="mac-btn mac-btn--ghost"
                      style={{ padding: 0, fontWeight: 500 }}
                      onClick={() => onOpen(p)}
                      title="进入项目"
                    >{p.name}</button>
                  </td>
                  <td className="num">{p.task_count}</td>
                  <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>{p.created_at || "-"}</td>
                  <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>{p.created_by || "-"}</td>
                  <td>
                    <Row style={{ gap: 4 }}>
                      <Button variant="ghost" icon="chevron" onClick={() => onOpen(p)} style={{ padding: 6 }} title="进入项目">进入</Button>
                      <Button variant="ghost" icon="trash" onClick={() => { setMsg(null); setConfirmDel(p); }}
                        style={{ padding: 6, color: "var(--danger)" }} title="删除项目">删除</Button>
                    </Row>
                  </td>
                </tr>
              ))}
            </Table>}
      </Card>

      {show && (
        <Modal title="新建项目" onClose={() => { setShow(false); setMsg(null); }} width={460}
          footer={
            <ModalFooter onCancel={() => { setShow(false); setMsg(null); }} onOk={create}
              busy={busy} okText="创建" />
          }>
          {msg && <Callout tone={msg.tone}>{msg.text}</Callout>}
          <div className="mac-field">
            <span className="mac-label">项目名称</span>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="如 订单域" autoFocus />
          </div>
        </Modal>
      )}

      {confirmDel && (
        <ConfirmDialog
          title="删除项目"
          message={<span>确认删除项目 <b>{confirmDel.name}</b>？仍含任务的项目需先清空或迁移任务。</span>}
          okText="删除"
          busy={busy}
          onOk={() => remove(confirmDel)}
          onCancel={() => { setConfirmDel(null); setMsg(null); }}
        />
      )}
    </div>
  );
}

/* 页内两级视图：项目清单 ⇄ 项目内任务列表（不引入路由库）。 */
export default function Projects() {
  const [current, setCurrent] = useState(null);
  if (current) return <Tasks project={current.name} onBack={() => setCurrent(null)} />;
  return <ProjectList onOpen={setCurrent} />;
}
