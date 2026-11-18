/* 多步清洗流水线画布：拖拽编排、连线即时校验、节点配置抽屉、节点级预览与运行指标徽标。
   画布只负责编排；节点内部的字段规则复用 FieldRulesEditor（与后端步骤注册表同源驱动）。 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow,
  addEdge, useEdgesState, useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Badge, Button, Callout, Card, Input, Modal, Select, Textarea } from "./ui";
import { api } from "../api/client";
import FieldRulesEditor from "./FieldRulesEditor";

const CATEGORY_CN = { source: "源", merge: "合并", row: "行级处理", batch: "整批处理", target: "目标" };
const CATEGORY_TONE = { source: "info", merge: "warning", row: "neutral", batch: "neutral", target: "success" };
const COL_W = 220;

// ---------- 配置序列化辅助（不同控件 ↔ 配置值） ----------

const listToText = (v) => (Array.isArray(v) ? v.join(", ") : "");
const textToList = (t) => String(t || "").split(",").map((s) => s.trim()).filter(Boolean);

function sortToText(keys) {
  return (keys || []).map((k) => `${k.field}${k.desc ? ":desc" : ""}`).join(", ");
}
function textToSort(t) {
  return String(t || "").split(",").map((s) => s.trim()).filter(Boolean)
    .map((s) => {
      const [field, dir] = s.split(":");
      return { field: field.trim(), desc: dir === "desc" };
    });
}
function aggToText(aggs) {
  return (aggs || []).map((a) => `${a.func}(${a.field || "*"})->${a.target || ""}`).join("\n");
}
function textToAgg(t) {
  return String(t || "").split("\n").map((s) => s.trim()).filter(Boolean).map((line) => {
    const m = line.match(/^(\w+)\(([^)]*)\)(?:->(.*))?$/);
    if (!m) return null;
    return { func: m[1], field: m[2] || "*", target: (m[3] || "").trim() };
  }).filter(Boolean);
}
function mapToText(m) {
  return Object.entries(m || {}).map(([k, v]) => `${k}=${v}`).join("\n");
}
function textToMap(t) {
  const out = {};
  String(t || "").split("\n").forEach((line) => {
    const i = line.indexOf("=");
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  });
  return out;
}

// ---------- 图与配置互转 ----------

function defaultConfig(spec) {
  const cfg = {};
  (spec?.config_schema?.fields || []).forEach((f) => {
    if (f.default !== undefined) cfg[f.key] = f.default;
  });
  if (spec?.rules_editor) cfg.rules = [];
  return cfg;
}

function layout(pipeline) {
  // 简单分层布局：按拓扑深度排布，保存的配置不携带坐标
  const ids = (pipeline?.nodes || []).map((n) => n.id);
  const depth = Object.fromEntries(ids.map((i) => [i, 0]));
  for (let round = 0; round < ids.length; round += 1) {
    let changed = false;
    (pipeline?.edges || []).forEach((e) => {
      if (depth[e.target] < depth[e.source] + 1) {
        depth[e.target] = depth[e.source] + 1;
        changed = true;
      }
    });
    if (!changed) break;
  }
  const perLevel = {};
  return (pipeline?.nodes || []).map((n) => {
    const d = depth[n.id] || 0;
    perLevel[d] = (perLevel[d] || 0) + 1;
    return {
      id: n.id,
      type: "syn",
      position: { x: 60 + d * (COL_W + 60), y: 60 + (perLevel[d] - 1) * 110 },
      data: { stepType: n.type, name: n.name || "", config: n.config || {} },
    };
  });
}

function toFlowEdges(pipeline) {
  return (pipeline?.edges || []).map((e) => ({
    id: `${e.source}->${e.target}`,
    source: e.source,
    target: e.target,
    markerEnd: { type: MarkerType.ArrowClosed },
  }));
}

// ---------- 自定义节点 ----------

function SynNode({ data, selected }) {
  const spec = data.spec;
  const metric = data.metric;
  const failed = metric?.status === "failed";
  const tone = failed ? "danger" : CATEGORY_TONE[data.category] || "neutral";
  return (
    <div
      style={{
        width: 190, padding: "10px 12px", borderRadius: "var(--r-md)",
        background: "var(--surface)",
        border: `1.5px solid ${selected ? "var(--accent)" : "var(--border)"}`,
        boxShadow: selected ? "var(--shadow-md)" : "var(--shadow-sm)",
        fontFamily: "var(--font-ui)",
      }}
    >
      <Handle type="target" position={Position.Left} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
        <div style={{ fontSize: "var(--fs-sm)", fontWeight: 600, color: "var(--text)" }}>
          {data.name || spec?.label || data.stepType}
        </div>
        <Badge tone={tone} dot={false}
               label={CATEGORY_CN[data.category] || data.category} />
      </div>
      <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-tertiary)", marginTop: 4 }}>
        {spec?.label || data.stepType}
      </div>
      {metric && (
        <div style={{ marginTop: 6, fontSize: "var(--fs-xs)", color: failed ? "var(--danger)" : "var(--text-secondary)" }}>
          {failed ? "失败" : `${metric.in_rows} → ${metric.out_rows}`}
          {!failed && ` · ${Number(metric.elapsed_ms || 0).toFixed(1)}ms`}
        </div>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const NODE_TYPES = { syn: SynNode };

// ---------- 连线合法性 ----------

function wouldCycle(edges, source, target) {
  // 从 target 出发能否走回 source
  const adj = {};
  edges.forEach((e) => { (adj[e.source] = adj[e.source] || []).push(e.target); });
  (adj[source] = adj[source] || []).push(target);
  const stack = [target];
  const seen = new Set();
  while (stack.length) {
    const cur = stack.pop();
    if (cur === source) return true;
    if (seen.has(cur)) continue;
    seen.add(cur);
    (adj[cur] || []).forEach((n) => stack.push(n));
  }
  return false;
}

// ---------- 主组件 ----------

export default function PipelineCanvas({ taskKey, initialPipeline, onChange, fieldErrors = {} }) {
  const [specs, setSpecs] = useState([]);
  const [specMap, setSpecMap] = useState({});
  const [nodes, setNodes, onNodesChange] = useNodesState(layout(initialPipeline));
  const [edges, setEdges, onEdgesChange] = useEdgesState(toFlowEdges(initialPipeline));
  const [selectedId, setSelectedId] = useState(null);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(null);
  const [schemaInfo, setSchemaInfo] = useState(null);
  const [schemaBusy, setSchemaBusy] = useState(false);
  const [schemaError, setSchemaError] = useState("");
  const [metrics, setMetrics] = useState({});
  const [batchSize, setBatchSize] = useState(initialPipeline?.batch_size || 5000);
  const [commitSize, setCommitSize] = useState(initialPipeline?.commit_size || 5000);
  const [conns, setConns] = useState([]);

  // 连接清单：节点配置里的 conn_key 用下拉选择，界面只呈现连接名
  useEffect(() => {
    let alive = true;
    api.get("/connections").then((cs) => { if (alive) setConns(cs || []); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // 步骤清单（后端注册表为唯一事实源）
  useEffect(() => {
    let alive = true;
    api.get("/pipeline/steps").then((r) => {
      if (!alive) return;
      setSpecs(r.steps || []);
      setSpecMap(Object.fromEntries((r.steps || []).map((s) => [s.type, s])));
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // 最近一次运行的步级指标（节点徽标）
  useEffect(() => {
    if (!taskKey) return undefined;
    let alive = true;
    api.get("/runs", { task_key: taskKey, page_size: 10 }).then(async (r) => {
      const runId = (r.runs || [])[0]?.run_id;
      if (!runId || !alive) return;
      const steps = await api.get(`/runs/${runId}/steps`);
      if (!alive) return;
      setMetrics(Object.fromEntries((steps.steps || []).map((s) => [s.node_id, s])));
    }).catch(() => {});
    return () => { alive = false; };
  }, [taskKey]);

  const decorated = useMemo(() => nodes.map((n) => ({
    ...n,
    data: {
      ...n.data,
      spec: specMap[n.data.stepType],
      category: specMap[n.data.stepType]?.category || "row",
      metric: metrics[n.id],
    },
  })), [nodes, specMap, metrics]);

  // 同步给父级（画布是编排的唯一事实源）；内容未变化时不再回调，避免父级重渲染循环
  const lastEmit = useRef("");
  const emit = useCallback((nextNodes, nextEdges, batch, commit) => {
    const payload = {
      version: 1,
      nodes: nextNodes.map((n) => ({
        id: n.id, type: n.data.stepType, name: n.data.name || "", config: n.data.config || {},
      })),
      edges: nextEdges.map((e) => ({ source: e.source, target: e.target })),
      batch_size: batch ?? batchSize,
      commit_size: commit ?? commitSize,
    };
    const serialized = JSON.stringify(payload);
    if (serialized === lastEmit.current) return;
    lastEmit.current = serialized;
    onChange?.(payload);
  }, [onChange, batchSize, commitSize]);

  useEffect(() => { emit(nodes, edges, batchSize, commitSize); }, [nodes, edges, batchSize, commitSize, emit]);

  const onConnect = useCallback((conn) => {
    const { source, target } = conn;
    if (!source || !target) return;
    if (source === target) { setError("不能把节点连回自身"); return; }
    const srcNode = nodes.find((n) => n.id === source);
    const tgtNode = nodes.find((n) => n.id === target);
    const srcSpec = specMap[srcNode?.data?.stepType];
    const tgtSpec = specMap[tgtNode?.data?.stepType];
    if (srcSpec && srcSpec.max_out === 0) { setError(`${srcSpec.label} 是目标步骤，不能有输出`); return; }
    if (tgtSpec && tgtSpec.max_in === 0) { setError(`${tgtSpec.label} 是源步骤，不能有输入`); return; }
    const outCount = edges.filter((e) => e.source === source).length;
    const inCount = edges.filter((e) => e.target === target).length;
    if (srcSpec && srcSpec.max_out >= 0 && outCount >= srcSpec.max_out) {
      setError(`${srcSpec.label} 最多只能有 ${srcSpec.max_out} 条输出`); return;
    }
    if (tgtSpec && tgtSpec.max_in >= 0 && inCount >= tgtSpec.max_in) {
      setError(`${tgtSpec.label} 最多只能有 ${tgtSpec.max_in} 条输入`); return;
    }
    if (edges.some((e) => e.source === source && e.target === target)) {
      setError("该连线已存在"); return;
    }
    if (wouldCycle(edges, source, target)) { setError("该连线会形成环路"); return; }
    setError("");
    setEdges((eds) => addEdge({ ...conn, markerEnd: { type: MarkerType.ArrowClosed } }, eds));
  }, [nodes, edges, specMap, setEdges]);

  const addNode = (type) => {
    const spec = specMap[type];
    if (!spec) return;
    let i = 1;
    while (nodes.some((n) => n.id === `${type}_${i}`)) i += 1;
    const id = `${type}_${i}`;
    setNodes((nds) => [...nds, {
      id, type: "syn", position: { x: 120 + (nds.length % 4) * 40, y: 420 },
      data: { stepType: type, name: spec.label, config: defaultConfig(spec) },
    }]);
    setSelectedId(id);
  };

  const selected = nodes.find((n) => n.id === selectedId) || null;
  const selectedSpec = selected ? specMap[selected.data.stepType] : null;

  const updateConfig = (key, value) => {
    if (!selected) return;
    setNodes((nds) => nds.map((n) => (
      n.id === selected.id ? { ...n, data: { ...n.data, config: { ...n.data.config, [key]: value } } } : n
    )));
  };

  const removeSelected = () => {
    if (!selectedId) return;
    setNodes((nds) => nds.filter((n) => n.id !== selectedId));
    setEdges((eds) => eds.filter((e) => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
  };

  // 连线删除：单击连线即选中（React Flow 内置 selected 标记），再点「删除连线」或按 Backspace
  const selectedEdges = edges.filter((e) => e.selected);
  const removeSelectedEdges = () => {
    if (!selectedEdges.length) return;
    setEdges((eds) => eds.filter((e) => !e.selected));
    setError("");
  };

  // 切换节点时清空上一次的预览与对齐结果，避免张冠李戴
  useEffect(() => {
    setPreview(null);
    setSchemaInfo(null);
    setSchemaError("");
  }, [selectedId]);

  const loadSchema = async () => {
    if (!selected || !taskKey) {
      setSchemaError("先保存任务后再解析列对齐");
      return;
    }
    setSchemaBusy(true);
    setSchemaError("");
    setSchemaInfo(null);
    try {
      const current = {
        version: 1,
        nodes: nodes.map((n) => ({ id: n.id, type: n.data.stepType, name: n.data.name, config: n.data.config })),
        edges: edges.map((e) => ({ source: e.source, target: e.target })),
      };
      const r = await api.post(`/tasks/${taskKey}/nodes/${selected.id}/schema`,
        { pipeline: current, limit: 200 });
      setSchemaInfo(r);
    } catch (e) {
      setSchemaError(String(e.message || e));
    } finally {
      setSchemaBusy(false);
    }
  };

  // 合并节点结果列重命名：写回 config.aliases（源列 → 结果列），留空或与源列同名表示保持原名
  const setAlias = (from, to) => {
    const current = { ...(selected?.data?.config?.aliases || {}) };
    const value = String(to || "").trim();
    if (!value || value === from) delete current[from];
    else current[from] = value;
    updateConfig("aliases", current);
  };
  const aliasOf = (col) => (selected?.data?.config?.aliases || {})[col] || col;

  const runPreview = async () => {
    if (!selected || !taskKey) {
      setPreview({ error: "先保存任务后再预览该节点" });
      return;
    }
    setPreview({ loading: true });
    try {
      const current = {
        version: 1,
        nodes: nodes.map((n) => ({ id: n.id, type: n.data.stepType, name: n.data.name, config: n.data.config })),
        edges: edges.map((e) => ({ source: e.source, target: e.target })),
      };
      const r = await api.post(`/tasks/${taskKey}/nodes/${selected.id}/preview`,
        { pipeline: current, sample: [], limit: 20 });
      setPreview({ nodeId: selected.id, sample: r.sample || [], metrics: r.metrics || [] });
    } catch (e) {
      setPreview({ error: String(e.message || e) });
    }
  };

  const renderField = (field) => {
    const value = selected.data.config?.[field.key];
    const common = { key: field.key };
    if (field.type === "select") {
      return (
        <Select {...common} value={value ?? field.default ?? ""} onChange={(e) => updateConfig(field.key, e.target.value)}>
          <option value="">请选择</option>
          {(field.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
        </Select>
      );
    }
    // 连接字段：下拉选择连接，只显示连接名（键值仅作为 option value 提交给后端）
    if (field.type === "connection") {
      const current = value ?? "";
      const known = conns.some((c) => c.conn_key === current);
      return (
        <Select {...common} value={current} onChange={(e) => updateConfig(field.key, e.target.value)}>
          <option value="">{conns.length ? "请选择连接" : "（暂无可用连接）"}</option>
          {conns.map((c) => <option key={c.conn_key} value={c.conn_key}>{c.name}</option>)}
          {current && !known && <option value={current}>（连接已失效）</option>}
        </Select>
      );
    }
    if (field.type === "number") {
      return <Input {...common} type="number" value={value ?? field.default ?? ""}
                    onChange={(e) => updateConfig(field.key, Number(e.target.value))} />;
    }
    if (field.type === "sql") {
      return <Textarea {...common} rows={4} value={value ?? ""} placeholder="SELECT ..."
                       onChange={(e) => updateConfig(field.key, e.target.value)} />;
    }
    if (field.type === "field_rules") {
      const sampleColumns = Object.keys((preview?.sample || [])[0] || {});
      return (
        <FieldRulesEditor rules={value || []} columns={sampleColumns} errors={fieldErrors}
                          onChange={(rules) => updateConfig(field.key, rules)} />
      );
    }
    if (field.type === "string_list" || field.type === "columns") {
      return <Input {...common} value={listToText(value)} placeholder="逗号分隔，如 id, name"
                    onChange={(e) => updateConfig(field.key, textToList(e.target.value))} />;
    }
    if (field.type === "sort_keys") {
      return <Input {...common} value={sortToText(value)} placeholder="如 amount:desc, id"
                    onChange={(e) => updateConfig(field.key, textToSort(e.target.value))} />;
    }
    if (field.type === "aggregations") {
      return <Textarea {...common} rows={4} value={aggToText(value)} placeholder="sum(amt)->total"
                       onChange={(e) => updateConfig(field.key, textToAgg(e.target.value))} />;
    }
    if (field.type === "mapping") {
      return <Textarea {...common} rows={4} value={mapToText(value)} placeholder="1=启用"
                       onChange={(e) => updateConfig(field.key, textToMap(e.target.value))} />;
    }
    return <Input {...common} value={value ?? field.default ?? ""}
                  placeholder={field.placeholder || field.help || ""}
                  onChange={(e) => updateConfig(field.key, e.target.value)} />;
  };

  return (
    <div style={{ display: "flex", gap: 12, minHeight: "clamp(520px, calc(84vh - 110px), 880px)" }}>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <Select value="" onChange={(e) => { if (e.target.value) addNode(e.target.value); }}>
            <option value="">+ 添加步骤</option>
            {["source", "merge", "row", "batch", "target"].map((cat) => (
              <optgroup key={cat} label={CATEGORY_CN[cat]}>
                {specs.filter((s) => s.category === cat)
                  .map((s) => <option key={s.type} value={s.type}>{s.label}</option>)}
              </optgroup>
            ))}
          </Select>
          <Button variant="ghost" icon="trash" onClick={removeSelected} disabled={!selectedId}>删除节点</Button>
          <Button variant="ghost" icon="x" onClick={removeSelectedEdges} disabled={!selectedEdges.length}
                  title="先点选一条连线，再删除（也可按 Backspace）">删除连线</Button>
          <span style={{ flex: 1 }} />
          <Input style={{ width: 110 }} type="number" value={batchSize} title="批次读取大小"
                 onChange={(e) => setBatchSize(Number(e.target.value) || 1)} />
          <Input style={{ width: 110 }} type="number" value={commitSize} title="提交批次大小"
                 onChange={(e) => setCommitSize(Number(e.target.value) || 1)} />
        </div>
        {error && <Callout tone="danger">{error}</Callout>}
        <div style={{ height: "clamp(460px, calc(84vh - 170px), 820px)", border: "1px solid var(--border)",
                      borderRadius: "var(--r-md)", background: "var(--bg-elevated)", overflow: "hidden" }}>
          <ReactFlow
            nodes={decorated}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_, node) => setSelectedId(node.id)}
            onPaneClick={() => setSelectedId(null)}
            fitView
          >
            <Background gap={16} />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable />
          </ReactFlow>
        </div>
        <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-tertiary)" }}>
          批次：每批读取 {batchSize} 行 · 提交：每 {commitSize} 行写一次（整批步骤超阈值会溢出到本地临时目录）
          {" · "}
          <span style={{ color: selectedEdges.length ? "var(--accent)" : "inherit" }}>
            删除连线：单击连线选中后按 Backspace，或点上方「删除连线」
          </span>
        </div>
      </div>

      {selected && (
        <Card title={`节点配置 · ${selected.id}`} sub={selectedSpec?.label}
              style={{ width: 380, flexShrink: 0, maxHeight: "calc(84vh - 110px)", overflowY: "auto" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <label style={{ fontSize: "var(--fs-xs)", color: "var(--text-secondary)" }}>
              节点名称
              <Input value={selected.data.name || ""}
                     onChange={(e) => setNodes((nds) => nds.map((n) => (
                       n.id === selected.id ? { ...n, data: { ...n.data, name: e.target.value } } : n)))} />
            </label>
            {(selectedSpec?.config_schema?.fields || []).map((f) => (
              <label key={f.key} style={{ fontSize: "var(--fs-xs)", color: "var(--text-secondary)" }}>
                {f.label}{f.required ? " *" : ""}
                {renderField(f)}
                {f.help && <span style={{ color: "var(--text-tertiary)" }}>{f.help}</span>}
              </label>
            ))}
            {selected.data.stepType === "union" && (
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                <Button variant="ghost" onClick={loadSchema} disabled={schemaBusy}>
                  {schemaBusy ? "解析中…" : "解析列对齐"}
                </Button>
                {schemaError && <Callout tone="danger">{schemaError}</Callout>}
                {schemaInfo && (
                  <div style={{ fontSize: "var(--fs-xs)", marginTop: 6 }}>
                    <div style={{ color: "var(--text-secondary)", marginBottom: 4 }}>
                      入边：{schemaInfo.sources
                        .map((s) => `${s.node_id}（${s.columns.length} 列 / ${s.rows} 行）`)
                        .join(" · ")}
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 0.6fr 1fr 1fr", gap: 4, alignItems: "center" }}>
                      <b>列</b><b>类型</b><b>输出列</b><b>缺失入边</b>
                      {schemaInfo.columns.map((c) => (
                        <Fragment key={c.name}>
                          <span>{c.name}</span>
                          <span>{c.type}</span>
                          <Input value={aliasOf(c.name)} placeholder={c.name}
                                 style={{ minWidth: 0 }}
                                 onChange={(e) => setAlias(c.name, e.target.value)} />
                          <span style={{ color: c.missing.length ? "var(--danger)" : "var(--text-tertiary)" }}>
                            {c.missing.join(", ") || "-"}
                          </span>
                        </Fragment>
                      ))}
                    </div>
                    <div style={{ color: "var(--text-tertiary)", marginTop: 4 }}>
                      输出列可改名；列顺序由上方「结果列」按逗号顺序决定。
                    </div>
                    {(schemaInfo.notes || []).map((n) => (
                      <div key={n} style={{ color: "var(--warning)", marginTop: 2 }}>{n}</div>
                    ))}
                  </div>
                )}
              </div>
            )}
            <Button variant="ghost" icon="play" onClick={runPreview}>预览该节点</Button>
            {preview?.loading && <div style={{ fontSize: "var(--fs-xs)" }}>执行中…</div>}
            {preview?.error && <Callout tone="danger">{preview.error}</Callout>}
            {preview?.sample && (
              <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-secondary)" }}>
                已生成样本 {preview.sample.length} 行（见弹出预览）
              </div>
            )}
          </div>
        </Card>
      )}

      {preview?.sample && (
        <Modal
          title={`节点预览 · ${preview.nodeId}`}
          onClose={() => setPreview(null)}
          width={720}
        >
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-secondary)", marginBottom: 8 }}>
            输出样本 {preview.sample.length} 行（试运行不写目标）
          </div>
          <pre style={{
            maxHeight: 380, overflow: "auto", background: "var(--bg)", padding: 10,
            borderRadius: "var(--r-sm)", fontSize: 11, margin: 0,
          }}>
            {JSON.stringify(preview.sample, null, 1)}
          </pre>
          <div style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--text-tertiary)" }}>
            {preview.metrics.map((m) => `${m.node_id}: ${m.in_rows}→${m.out_rows}`).join(" · ")}
          </div>
        </Modal>
      )}
    </div>
  );
}
