import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { Button, Callout, Card, Empty, Icon, Input, Segmented, Select, Skeleton, Table } from "../components/ui";

const MODES = [{ value: "rel", label: "关系库" }, { value: "api", label: "REST/API" }];
const REL_CTYPES = ["mysql", "postgresql"];
const KINDS = [
  { value: "all", label: "全部" },
  { value: "table", label: "表" },
  { value: "view", label: "视图" },
];
// 同一连接枚举结果的短时缓存时长（毫秒）；手动刷新会绕过缓存
const CATALOG_TTL = 30_000;

const ckey = (c) => c.conn_key || c.key;
const ctype = (c) => String(c.ctype || c.type || "").toLowerCase();

function cellVal(row, col, idx) {
  const v = Array.isArray(row) ? row[idx] : (row ? row[col] : undefined);
  return v === null || typeof v === "undefined" ? "" : String(v);
}

export default function Query() {
  const [mode, setMode] = useState("rel");
  const [conns, setConns] = useState([]);
  const [connKey, setConnKey] = useState("");
  const [sql, setSql] = useState("SELECT * FROM table_name LIMIT 100;");
  const [result, setResult] = useState(null);
  const [meta, setMeta] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  // 左列：库表/视图导航
  const [catalog, setCatalog] = useState(null);
  const [catErr, setCatErr] = useState(null);
  const [catLoading, setCatLoading] = useState(false);
  const [catNonce, setCatNonce] = useState(0);
  const catCache = useRef(new Map());
  const [schema, setSchema] = useState("");
  const [kind, setKind] = useState("all");
  const [keyword, setKeyword] = useState("");
  const [picked, setPicked] = useState("");

  useEffect(() => {
    api.get("/connections")
      .then((rows) => setConns(rows || []))
      .catch(() => setConns([]));
  }, []);

  // 按模式收窄可选连接：关系库模式仅列 MySQL/PostgreSQL
  const available = useMemo(() => conns.filter((c) => (
    mode === "rel" ? REL_CTYPES.includes(ctype(c)) : ctype(c) === "api"
  )), [conns, mode]);

  // 切换连接/模式时一并重置左列筛选，避免（a）残留上一个连接的筛选条件，
  // （b）用上一个连接的 schema 发起一次多余的枚举请求
  function resetPanel() {
    setSchema(""); setKind("all"); setKeyword(""); setPicked("");
    setCatalog(null); setCatErr(null);
  }

  function switchConn(value) {
    resetPanel();
    setConnKey(value);
  }

  function switchMode(value) {
    resetPanel();
    setMode(value);
  }

  useEffect(() => {
    const keys = available.map(ckey);
    if (keys.includes(connKey)) return;
    setSchema(""); setKind("all"); setKeyword(""); setPicked("");
    setCatalog(null); setCatErr(null);
    setConnKey(keys[0] || "");
  }, [available, connKey]);

  const conn = available.find((c) => ckey(c) === connKey) || null;
  const project = conn?.project || "demo";

  const catKey = `${connKey}|${project}|${schema}`;

  // 依赖全部为原始值：避免 conn/available 的对象身份变化（连接列表重载）触发多余枚举
  useEffect(() => {
    if (mode !== "rel" || !connKey) {
      setCatLoading(false);
      setCatalog(null);
      setCatErr(null);
      return undefined;
    }
    // 短时缓存：同一连接内重复切换（含切走再切回）不再往返
    const hit = catCache.current.get(catKey);
    if (hit && Date.now() - hit.at < CATALOG_TTL) {
      setCatalog(hit.value);
      setCatErr(null);
      setCatLoading(false);
      return undefined;
    }
    let alive = true;
    setCatLoading(true); setCatErr(null);
    const qs = new URLSearchParams({ project });
    if (schema) qs.set("schema", schema);
    api.get(`/connections/${encodeURIComponent(connKey)}/catalog?${qs.toString()}`)
      .then((res) => {
        catCache.current.set(catKey, { at: Date.now(), value: res });
        if (alive) setCatalog(res);
      })
      .catch((e) => { if (alive) { setCatalog(null); setCatErr(e.message || "库表枚举失败"); } })
      .finally(() => { if (alive) setCatLoading(false); });
    return () => { alive = false; };
  }, [mode, connKey, project, schema, catNonce, catKey]);

  function reloadCatalog() {
    catCache.current.delete(catKey);
    setCatNonce((n) => n + 1);
  }

  const allItems = catalog?.items || [];
  const items = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    return allItems.filter((it) => (kind === "all" || it.kind === kind)
      && (!kw || String(it.name).toLowerCase().includes(kw)));
  }, [allItems, kind, keyword]);

  async function run() {
    setErr(null); setResult(null); setMeta(null);
    if (!connKey) { setErr("无可用的连接，请先在「连接管理」中创建"); return; }
    setBusy(true);
    try {
      if (mode === "api") {
        const res = await api.post(`/connections/${connKey}/debug`, { project, limit: 5 });
        setResult({ columns: [], rows: [], sample: res.sample });
        setMeta(`API 样例 · HTTP ${res.status ?? "-"}`);
      } else {
        const res = await api.post(`/connections/${connKey}/query`, { project, sql, limit: 100 });
        setResult(res);
        setMeta(`${res.count} 行 · truncated=${res.truncated}`);
      }
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  function pick(item) {
    setSql(item.snippet);   // 仅预填，不自动执行
    setPicked(`${item.schema}.${item.name}`);
  }

  function renderCatalog() {
    if (catLoading && !catalog) return <Skeleton height={16} width="80%" style={{ marginTop: 8 }} />;
    if (catErr) return <Callout tone="danger" icon="alert">{catErr}</Callout>;
    if (!connKey) {
      return <Empty icon="plug" title="暂无可用连接" desc="请先在「连接管理」创建 MySQL / PostgreSQL 连接" />;
    }
    if (!catalog) return null;
    if (allItems.length === 0) {
      return <Empty icon="query" title="该库下没有表或视图" desc="确认连接库名与账号的读取权限" />;
    }
    return (
      <>
        <div className="mac-row" style={{ gap: 8, marginTop: 12 }}>
          <Input value={keyword} onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索表或视图" style={{ flex: 1 }} />
          <Button variant="ghost" icon="refresh" title="重新枚举"
            onClick={reloadCatalog} />
        </div>
        <div className="mac-row" style={{ marginTop: 8 }}>
          <Segmented items={KINDS} value={kind} onChange={setKind} />
        </div>
        {items.length === 0
          ? <div className="mac-muted" style={{ marginTop: 8 }}>没有匹配的对象</div>
          : (
            <div className="mac-tree" role="list">
              {items.map((it) => {
                const id = `${it.schema}.${it.name}`;
                return (
                  <button
                    key={`${it.kind}:${id}`}
                    type="button"
                    role="listitem"
                    className={`mac-tree__item${picked === id ? " mac-tree__item--active" : ""}`}
                    title={`点击填入查询语句：${it.name}`}
                    onClick={() => pick(it)}
                  >
                    <Icon name={it.kind === "view" ? "eye" : "tasks"} size={14} />
                    <span className="mac-tree__name">{it.name}</span>
                    <span className="mac-tree__kind">{it.kind === "view" ? "视图" : "表"}</span>
                  </button>
                );
              })}
            </div>
          )}
      </>
    );
  }

  return (
    <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <div className="mac-row mac-end" style={{ justifyContent: "space-between" }}>
        <div>
          <h1 className="mac-h1">只读查询 / 接口调试</h1>
          <div className="mac-muted">左选连接与库表 · 右写 SQL 看结果 · 只读守卫：单条 SELECT · 拒写 · 行数上限与超时</div>
        </div>
        <Segmented items={MODES} value={mode} onChange={switchMode} />
      </div>

      <div className="mac-workbench">
        <aside className="mac-workbench__side">
          <Card solid title="数据连接"
            sub={mode === "rel" ? "仅关系库（MySQL / PostgreSQL）" : "仅 REST/API"}>
            <Select value={connKey} onChange={(e) => switchConn(e.target.value)}>
              {available.length === 0 && <option value="">（无可用连接）</option>}
              {available.map((c) => (
                <option key={ckey(c)} value={ckey(c)} title={ckey(c)}>
                  {c.name} ({String(ckey(c)).slice(0, 8)}…)
                </option>
              ))}
            </Select>
          </Card>

          {mode === "rel" ? (
            <Card solid title="表 / 视图"
              sub={catalog
                ? `${items.length}/${allItems.length} 个对象${catalog.truncated ? "（已截断，请用关键字收窄）" : ""}`
                : "点击表名填入查询语句"}>
              {catalog?.schemas?.length > 1 && (
                <div style={{ marginBottom: 12 }}>
                  <Select value={schema || catalog.default_schema}
                    onChange={(e) => setSchema(e.target.value)}>
                    {catalog.schemas.map((s) => <option key={s} value={s}>{s}</option>)}
                  </Select>
                </div>
              )}
              {renderCatalog()}
            </Card>
          ) : (
            <Card solid title="接口调试" sub="REST/API 无库表导航">
              <div className="mac-muted">选择 API 连接后点击执行，返回样例字段可送入字段选择向导。</div>
            </Card>
          )}
        </aside>

        <section className="mac-workbench__main">
          <Card solid>
            <div className="mac-row mac-end" style={{ justifyContent: "space-between" }}>
              <span className="mac-card__title" style={{ margin: 0 }}>
                {mode === "rel" ? "SQL 编辑器" : "请求调试"}
              </span>
              <div className="mac-row" style={{ gap: 8 }}>
                {mode === "rel" && catalog?.default_schema && (
                  <span className="mac-muted" style={{ fontSize: 11 }}>默认库：{catalog.default_schema}</span>
                )}
                <Button variant="primary" icon="play" onClick={run} disabled={busy}>
                  {busy ? "执行中…" : "执行"}
                </Button>
              </div>
            </div>
            <div style={{ height: 12 }} />
            {mode === "rel" ? (
              <>
                <textarea
                  className="mac-textarea"
                  value={sql} onChange={(e) => setSql(e.target.value)}
                  onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); run(); } }}
                  style={{ fontFamily: "var(--font-mono)", minHeight: 200, fontSize: 13 }}
                  spellCheck={false}
                  placeholder="SELECT * FROM table_name LIMIT 100;"
                />
                <div className="mac-muted" style={{ fontSize: 11, marginTop: 6 }}>
                  ⌘/Ctrl + Enter 执行 · 仅允许单条 SELECT · 点击左列表名可填入基础语句
                </div>
              </>
            ) : (
              <>
                <Callout tone="info" icon="plug">
                  接口调试不输入 SQL：点击「执行」将以该连接的 {conn?.meta?.method || "GET"} 方法与认证配置发起请求，
                  返回样例供字段选择参考。
                </Callout>
                <div className="mac-muted" style={{ fontSize: 11, marginTop: 8 }}>
                  方法与认证在「连接管理」中维护 · 返回样例字段可送入字段选择向导
                </div>
              </>
            )}
          </Card>

          {err && (
            <Callout tone="danger" icon="alert">
              查询失败：{err}。若为写入、多语句或超出边界，将被只读守卫拒绝。
            </Callout>
          )}

          {result && (
            <Card className="mac-pop"
              title={`查询结果${meta ? `（${meta}）` : ""}`}>
              {result.columns?.length
                ? (
                  <Table head={result.columns}>
                    {result.rows.map((r, i) => (
                      <tr key={i}>
                        {result.columns.map((c, j) => (
                          <td key={c} className="num">{cellVal(r, c, j)}</td>
                        ))}
                      </tr>
                    ))}
                  </Table>
                )
                : (
                  <pre style={{ fontSize: 12, margin: 0, color: "var(--text-secondary)", whiteSpace: "pre-wrap" }}>
                    {JSON.stringify(result.sample ?? result, null, 2)}
                  </pre>
                )}
            </Card>
          )}
        </section>
      </div>
    </div>
  );
}
