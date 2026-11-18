/* 字段规则编辑器（五种规则：直连/常量/空值填充/类型转换/受限表达式）。
   画布节点与既有「快速映射」视图共用同一份规则语义；表达式规则可用 AI 生成建议，
   提示词与校验口径与后端 /api/llm/expression 一致。 */
import { useState } from "react";
import { Button, IconButton, Input, Select } from "./ui";
import { api } from "../api/client";

export const RULE_KINDS = [
  ["direct", "直连"],
  ["constant", "常量"],
  ["null_fill", "空值填充"],
  ["cast", "类型转换"],
  ["expression", "表达式"],
];

export const EXPR_FUNCTIONS = [
  "str", "int", "float", "bool", "len", "abs", "round", "min", "max", "sum",
  "lower", "upper", "strip", "replace", "concat", "cast",
];

const CELL = { display: "grid", gridTemplateColumns: "110px 1fr 1fr 1fr 34px", gap: 6, alignItems: "center" };

function emptyRule() {
  return { kind: "direct", source: "", target: "" };
}

export default function FieldRulesEditor({ rules = [], onChange, errors = {}, columns = [] }) {
  // AI 生成：{ [ruleIndex]: { prompt, busy, msg } }，只影响当前规则行
  const [ai, setAi] = useState({});

  const setAiState = (idx, patch) => setAi((s) => ({ ...s, [idx]: { ...(s[idx] || {}), ...patch } }));

  const update = (idx, patch) => {
    const next = rules.map((r, i) => (i === idx ? { ...r, ...patch } : r));
    onChange(next);
  };
  const remove = (idx) => onChange(rules.filter((_, i) => i !== idx));
  const add = () => onChange([...rules, emptyRule()]);

  const runAi = async (idx) => {
    const rule = rules[idx] || {};
    const state = ai[idx] || {};
    const prompt = (state.prompt || "").trim();
    if (!prompt || state.busy) return;
    setAiState(idx, { busy: true, msg: null });
    try {
      const r = await api.post("/llm/expression", {
        prompt,
        source_field: rule.source || "",
        target_field: rule.target || rule.source || "",
        columns: columns.filter(Boolean),
      });
      if (r.ok) {
        update(idx, { expression: r.expression });
        setAiState(idx, { busy: false, prompt: "", msg: { tone: "success", text: `已生成：${r.expression}` } });
      } else {
        setAiState(idx, {
          busy: false,
          msg: { tone: "danger", text: `未通过校验：${(r.issues || []).join("；")}（已保留你填写的内容）` },
        });
      }
    } catch (e) {
      setAiState(idx, { busy: false, msg: { tone: "danger", text: `生成失败：${e.message}` } });
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {rules.length > 0 && (
        <div style={{ ...CELL, fontSize: "var(--fs-xs)", color: "var(--text-tertiary)" }}>
          <span>方式</span><span>源字段</span><span>目标字段</span><span>参数</span><span />
        </div>
      )}
      {rules.map((r, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={CELL}>
            <Select value={r.kind} onChange={(e) => update(i, { kind: e.target.value })}>
              {RULE_KINDS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
            </Select>
            <Input
              value={r.source || ""}
              placeholder={r.kind === "constant" ? "（可空）" : "源列名"}
              onChange={(e) => update(i, { source: e.target.value })}
            />
            <Input
              value={r.target || ""}
              placeholder="目标列名"
              onChange={(e) => update(i, { target: e.target.value })}
            />
            {r.kind === "constant" && (
              <Input value={r.constant_value || ""} placeholder="常量值"
                     onChange={(e) => update(i, { constant_value: e.target.value })} />
            )}
            {r.kind === "null_fill" && (
              <Input value={r.null_default || ""} placeholder="空值填充为"
                     onChange={(e) => update(i, { null_default: e.target.value })} />
            )}
            {r.kind === "cast" && (
              <Select value={r.cast_to || "str"} onChange={(e) => update(i, { cast_to: e.target.value })}>
                {["int", "float", "str", "bool", "bool_int"].map((t) => <option key={t} value={t}>{t}</option>)}
              </Select>
            )}
            {r.kind === "expression" && (
              <Input value={r.expression || ""} placeholder="如 upper(name)"
                     onChange={(e) => update(i, { expression: e.target.value })} />
            )}
            {r.kind === "direct" && <span />}
            <IconButton icon="trash" title="删除该规则" onClick={() => remove(i)} />
          </div>
          {r.kind === "expression" && (
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <Input
                value={(ai[i] || {}).prompt || ""}
                placeholder="用一句话描述目标值，如「邮箱统一小写并去空格」"
                onChange={(e) => setAiState(i, { prompt: e.target.value })}
                style={{ flex: 1, minWidth: 0 }}
              />
              <Button variant="ghost" icon="ai" onClick={() => runAi(i)} disabled={(ai[i] || {}).busy}>
                {(ai[i] || {}).busy ? "生成中…" : "AI 生成"}
              </Button>
            </div>
          )}
          {r.kind === "expression" && (ai[i] || {}).msg && (
            <span style={{
              fontSize: 11, wordBreak: "break-word",
              color: (ai[i].msg.tone === "success" ? "var(--success)" : "var(--danger)"),
            }}>
              {ai[i].msg.text}
            </span>
          )}
          {errors[r.target || r.source] && (
            <div style={{ color: "var(--danger)", fontSize: "var(--fs-xs)" }}>
              表达式校验未通过：{errors[r.target || r.source]}
            </div>
          )}
        </div>
      ))}
      <Button variant="ghost" icon="plus" onClick={add}>添加字段规则</Button>
      <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-tertiary)", lineHeight: 1.6 }}>
        表达式可用函数：{EXPR_FUNCTIONS.join("、")}；直接写源列名（如 name），禁止属性访问与 SQL 写法。
      </div>
    </div>
  );
}
