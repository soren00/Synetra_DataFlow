import { useState } from "react";
import { Button, Callout, Icon, Input } from "../components/ui";
import { login } from "../api/client";

export default function Login({ onAuthed }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const s = await login(username.trim(), password);
      onAuthed?.(s);
    } catch (err) {
      setError(err.message || "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-window">
      <div style={{
        display: "grid", placeItems: "center", minHeight: "100vh",
        background: "radial-gradient(1200px 600px at 20% 0%, var(--bg-elevated), transparent), var(--bg)",
      }}>
        <form className="mac-card mac-card--solid" onSubmit={submit}
          style={{ width: 380, maxWidth: "90vw", padding: "36px 40px", display: "flex", flexDirection: "column", gap: 6 }}>
          <div className="mac-toolbar__brand" style={{ marginBottom: 6 }}>
            <span className="mac-toolbar__logo">S</span>
            <span className="mac-toolbar__title">Synetra DataFlow</span>
          </div>
          <div className="mac-muted" style={{ marginBottom: 18 }}>数据管道 / 治理 / 监控一体化平台</div>

          {error && <Callout tone="danger">{error}</Callout>}

          <label className="mac-label" htmlFor="login-user">用户名</label>
          <Input id="login-user" placeholder="admin" autoComplete="username"
            value={username} onChange={(e) => setUsername(e.target.value)} />

          <label className="mac-label" htmlFor="login-pass" style={{ marginTop: 12 }}>密码</label>
          <Input id="login-pass" type="password" placeholder="••••••" autoComplete="current-password"
            value={password} onChange={(e) => setPassword(e.target.value)} />

          <div style={{ height: 10 }} />
          <Button variant="primary" type="submit" icon="check" disabled={busy} block>
            {busy ? "登录中…" : "登录"}
          </Button>

          <div className="mac-muted" style={{ fontSize: 12, marginTop: 14, lineHeight: 1.6 }}>
            试用账号：admin / admin（管理员）、dev / dev（开发者）。
            登录后会话按 <span className="mac-chip">X-Synetra-User</span> 注入，角色决定读写权限。
          </div>
        </form>
      </div>
    </div>
  );
}