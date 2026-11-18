import { useCallback, useEffect, useState } from "react";
import { getSession, api } from "../api/client";
import { Avatar, Badge, Button, Callout, Card, ConfirmDialog, Input, Modal, ModalFooter, Row, Select, Segmented, Table } from "../components/ui";

const ROLE_TONE = { admin: "danger", developer: "info", readonly: "neutral" };
const ROLE_CN = { admin: "管理员", developer: "开发者", readonly: "只读" };

export default function Security() {
  const [tab, setTab] = useState("users");
  const [users, setUsers] = useState([]);
  const [q, setQ] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [error, setError] = useState(null);

  const [showNew, setShowNew] = useState(false);
  const [newForm, setNewForm] = useState({ username: "", display_name: "", role: "developer", password: "" });

  const [editUser, setEditUser] = useState(null);
  const [editForm, setEditForm] = useState({ role: "developer", display_name: "", enabled: true });

  const [pwd, setPwd] = useState(null); // {mode:'reset', username} | {mode:'me'}
  const [pwdForm, setPwdForm] = useState({ old_password: "", new_password: "" });

  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [confirmUser, setConfirmUser] = useState(null);

  // API 凭据
  const [projects, setProjects] = useState([]);
  const [credProject, setCredProject] = useState("");
  const [creds, setCreds] = useState([]);
  const [credLabel, setCredLabel] = useState("ci-trigger");
  const [issuedToken, setIssuedToken] = useState(null);

  // 审计日志
  const [auditProject, setAuditProject] = useState("");
  const [auditAction, setAuditAction] = useState("");
  const [auditRows, setAuditRows] = useState([]);
  const [auditError, setAuditError] = useState(null);

  const session = getSession();
  const isAdmin = session?.role === "admin";

  const load = useCallback((query = q, role = roleFilter) => {
    api.get(`/users?q=${encodeURIComponent(query || "")}&role=${encodeURIComponent(role || "")}`)
      .then((u) => { setUsers(u); setError(null); })
      .catch((e) => setError(e.message));
  }, [q, roleFilter]);

  useEffect(() => { load(); }, [load]);

  const loadCreds = useCallback((project) => {
    if (!project) { setCreds([]); return Promise.resolve(); }
    return api.get(`/projects/${encodeURIComponent(project)}/credentials`)
      .then((rows) => { setCreds(rows); setError(null); })
      .catch((e) => setMsg({ tone: "danger", text: `凭据列表不可达：${e.message}` }));
  }, []);

  const loadAudit = useCallback(() => {
    setAuditError(null);
    api.get("/audit/events", { project: auditProject, action: auditAction, limit: 50 })
      .then((rows) => setAuditRows(rows))
      .catch((e) => { setAuditRows([]); setAuditError(e.message); });
  }, [auditProject, auditAction]);

  useEffect(() => {
    api.get("/projects")
      .then((ps) => {
        setProjects(ps);
        setCredProject((cur) => cur || ps[0]?.name || "");
      })
      .catch(() => setProjects([]));
  }, []);

  useEffect(() => {
    if (tab === "tokens") loadCreds(credProject);
  }, [tab, credProject, loadCreds]);

  useEffect(() => {
    // 仅切到审计 Tab 时拉一次；后续按「查询」按钮触发
    if (tab === "audit" && isAdmin) loadAudit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, isAdmin]);

  const createUser = async () => {
    setBusy(true); setMsg(null);
    try {
      await api.post("/users", { username: newForm.username, display_name: newForm.display_name, role: newForm.role, password: newForm.password });
      setShowNew(false); setNewForm({ username: "", display_name: "", role: "developer", password: "" });
      load();
    } catch (e) { setMsg({ tone: "danger", text: `新建失败：${e.message}` }); }
    finally { setBusy(false); }
  };

  const openEdit = (u) => {
    setEditForm({ role: u.role, display_name: u.display_name || "", enabled: !!u.enabled });
    setEditUser(u); setMsg(null);
  };

  const saveEdit = async () => {
    setBusy(true); setMsg(null);
    try {
      await api.put(`/users/${editUser.username}`, { role: editForm.role, display_name: editForm.display_name, enabled: editForm.enabled });
      setEditUser(null); load();
    } catch (e) { setMsg({ tone: "danger", text: `保存失败：${e.message}` }); }
    finally { setBusy(false); }
  };

  const resetPwd = async () => {
    setBusy(true); setMsg(null);
    try {
      if (pwd.mode === "me") await api.post("/auth/change-password", pwdForm);
      else await api.patch(`/users/${pwd.username}/password`, { password: pwdForm.new_password });
      setPwd(null); setPwdForm({ old_password: "", new_password: "" });
      load();
    } catch (e) { setMsg({ tone: "danger", text: `重置失败：${e.message}` }); }
    finally { setBusy(false); }
  };

  const removeUser = async (u) => {
    setBusy(true); setMsg(null);
    try { await api.del(`/users/${u.username}`); setConfirmUser(null); load(); }
    catch (e) { setMsg({ tone: "danger", text: `删除失败：${e.message}` }); setConfirmUser(null); }
    finally { setBusy(false); }
  };

  const issueCred = async () => {
    setBusy(true); setMsg(null); setIssuedToken(null);
    try {
      const r = await api.post(`/projects/${encodeURIComponent(credProject)}/credentials`,
        { label: credLabel });
      setIssuedToken(r.token);
      await loadCreds(credProject);
    } catch (e) { setMsg({ tone: "danger", text: `签发失败：${e.message}` }); }
    finally { setBusy(false); }
  };

  const revokeCred = async (credId) => {
    setBusy(true); setMsg(null);
    try {
      await api.post(`/projects/${encodeURIComponent(credProject)}/credentials/${credId}/revoke`);
      await loadCreds(credProject);
    } catch (e) { setMsg({ tone: "danger", text: `吊销失败：${e.message}` }); }
    finally { setBusy(false); }
  };

  return (
    <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <div><h1 className="mac-h1">权限与凭据</h1><div className="mac-muted">用户管理 · 角色隔离 · 审计日志 · 外部触发 API 凭据</div></div>

      {error && <Callout tone="danger">用户列表不可达：{error}（当前身份 {session?.username || "-"}，用户管理仅管理员可用）。</Callout>}

      <Card solid title="用户与角色" sub={`当前会话：${session?.username || "-"}（${ROLE_CN[session?.role] || session?.role || "?"}）`}>
        <Segmented items={[{ value: "users", label: "用户与角色" }, { value: "tokens", label: "API 凭据" }, { value: "audit", label: "审计日志" }]} value={tab} onChange={setTab} />
        <div style={{ height: 14 }} />

        {tab === "users" && (
          <>
            <Row className="mac-end" style={{ gap: 8 }}>
              <Input value={q} onChange={(e) => { setQ(e.target.value); load(e.target.value, roleFilter); }} placeholder="搜索用户名 / 显示名" style={{ flex: 1 }} />
              <Select value={roleFilter} onChange={(e) => { setRoleFilter(e.target.value); load(q, e.target.value); }} style={{ width: 130 }}>
                <option value="">全部角色</option>
                {Object.entries(ROLE_CN).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </Select>
              {isAdmin && <Button variant="primary" icon="plus" onClick={() => { setMsg(null); setShowNew(true); }}>新建用户</Button>}
            </Row>
            {!isAdmin && <div style={{ height: 6 }} />}
            <Table head={["用户", "显示名", "角色", "状态", "创建时间", "操作"]}>
              {users.length === 0
                ? <tr><td colSpan={6} style={{ textAlign: "center", color: "var(--text-tertiary)" }}>暂无用户</td></tr>
                : users.map((u) => (
                    <tr key={u.username} style={{ opacity: u.enabled ? 1 : 0.5 }}>
                      <td><span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}><Avatar>{u.username[0]}</Avatar> <b>{u.username}</b>{u.username === session?.username && <span className="mac-chip">我</span>}</span></td>
                      <td>{u.display_name || "-"}</td>
                      <td><Badge tone={ROLE_TONE[u.role] || "neutral"} label={ROLE_CN[u.role] || u.role} dot={false} /></td>
                      <td><Badge dot tone={u.enabled ? "success" : "danger"} label={u.enabled ? "启用" : "停用"} /></td>
                      <td className="num">{String(u.created_at || "").slice(0, 10)}</td>
                      <td>
                        {isAdmin && (u.username !== session?.username) && (
                          <Row style={{ gap: 4 }}>
                            <Button variant="ghost" onClick={() => openEdit(u)} style={{ padding: 5 }}>编辑</Button>
                            <Button variant="ghost" onClick={() => { setMsg(null); setPwd({ mode: "reset", username: u.username }); setPwdForm({ old_password: "", new_password: "" }); }} style={{ padding: 5 }}>改密</Button>
                            <Button variant="ghost" onClick={() => { setMsg(null); setConfirmUser(u); }} style={{ padding: 5 }}>删除</Button>
                          </Row>
                        )}
                        {isAdmin && u.username === session?.username && <span className="mac-muted" style={{ fontSize: 12 }}>当前账号</span>}
                        {!isAdmin && "-"}
                      </td>
                    </tr>
                  ))}
            </Table>
            <div style={{ height: 10 }} />
            <Row className="mac-end">
              <Button variant="ghost" icon="key" onClick={() => { setMsg(null); setPwd({ mode: "me" }); setPwdForm({ old_password: "", new_password: "" }); }}>修改我的密码</Button>
            </Row>
          </>
        )}

        {tab === "tokens" && (
          <>
            <Row className="mac-end" style={{ gap: 8, alignItems: "flex-end" }}>
              <div className="mac-field" style={{ width: 200 }}>
                <span className="mac-label">项目</span>
                <Select value={credProject} onChange={(e) => { setCredProject(e.target.value); setIssuedToken(null); }}>
                  {projects.length === 0 && <option value="">暂无项目</option>}
                  {projects.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
                </Select>
              </div>
              <div className="mac-field" style={{ flex: 1 }}>
                <span className="mac-label">凭据名称</span>
                <Input value={credLabel} onChange={(e) => setCredLabel(e.target.value)} placeholder="如 ci-trigger" />
              </div>
              <Button variant="primary" icon="plus" onClick={issueCred}
                disabled={busy || !credProject}>签发凭据</Button>
            </Row>

            {issuedToken && (
              <Callout tone="warning">
                <div>凭据明文仅展示这一次，请立即保存：</div>
                <div className="num" style={{ wordBreak: "break-all", marginTop: 4 }}>{issuedToken}</div>
              </Callout>
            )}

            <Table head={["凭据", "项目", "状态", "创建时间", "操作"]}>
              {creds.length === 0
                ? <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-tertiary)" }}>该项目暂无凭据</td></tr>
                : creds.map((c) => (
                    <tr key={c.cred_id} style={{ opacity: c.revoked ? 0.55 : 1 }}>
                      <td><b>{c.label || c.cred_id}</b><div className="mac-muted num" style={{ fontSize: 12 }}>{c.cred_id}</div></td>
                      <td>{c.project}</td>
                      <td><Badge dot tone={c.revoked ? "danger" : "success"} label={c.revoked ? "已吊销" : "有效"} /></td>
                      <td className="num">{String(c.created_at || "").slice(0, 19).replace("T", " ")}</td>
                      <td>
                        {c.revoked
                          ? <span className="mac-muted" style={{ fontSize: 12 }}>-</span>
                          : <Button variant="ghost" onClick={() => revokeCred(c.cred_id)} disabled={busy} style={{ padding: 5 }}>吊销</Button>}
                      </td>
                    </tr>
                  ))}
            </Table>
            <div className="mac-muted" style={{ fontSize: 12, marginTop: 10 }}>
              凭据仅能触发所属项目的任务（/api/trigger/{"{task_key}"}），明文不落库、不可再次查看。
            </div>
          </>
        )}

        {tab === "audit" && (isAdmin ? (
          <>
            <Row className="mac-end" style={{ gap: 8 }}>
              <Input value={auditProject} onChange={(e) => setAuditProject(e.target.value)}
                placeholder="项目（可选）" style={{ width: 180 }} />
              <Input value={auditAction} onChange={(e) => setAuditAction(e.target.value)}
                placeholder="操作（如 task_save）" style={{ width: 200 }} />
              <Button variant="ghost" icon="refresh" onClick={loadAudit}>查询</Button>
            </Row>
            {auditError && <Callout tone="danger">审计日志不可达：{auditError}</Callout>}
            <Table head={["时间", "操作者", "项目", "操作", "明细"]}>
              {auditRows.length === 0
                ? <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-tertiary)" }}>暂无审计记录</td></tr>
                : auditRows.map((r, i) => (
                    <tr key={`${r.ts}-${i}`}>
                      <td className="num">{String(r.ts || "").slice(0, 19).replace("T", " ")}</td>
                      <td>{r.actor || "-"}</td>
                      <td>{r.project || "-"}</td>
                      <td><Badge tone="neutral" label={r.action || "-"} dot={false} /></td>
                      <td className="mac-muted" style={{ fontSize: 12, maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {JSON.stringify(r.detail || {})}
                      </td>
                    </tr>
                  ))}
            </Table>
          </>
        ) : (
          <Callout tone="warning">审计日志仅管理员可见。</Callout>
        ))}
      </Card>
      {msg && <Callout tone={msg.tone}>{msg.text}</Callout>}

      {confirmUser && (
        <ConfirmDialog
          title="删除用户"
          message={<span>确认删除用户 <b>{confirmUser.username}</b>？此操作不可撤销。</span>}
          okText="删除"
          busy={busy}
          onOk={() => removeUser(confirmUser)}
          onCancel={() => { setConfirmUser(null); setMsg(null); }}
        />
      )}

      {showNew && (
        <Modal title="新建用户" onClose={() => setShowNew(false)} footer={<ModalFooter onCancel={() => setShowNew(false)} onOk={createUser} busy={busy} okText="创建用户" />}>
          <div className="mac-field"><span className="mac-label">用户名</span><Input value={newForm.username} onChange={(e) => setNewForm({ ...newForm, username: e.target.value })} placeholder="登录名" /></div>
          <div className="mac-field"><span className="mac-label">显示名（可选）</span><Input value={newForm.display_name} onChange={(e) => setNewForm({ ...newForm, display_name: e.target.value })} placeholder="展示名称" /></div>
          <div className="mac-field"><span className="mac-label">角色</span>
            <Select value={newForm.role} onChange={(e) => setNewForm({ ...newForm, role: e.target.value })}>
              {Object.entries(ROLE_CN).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </div>
          <div className="mac-field"><span className="mac-label">初始密码</span><Input type="password" value={newForm.password} onChange={(e) => setNewForm({ ...newForm, password: e.target.value })} placeholder="用户首次登录使用" /></div>
        </Modal>
      )}

      {editUser && (
        <Modal title={`编辑用户 ${editUser.username}`} onClose={() => setEditUser(null)} footer={<ModalFooter onCancel={() => setEditUser(null)} onOk={saveEdit} busy={busy} okText="保存修改" />}>
          <div className="mac-field"><span className="mac-label">显示名</span><Input value={editForm.display_name} onChange={(e) => setEditForm({ ...editForm, display_name: e.target.value })} /></div>
          <div className="mac-field"><span className="mac-label">角色</span>
            <Select value={editForm.role} onChange={(e) => setEditForm({ ...editForm, role: e.target.value })}>
              {Object.entries(ROLE_CN).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </div>
          <div className="mac-field"><span className="mac-label">账号状态</span>
            <Select value={editForm.enabled ? "1" : "0"} onChange={(e) => setEditForm({ ...editForm, enabled: e.target.value === "1" })}>
              <option value="1">启用</option><option value="0">停用（禁止登录）</option>
            </Select>
          </div>
        </Modal>
      )}

      {pwd && (
        <Modal title={pwd.mode === "me" ? "修改我的密码" : `重置密码 · ${pwd.username}`} onClose={() => setPwd(null)} footer={<ModalFooter onCancel={() => setPwd(null)} onOk={resetPwd} busy={busy} okText="确定" />}>
          {pwd.mode === "me" && (
            <div className="mac-field"><span className="mac-label">原密码</span><Input type="password" value={pwdForm.old_password} onChange={(e) => setPwdForm({ ...pwdForm, old_password: e.target.value })} /></div>
          )}
          <div className="mac-field"><span className="mac-label">新密码</span><Input type="password" value={pwdForm.new_password} onChange={(e) => setPwdForm({ ...pwdForm, new_password: e.target.value })} /></div>
        </Modal>
      )}
    </div>
  );
}