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

  const session = getSession();
  const isAdmin = session?.role === "admin";

  const load = useCallback((query = q, role = roleFilter) => {
    api.get(`/users?q=${encodeURIComponent(query || "")}&role=${encodeURIComponent(role || "")}`)
      .then((u) => { setUsers(u); setError(null); })
      .catch((e) => setError(e.message));
  }, [q, roleFilter]);

  useEffect(() => { load(); }, [load]);

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
          <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>
            API 凭据通过 <span className="mac-chip">POST /api/projects/{"{project}"}/credentials</span> 签发，明文仅在签发时展示一次，可随时吊销，按项目隔离。仅对触发接口 /api/trigger/{"{task_key}"} 授权。
          </div>
        )}
        {tab === "audit" && (
          <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>
            配置变更 task_save / 凭据签发 credential_issue / 任务触发 external_api / 对账 reconcile 等操作均已写入审计日志。列表接口按需暴露。
          </div>
        )}
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