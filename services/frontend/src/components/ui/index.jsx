import "./ui.css";

/* ---------------- inline 图标（stroke 风格） ---------------- */
const PATHS = {
  dash: <><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>,
  plug: <><path d="M9 2v6M15 2v6" /><path d="M6 8h12l-1 5a5 5 0 0 1-10 0z" /><path d="M12 18v4" /></>,
  query: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
  tasks: <><path d="M6 6h12M6 12h12M6 18h7" /></>,
  lineage: <><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="10" r="2.5" /><circle cx="10" cy="18" r="2.5" /><path d="M8 7.5 16 8.5M7.5 8.5 10.4 16M16 11l-3.5 6" /></>,
  monitor: <><path d="M3 17 9 11l4 4 8-8" /><path d="M15 7h6v6" /></>,
  mdm: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></>,
  shield: <><path d="M12 3 20 6v6c0 4.5-3.5 7.8-8 9-4.5-1.2-8-4.5-8-9V6z" /><path d="m9 12 2 2 4-4" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M4.9 4.9 6.3 6.3m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1 6.3 17.7m11.4-11.4 1.4-1.4" /></>,
  moon: <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5z" />,
  play: <path d="M8 5v14l11-7z" />,
  refresh: <path d="M20 12a8 8 0 1 1-2.3-5.6M20 3v4h-4" />,
  check: <path d="m5 12 4 4L19 7" />,
  alert: <><path d="M12 4 2.5 19h19z" /><path d="M12 10v4" /><circle cx="12" cy="17" r="0.8" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  more: <path d="M5 12h.01M12 12h.01M19 12h.01" />,
  chevron: <path d="m9 6 6 6-6 6" />,
  chevronLeft: <path d="m15 6-6 6 6 6" />,
  link: <><path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1.5 1.5" /><path d="M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1.5-1.5" /></>,
  run: <path d="M7 3v18M7 3l3 2-3 2M7 19l3-2-3-2M11 5h4M11 12h7M11 19h5" />,
  logout: <><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" /><path d="m10 8-4 4 4 4" /><path d="M6 12h10" /></>,
  edit: <><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z" /></>,
  trash: <><path d="M4 7h16" /><path d="M10 11v6M14 11v6" /><path d="M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13" /><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" /></>,
  time: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  eye: <><path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6z" /><circle cx="12" cy="12" r="3" /></>,
  ai: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />,
  chat: <path d="M4 4h16v12H9l-4 4V4z" />,
};

export function Icon({ name, size = 18, style, className }) {
  // style/className 需透传到 svg：此前不透传，调用方写的 transform/verticalAlign 会被静默丢弃
  // （曾导致「返回」按钮上用于镜像箭头的 rotate(180deg) 失效，视觉上与「进入」同向）。
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
      style={style} className={className} aria-hidden="true">
      {PATHS[name] || null}
    </svg>
  );
}

/* ---------------- 卡片 ---------------- */
export function Card({ title, sub, actions, solid, children, className = "", style, pad = true }) {
  return (
    <section className={`mac-card${solid ? " mac-card--solid" : ""} ${className}`} style={style}>
      {(title || actions) && (
        <div className="mac-card__head">
          <div>
            {title && <h3 className="mac-card__title">{title}</h3>}
            {sub && <div className="mac-card__sub">{sub}</div>}
          </div>
          {actions && <div className="mac-row">{actions}</div>}
        </div>
      )}
      <div style={pad ? undefined : {}}>{children}</div>
    </section>
  );
}

const BTN = { primary: "mac-btn--primary", ghost: "mac-btn--ghost", danger: "mac-btn--danger" };
export function Button({ variant = "", icon, children, className = "", block, ...rest }) {
  return (
    <button className={`mac-btn ${BTN[variant] || ""}${block ? " mac-btn--block" : ""} ${className}`} {...rest}>
      {icon && <Icon name={icon} />}
      {children}
    </button>
  );
}
export function IconButton({ icon, className = "", ...rest }) {
  return <button className={`mac-btn mac-btn--ghost ${className}`} style={{ padding: "7px" }} {...rest}><Icon name={icon} /></button>;
}

export function Segmented({ items, value, onChange }) {
  return (
    <div className="mac-segmented" role="tablist">
      {items.map((it) => (
        <button key={it.value} role="tab" aria-selected={it.value === value}
          className={`mac-segmented__item${it.value === value ? " mac-segmented__item--active" : ""}`}
          onClick={() => onChange(it.value)}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------- 表单 ---------------- */
export function Field({ label, hint, children, className = "" }) {
  return (
    <label className={`mac-field ${className}`}>
      {(label || hint) && <span className="mac-label">{label}</span>}
      {children}
      {hint && <span className="mac-muted" style={{ fontSize: 11 }}>{hint}</span>}
    </label>
  );
}
export const Input = ({ className = "", ...rest }) => <input className={`mac-input ${className}`} {...rest} />;
export const Select = ({ className = "", children, ...rest }) => <select className={`mac-select ${className}`} {...rest}>{children}</select>;
export const Textarea = ({ className = "", ...rest }) => <textarea className={`mac-textarea ${className}`} {...rest} />;
export { Field as Label };
export function Row({ children, className = "", grow }) {
  return <div className={`mac-row ${grow ? "mac-grow" : ""} ${className}`}>{children}</div>;
}

/* ---------------- 徽章 ---------------- */
const BADGE_TONE = {
  success: "mac-badge--success", warning: "mac-badge--warning", danger: "mac-badge--danger",
  info: "mac-badge--info", neutral: "mac-badge--neutral",
};
export function Badge({ tone = "neutral", label, dot = true }) {
  return (
    <span className={`mac-badge ${BADGE_TONE[tone] || ""}`}>
      {dot && <span className="mac-badge__dot" />}
      {label}
    </span>
  );
}

/* ---------------- 表格 ---------------- */
export function Table({ head, children }) {
  return (
    <div style={{ overflow: "auto" }}>
      <table className="mac-table">
        <thead>
          <tr>{head.map((h, i) => <th key={i}>{h}</th>)}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/* ---------------- 分页 ----------------
   服务端分页：页码/每页条数均为受控值，翻页由调用方重新拉取数据。 */
export function Pager({ page = 1, pageSize = 20, total = 0, pageSizes = [10, 20, 50], onPageChange, onPageSizeChange }) {
  const pages = Math.max(1, Math.ceil((total || 0) / (pageSize || 1)));
  const from = total > 0 ? (page - 1) * pageSize + 1 : 0;
  const to = Math.min(total, page * pageSize);
  return (
    <div className="mac-pager">
      <span className="mac-pager__info">共 {total} 条{total > 0 ? `，当前 ${from}-${to}` : ""}</span>
      <div className="mac-row" style={{ gap: 8, flexWrap: "nowrap" }}>
        <select className="mac-select mac-pager__size" value={pageSize} aria-label="每页条数"
          onChange={(e) => onPageSizeChange?.(Number(e.target.value))}>
          {pageSizes.map((s) => <option key={s} value={s}>每页 {s} 条</option>)}
        </select>
        <Button variant="ghost" disabled={page <= 1} onClick={() => onPageChange?.(page - 1)}>上一页</Button>
        <span className="mac-pager__page">{page} / {pages}</span>
        <Button variant="ghost" disabled={page >= pages} onClick={() => onPageChange?.(page + 1)}>下一页</Button>
      </div>
    </div>
  );
}

/* ---------------- 指标卡 ---------------- */
export function StatCard({ label, value, delta, up, icon, tone = "info" }) {
  const bg = { info: "var(--info-bg)", success: "var(--success-bg)", warning: "var(--warning-bg)", danger: "var(--danger-bg)" };
  return (
    <Card solid pad>
      <div className="mac-row" style={{ justifyContent: "space-between" }}>
        <div className="mac-stat">
          <span className="mac-stat__label">{label}</span>
          <span className="mac-stat__value">{value}</span>
          {delta && <span className={`mac-stat__delta ${up ? "mac-stat--up" : "mac-stat--down"}`}>{up ? "↑" : "↓"} {delta}</span>}
        </div>
        <span className="mac-stat__icon" style={{ backgroundColor: bg[tone], color: `var(--${tone})` }}>
          <Icon name={icon} size={18} />
        </span>
      </div>
    </Card>
  );
}

/* ---------------- 提示 ---------------- */
const CALLOUT_TONE = { success: "mac-callout--success", warning: "mac-callout--warning", danger: "mac-callout--danger", info: "mac-callout--info" };
export function Callout({ tone = "info", icon, children }) {
  return <div className={`mac-callout ${CALLOUT_TONE[tone]}`}>{icon && <Icon name={icon} size={16} />}<div>{children}</div></div>;
}

/* ---------------- 空状态 / 骨架 ---------------- */
export function Empty({ icon = "plug", title, desc }) {
  return (
    <div className="mac-empty">
      <Icon name={icon} />
      <div><b>{title}</b></div>
      {desc && <div style={{ fontSize: 12 }}>{desc}</div>}
    </div>
  );
}
export function Skeleton({ width = "100%", height = 14, style }) {
  return <span className="mac-skeleton" style={{ display: "inline-block", width, height, ...style }} />;
}

/* ---------------- 通用 ---------------- */
export function Chiptone({ children, tone = "neutral" }) {
  return <span className="mac-chip" style={{ color: `var(--${tone})`, background: `var(--${tone}-bg)` }}>{children}</span>;
}
export function Avatar({ children, bg = "var(--accent-grad)" }) {
  return <span className="mac-avatar" style={{ background: bg }}>{children}</span>;
}

/* ---------------- 列表展示用短格式 ----------------
   表格里直接铺满 24~32 字符的 run_id / task_key / ISO 时间会把列挤爆，后续列被推出
   可视区（表现为「显示不全」）。此处统一取前缀 + 省略号；完整值由调用方用 title 提示。
   取 8 位前缀与任务页/对话页既有的 slice(0, 8) 惯例一致。 */
export const shortId = (s, n = 8) => (!s ? "-" : (String(s).length > n ? `${String(s).slice(0, n)}…` : String(s)));
export const shortTime = (s) => (!s ? "-" : String(s).slice(5, 16).replace("T", " "));

export function Modal({ title, onClose, width = 560, children, footer }) {
  return (
    <div className="mac-modal" role="dialog" aria-modal="true" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="mac-modal__panel" style={{ width, maxWidth: "94vw" }}>
        <header className="mac-modal__head">
          <h3 className="mac-card__title" style={{ margin: 0 }}>{title}</h3>
          <button className="mac-btn mac-btn--ghost" onClick={onClose} aria-label="关闭" style={{ padding: 6 }}><Icon name="more" /></button>
        </header>
        <div className="mac-modal__body">{children}</div>
        {footer && <footer className="mac-modal__foot">{footer}</footer>}
      </div>
    </div>
  );
}

export function ModalFooter({ onCancel, onOk, busy, okText = "确定", cancelText = "取消", okVariant = "primary", leading }) {
  return (
    <div className="mac-row mac-end" style={{ gap: 8, width: "100%", justifyContent: leading ? "space-between" : "flex-end" }}>
      {leading ? <div className="mac-row" style={{ gap: 8, alignItems: "center" }}>{leading}</div> : null}
      <div className="mac-row" style={{ gap: 8, alignItems: "center" }}>
        <Button variant="ghost" onClick={onCancel} disabled={busy}>{cancelText}</Button>
        <Button variant={okVariant} icon="check" onClick={onOk} disabled={busy}>{busy ? "处理中…" : okText}</Button>
      </div>
    </div>
  );
}

/* ---------------- 二次确认 ----------------
   不使用 window.confirm：内嵌预览（sandbox iframe 无 allow-modals）会静默忽略原生弹窗并返回 false，
   导致操作被无反馈地取消。这里统一用应用内 Modal，任意宿主环境均可交互。 */
export function ConfirmDialog({ title = "确认操作", message, okText = "确定", cancelText = "取消", tone = "danger", busy, onOk, onCancel }) {
  return (
    <Modal title={title} onClose={onCancel} width={420} footer={
      <ModalFooter onCancel={onCancel} onOk={onOk} busy={busy} okText={okText} cancelText={cancelText} okVariant={tone} />
    }>
      <div style={{ lineHeight: 1.6 }}>{message}</div>
    </Modal>
  );
}