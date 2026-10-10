import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Avatar, Card, Chiptone, Icon, Row, Table } from "../components/ui";

export default function Lineage() {
  const [edges, setEdges] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.get("/tasks").then((ts) => {
      setTasks(ts || []);
      return Promise.all((ts || []).map((t) => api.get("/metadata/lineage", { task: t.task_key || t.key }).catch(() => ({ summary: { edges: [] } }))));
    }).then((summaries) => {
      const all = summaries.flatMap((s) => s.summary?.edges ?? []);
      setEdges(all);
    }).catch((e) => { setErr(e.message); setEdges([]); });
  }, []);

  const fieldEdges = edges.filter((e) => e.kind !== "table");
  const tableEdges = edges.filter((e) => e.kind === "table");

  return (
    <div className="mac-fade" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <div><h1 className="mac-h1">元数据与血缘</h1><div className="mac-muted">向导/LLM 路径字段级血缘 · 手写路径表级血缘</div></div>

      {err && <div className="mac-callout mac-callout--warning">血缘数据不可达（{err}）。</div>}

      <Card solid title="血缘图">
        <div className="mac-row mac-end" style={{ gap: 8 }}>
          <Chiptone>字段级可用</Chiptone>
          <Chiptone tone="info">来源</Chiptone>
          <Chiptone tone="success">ETL 变换</Chiptone>
          <Chiptone tone="warning">目标</Chiptone>
        </div>
        <div style={{ marginTop: 16 }}>
          {edges.length === 0
            ? <div className="mac-muted">暂无血缘（创建并保存任务后自动推导）。</div>
            : <Table
                head={["来源", "目标", "血缘"]}>
                {[...tableEdges.map((e) => [e.source, e.target, "table"]),
                  ...fieldEdges.map((e) => [e.source, e.target, "field"])].map(([s, t, k], i) => (
                  <tr key={i}>
                    <td className="num">{s}</td>
                    <td className="num">{t}</td>
                    <td>{k === "field" ? <Chiptone>字段级</Chiptone> : <Chiptone tone="warning">表级</Chiptone>}</td>
                  </tr>
                ))}
              </Table>}
        </div>
      </Card>

      <Card title="字段映射明细" sub={tasks.length ? `${tasks.length} 个任务` : "暂无任务"}>
        <table className="mac-table">
          <thead><tr><th>来源字段</th><th>目标字段</th><th>血缘</th></tr></thead>
          <tbody>
            {fieldEdges.length === 0
              ? <tr><td colSpan={3} style={{ textAlign: "center", color: "var(--text-tertiary)" }}>暂无字段级血缘</td></tr>
              : fieldEdges.map((e, i) => (
                  <tr key={i}>
                    <td className="num">{e.source}</td>
                    <td className="num">{e.target}</td>
                    <td><Chiptone>字段级</Chiptone></td>
                  </tr>
                ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}