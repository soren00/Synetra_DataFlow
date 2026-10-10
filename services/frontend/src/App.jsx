import { Fragment, useState } from "react";
import { useTheme } from "./theme/ThemeProvider";
import { Icon } from "./components/ui";
import { clearSession, logout, getSession } from "./api/client";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Connections from "./pages/Connections";
import Query from "./pages/Query";
import Projects from "./pages/Projects";
import Schedules from "./pages/Schedules";
import Lineage from "./pages/Lineage";
import Monitor from "./pages/Monitor";
import MDM from "./pages/MDM";
import Security from "./pages/Security";
import LLMConfig from "./pages/LLMConfig";
import ConversationTask from "./pages/ConversationTask";

const NAV = [
  { key: "overview", label: "总览", icon: "dash", section: "工作台", page: <Overview /> },
  { key: "connections", label: "连接管理", icon: "plug", section: "数据", page: <Connections /> },
  { key: "query", label: "只读查询", icon: "query", section: "数据", page: <Query /> },
  { key: "tasks", label: "ETL 项目", icon: "tasks", section: "数据", page: <Projects /> },
  { key: "nl-task", label: "AI ETL 任务", icon: "chat", section: "数据", page: <ConversationTask /> },
  { key: "schedules", label: "定时任务", icon: "time", section: "数据", page: <Schedules /> },
  { key: "lineage", label: "血缘图谱", icon: "lineage", section: "治理", page: <Lineage /> },
  { key: "monitor", label: "监控告警", icon: "monitor", section: "治理", page: <Monitor /> },
  { key: "mdm", label: "主数据", icon: "mdm", section: "治理", page: <MDM /> },
  { key: "security", label: "权限凭据", icon: "shield", section: "系统", page: <Security /> },
  { key: "llm", label: "AI 管理", icon: "ai", section: "系统", page: <LLMConfig />, admin: true },
];

export default function App() {
  const { theme, toggle } = useTheme();
  const [active, setActive] = useState("overview");
  const [session, setSession] = useState(() => (typeof window !== "undefined" ? getSession() : null));
  const visibleNav = NAV.filter((n) => !n.admin || session?.role === "admin");
  const page = visibleNav.find((n) => n.key === active) || visibleNav[0];

  if (!session) return <Login onAuthed={setSession} />;

  return (
    <div className="app-window">
      <header className="mac-toolbar">
        <div className="mac-toolbar__brand">
          <span className="mac-toolbar__logo">S</span>
          <span className="mac-toolbar__title">Synetra DataFlow</span>
        </div>
        <span className="mac-chip">macOS</span>
        <div className="mac-toolbar__spacer" />
        <button className="mac-btn mac-btn--ghost" onClick={toggle} aria-label="切换主题">
          <Icon name={theme === "light" ? "moon" : "sun"} size={16} />
        </button>
        <button className="mac-btn mac-btn--ghost" aria-label="通知">
          <Icon name="alert" size={16} />
        </button>
        <span className="mac-avatar" title={session.username} style={{ width: 30, height: 30, borderRadius: "50%" }}>
          {session.username[0].toUpperCase()}
        </span>
        <button className="mac-btn mac-btn--ghost" onClick={async () => { await logout(); setSession(null); }} title="退出登录">
          <Icon name="logout" size={16} />
        </button>
      </header>

      <div className="app-shell">
        <aside className="app-side">
          <nav className="mac-side">
            {visibleNav.map((n, i) => (
              <Fragment key={n.key}>
                {n.section && (i === 0 || visibleNav[i - 1].section !== n.section) && (
                  <div className="mac-side__section">{n.section}</div>
                )}
                <button
                  className={`mac-nav-item${active === n.key ? " mac-nav-item--active" : ""}`}
                  onClick={() => setActive(n.key)}
                  role="tab"
                  aria-selected={active === n.key}
                >
                  <Icon name={n.icon} />
                  <span>{n.label}</span>
                  {(n.key === "monitor") && <span style={{ marginLeft: "auto", width: 8, height: 8, borderRadius: "50%", background: "var(--danger)" }} />}
                </button>
              </Fragment>
            ))}
          </nav>
        </aside>
        <main className="app-main">{page.page}</main>
      </div>
    </div>
  );
}