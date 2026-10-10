# Proposal: AI 生成 ETL 任务——多轮对话会话 + 表达式 Skill

## Why

平台的两条 AI 生成路径在「对话形态」与「表达式规范」两处都有实质缺陷，共同导致生成结果不准：

### A. 与大模型的对话不是多轮（现状为伪多轮）

| 现状 | 证据 |
|---|---|
| 供应商接口每次只发 `[system?, user]`，没有 assistant 轮次 | `app/llm/deepseek.py:35-42` |
| 每次调用都把上下文重新拼进**新** prompt（骨架 JSON、源表结构、草稿 JSON 全是文本拼接） | `app/llm/task_draft.py:143-162`、`324-389` |
| 会话历史只在前端内存里、仅用于展示，**从不上送** | `services/frontend/src/pages/ConversationTask.jsx:20,45,62-69` |
| 修订轮只拿到「当前草稿 JSON + 修订指令」，没有前序对话 | `app/llm/task_draft.py:381-388` |

后果：①模型看不到自己的前序输出，每轮都被迫重新理解全部上下文，导致「保持字段映射稳定」只能靠祈祷；②修订轮丢失「用户原始需求」与「骨架识别结论」，改一处容易带崩其他字段；③上下文重复拼接，token 浪费且易与最新草稿不一致（服务端拼接的 JSON 与模型记忆漂移）。

### B. 表达式规范缺失

1. **提示词里没有表达式的概念**：自然语言建任务第二轮 `build_full_prompt` 给的字段示例只有 `{"source","target","kind":"direct"}`，未提及 `expression`/`cast`/`null_fill`/`constant` 四种 kind 的存在与差异，更未给出白名单函数。
2. **RAG 里没有表达式的知识**：`app/llm/rag.py` 只加载 `contracts/*.md`，目录下仅 `etl_sdk.md`（抽取/转换/加载运行契约），**没有任何表达式使用规范**；自然语言建任务路径更是完全不走 RAG。
3. **权威清单只存在于代码里**：16 个白名单函数写死在 `app/expr/whitelist.py`，且被 `app/scripting/gen_config.py` 的 `_EXPR_NS_SRC` 复制了第二份（双份漂移风险）。
4. **后果**：LLM 会把 SQL/Python 习惯直接写进 `expression`，分三类出错：
   - **被保存校验拒绝**（实测确认）：`.strip()` / `.lower()` 属性访问、`datetime.now()`、`coalesce(...)` / `nvl(...)` 等 SQL 习惯函数、字符串 `.format()`——保存时被 `validate_rules` 的 AST 白名单以 422 拒绝；
   - **通过静态校验但运行期报错**（实测确认）：`row['col']` 这类引用方式不含属性/调用，校验通过但求值时 `row` 未定义直接 NameError；引用不存在的列名同理；
   - **通过校验但不合规范**（实测确认）：`f"{a}-{b}"`、`a % b` 格式化（不含属性/调用，能通过校验），与 `concat()` 表达同一语义却让任务配置出现两套写法，影响一致性与后续维护。
   三种都需要人工逐条返工或埋下运行期故障。

两件事必须一起做：**多轮会话是载体**（技能包只需在会话 system 注入一次，修订轮与修复轮天然成为后续轮次），**表达式 skill 是内容**（让每一轮生成的表达式都合规范）。这就是本次变更的整体范围。

## What Changes

### 1. 与大模型的对话改为真多轮

- **接入层增加多轮入口**：`LLMProvider` 增加 `converse(messages, *, system=None)`；既有 `complete(prompt, *, system=None)` 保留为「单轮 message 列表 + 一个 user」的薄封装，脚本生成路径与既有调用不受影响。
- **生成会话内维护模型可见轮次历史**：骨架识别 → 源表结构注入 → 完整草稿 → 用户修订 → 表达式修复，均为同一会话中的连续轮次；后续轮次在既有历史上**追加**，不再重发骨架/草稿 JSON。
- **无状态服务端契约**：服务端在响应中返回本轮之后的模型可见历史（opaque），客户端随下一次请求原样回传；服务端对回传历史做角色白名单与长度校验，不合法则拒绝并给出可读原因；服务端不依赖跨请求内存状态。
- **历史边界**：会话历史有轮次/字符上限，超限时裁剪最早的非系统轮次；历史中不得出现明文凭据（沿用既有脱敏）。
- **审计扩展**：LLM 调用审计记录会话标识与轮次序号，使一次多轮生成的完整轨迹可回溯。

### 2. 新增「表达式 Skill」知识资产

在 `services/backend/app/llm/skills/expression/` 下沉随服务分发的 skill 包（`SKILL.md` 为主体，`examples.md` 承载配方与反例），面向 AI 讲清「各类表达式怎么用」：

- 五类转换规则（`direct` / `constant` / `null_fill` / `cast` / `expression`）的语义、适用判据与 JSON 字段（`source`/`target`/`kind`/`cast_to`/`null_default`/`constant_value`/`expression`），明确「能用更简单的 kind 表达就不要写表达式」；
- `fields[].expression` 的就位形态与字段引用写法（列名即变量名，仅合法标识符列名可引用）；
- 16 个白名单函数的逐个签名、语义与边界（`lower/upper/strip/replace` 对非字符串原样返回、`concat` 统一转字符串、`cast(v, t)` 即 `t(v)`、`bool_int` 等 `cast_to` 取值）；
- 禁止项清单（属性访问、赋值、任何内建、非白名单调用）与**正/反例对照**（SQL/Python 习惯误写 → 等价正确写法）；
- 常见需求→表达式配方（拼接、去空格、大小写归一、四舍五入、数值/布尔转换、兜底默认值、除零保护等）。

### 3. 白名单单一事实源

表达式 skill 的「可用函数 / 禁止项」由 `app/expr/whitelist.py` 的权威清单派生并加一致性测试断言；消除 `app/scripting/gen_config.py` 内联的 `_EXPR_NS_SRC` 第二份副本，使「文档 / 校验器 / 生成脚本运行时」三者同源。

### 4. 注入两条 AI 生成路径

- `app/llm/rag.py` 加载范围扩展为「`contracts/*.md` + `skills/*/SKILL.md`」，自动覆盖「TaskConfig→Python 脚本」路径；
- 「自然语言→TaskConfig 草稿」路径（不走既有 RAG）在**会话 system 段落注入一次**技能包，历史轮次不重复注入。

### 5. 生成后表达式校验回环

- 草稿生成、修订、修复每轮产出后立即对 `fields` 执行 `validate_rules`；存在违规时把「字段 + 违规原因 + 规范要点」作为**同一会话的下一轮**回喂 LLM 修复，修复最多 1 次；仍失败则降级为草稿告警；
- 脚本生成路径前置 `validate_rules` 并把表达式相关失败纳入可读反馈，违规脚本不得生效。

### 6. 违规可见且不落库

草稿卡与任务表单以字段级标红展示表达式校验结果（通过/违规原因），违规草稿不得进入保存链路（沿用既有 422 语义），用户可据告警在会话中继续修订。

## Capabilities

### New Capabilities

无。本变更不引入新的 spec 能力，全部落在既有 `etl-authoring` 能力内（表达式规则、LLM 接入层与对话式建任务同属该能力域）。

### Modified Capabilities

- `etl-authoring`：
  - **新增** Requirement：①多轮对话会话、②会话历史契约与回传校验、③表达式技能包内容契约、④技能包与白名单同源、⑤技能注入两条生成路径、⑥规则类型选择引导、⑦生成后表达式校验回环、⑧违规可见且不落库；
  - **修改** Requirement：「LLM 接入层抽象」（增加多轮对话入口，补全作为其特例）、「LLM RAG 上下文注入」（注入范围扩展为 etl_sdk 契约 + 表达式技能包，且在会话中以系统段落注入一次）、「对话修订草稿」（由单轮草稿修订改为在会话历史基础上追加修订轮）、「LLM 调用审计」（增加会话标识与轮次序号）。

## Impact

- **后端（新增）**：
  - `services/backend/app/llm/skills/expression/{SKILL.md,examples.md}`；
  - 会话组装的内部模块（模型可见历史的构造、裁剪与校验）。
- **后端（修改）**：
  - `app/llm/provider.py`：`LLMProvider` 增加 `converse(messages, *, system=None)`，`complete` 降为其薄封装；
  - `app/llm/deepseek.py`：把 `messages` 列表直接下发，不再固定两条；
  - `app/llm/task_draft.py`：`generate_draft` / `generate_revise` 改为在会话历史上追加轮次；生成后 `validate_rules` 自检 + 违规回喂修复限次；
  - `app/llm/rag.py`：加载范围扩展（契约 + skill），保持供应商无关语义；
  - `app/llm/generator.py`：脚本生成路径携带表达式 skill，并前置表达式校验；
  - `app/expr/whitelist.py`：导出权威清单与禁止项（求值/校验行为不变）；
  - `app/scripting/gen_config.py`：`_EXPR_NS_SRC` 改为与权威清单同源；
  - `app/transform.py`：`validate_rules` 作为回环校验入口复用（行为不变）。
- **API（兼容增量）**：`POST /api/llm/tasks/draft` 与 `/draft/revise` 的请求增加可选 `history`（模型可见历史）字段，响应增加 `history`（本轮之后的历史）与 `expression_issues`；既有 `draft`/`warnings`/`sample`/`schema_source`/`inferred_fields` 字段与含义不变，不传 `history` 时行为退化为既有的单轮编排。
- **前端**：`ConversationTask.jsx` 保存并回传 opaque `history`（展示仍用本地消息流）、草稿卡字段级表达式告警；`Tasks.jsx` 表单表达式规则告警展示。**不新增**对外表达式参考接口或前端参考面板（本次范围外）。
- **数据与依赖**：不改变 `TaskConfig` 契约，不新增第三方依赖，无数据迁移；会话历史由客户端持有，不在服务端落库。无 **BREAKING** 变更（既有单轮调用路径保持可用）。
- **测试**：新增多轮会话历史组装/裁剪/校验测试、skill 与白名单一致性断言、正例可通过/反例必被拒的 docs-as-tests、校验回环修复测试；既有 `FakeProvider` / `RecordingProvider` 需适配多轮签名。
