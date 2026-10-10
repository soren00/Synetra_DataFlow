# Tasks: 多轮对话会话 + 表达式 Skill

依赖说明：P1 端到端闭环，按 design 的迁移顺序推进 —— ①多轮会话基础（接入层 + 历史契约 + 编排 + API + 前端回传）→ ②表达式技能包与单一事实源 → ③注入两条生成路径 → ④生成后校验回环 → ⑤违规可见与不落库 → ⑥验收回归。第 3 组依赖 1、2 组，第 4 组依赖 3 组，第 5 组依赖 4 组。所有验证均在本地启动后端（:8000）后可复现；单轮兼容路径（请求不带 `history`）在每个阶段都必须保持可用。

## 1. 多轮对话会话基础 [P1]

- [x] 1.1 接入层多轮化：`app/llm/provider.py` 的抽象方法改为 `converse(messages, *, system=None) -> str`；`complete(prompt, *, system=None)` 保留为非抽象薄封装（内部构造单条 `user` 消息后调用 `converse`）；`app/llm/deepseek.py` 把 `messages` 列表直接映射进 payload，不再固定 `[system?, user]` 两条。
  - 验证：测试替身只需实现 `converse` 即可被 `ScriptGenerator` 与草稿路径使用；既有的单轮调用点（`generator.py`）与既有断言仍通过；`DeepSeekProvider` 构造出的 payload 与传入的 messages 列表逐条一致。

- [x] 1.2 会话历史契约与校验：定义模型可见历史的角色白名单（`system` / `user` / `assistant`）与结构契约，实现结构校验与长度双限裁剪（默认 20 轮 / 60K 字符，从最早的非 `system` 轮次开始裁剪，`system` 永不被裁剪），并提供 `history_trimmed` 标记。
  - 验证：回传含非法角色的历史 → 请求被拒绝且 provider 调用次数为 0；构造超长历史 → 裁剪后 `system` 段落与表达式技能包仍在，响应标记 `history_trimmed=true`。

- [x] 1.3 编排改造为会话追加：`app/llm/task_draft.py` 的 `generate_draft` / `generate_revise` 改为在会话历史上追加轮次——骨架轮、源表结构注入轮（`user` 角色，带「已识别骨架」「源表结构（来源：…）」标签）、完整草稿轮依次入历史，assistant 的 JSON 输出原样入历史；修订轮不再重复拼接草稿 JSON。
  - 验证：用假供应商断言第二次调用的 messages 中包含「骨架」「源表结构」与上一轮 assistant 输出的 JSON 原文；`/draft` 后接 `/draft/revise`，修订轮的 messages 中包含用户原始需求描述。

- [x] 1.4 API 契约扩展：`app/api/llm.py` 的 `POST /llm/tasks/draft` 与 `/llm/tasks/draft/revise` 请求增加可选 `history`，响应增加 `history` 与 `history_trimmed`；不传 `history` 时退化为既有单轮编排。
  - 验证：不传 `history` 的请求，响应中 `draft`/`warnings`/`sample`/`schema_source`/`inferred_fields` 的结构与含义与改造前一致；传 `history` 时返回的 `history` 长度等于上次长度加本轮轮次数。

- [x] 1.5 审计扩展：`llm_draft_call` 事件记录会话标识与轮次序号。
  - 验证：一次两轮生成的审计事件可按会话标识与轮次序号还原完整调用轨迹（含失败与重试）。

- [x] 1.6 前端回传会话历史：`services/frontend/src/pages/ConversationTask.jsx` 保存响应返回的 `history`，并在下一次请求中原样回传；聊天区展示仍使用本地消息流，不因历史回传而重复渲染。
  - 验证：首次生成后连续两次修订，第二次修订请求体中的 `history` 包含前序轮次；页面消息流既不重复也不错乱。

## 2. 表达式技能包与白名单单一事实源 [P1]

- [x] 2.1 在 `services/backend/app/expr/whitelist.py` 增加权威访问器 `allowed_functions()` 与 `forbidden_notes()`，并导出供生成脚本复用的命名空间源码常量 `EXPR_NS_SOURCE`；**校验与求值行为不变**。
  - 验证：`python -c "from app.expr.whitelist import allowed_functions; print(sorted(allowed_functions()))"` 输出 16 个函数；既有表达式相关测试全部仍通过。

- [x] 2.2 把 `services/backend/app/scripting/gen_config.py` 的内联 `_EXPR_NS_SRC`（第 19-29 行）改为引用 2.1 的 `EXPR_NS_SOURCE` 渲染，消除第二份函数清单副本。
  - 验证：生成一份含 expression 规则的任务脚本，脚本内 `_EXPR_NS` 的函数集合与 `allowed_functions()` 完全一致；脚本可正常 dry-run。

- [x] 2.3 新增 `services/backend/app/llm/skills/expression/SKILL.md`（带 `name` / `description` / `applies_to` frontmatter），覆盖：五类规则的语义与选择判据、`fields[].expression` 形态与字段引用写法（列名即变量名，仅合法标识符可引用）、受支持函数逐个签名/语义/边界（非字符串原样返回、`concat` 转字符串、`cast(v,t)` 语义、`cast_to` 取值）、禁止项清单、何时确实该用表达式。
  - 验证：通读后可在不查源码的前提下写出 `lower(email)`、`concat(id,'-',name)`、`round(amount,2)`、`cast(amount,int)` 等正确写法，并说明 `.strip()` 属性访问为何被拒；文档体积 ≤ 12KB。

- [x] 2.4 新增 `services/backend/app/llm/skills/expression/examples.md`：反例对照（属性访问、内建调用、SQL 习惯函数如 `coalesce`/`nvl`）+ 「静态通过但运行期报错」类（`row['col']`、引用不存在列、`sum(a,b)`、`int('abc')`）+ 「通过但不合规范」类（`%`/f-string/`+` 拼接 → `concat`）+ 常见需求配方（拼接、去空格、大小写归一、四舍五入、数值/布尔转换、兜底默认值、除零保护）。
  - 实现期修正：`%`/f-string/`+` 拼接实测**能通过**白名单校验，故按「不合规范、应改用 `concat`」而非「被拒绝」归类。
  - 验证：每条反例都是 `kind=expression` 场景下 LLM 真会写出的形态，且都附一条通过白名单的等价写法。

- [x] 2.5 新增 `services/backend/tests/test_expression_skill.py`：断言技能包声明的函数集合 == `allowed_functions()`；逐一求值正例（全部通过）、逐一校验反例（全部被拒且给出违规原因）。
  - 验证：把 `allowed_functions()` 临时多/少一个函数，该测试失败；恢复后通过（文档与校验器不一致可被构建检出）。

## 3. 技能包注入两条生成路径 [P1, 依赖 1、2]

- [x] 3.1 扩展 `services/backend/app/llm/rag.py`：加载范围由 `contracts/*.md` 变为「`contracts/*.md` + `skills/*/SKILL.md`」，拼接时带技能名小标题；`build_rag_context()` 签名与「供应商无关」语义不变。
  - 验证：`build_rag_context()` 返回文本同时包含 etl_sdk 契约与表达式技能包内容；切换 LLM 供应商后注入内容不变。

- [x] 3.2 草稿路径会话级注入：`task_draft.py` 把表达式技能包合入**会话级 `system` 段落**（与「只输出合法 JSON、不得虚构连接」等既有系统提示合成一段），骨架轮与后续轮都携带该 `system`；同时把 `fields` 示例组由单一 `kind:"direct"` 扩展为覆盖 `cast` / `null_fill` / `expression`，并给出五类规则选择判据。
  - 验证：打印任一草稿轮次的请求，`system` 中含技能包与五类判据，`fields` 示例含 `expression` 完整形态；同一会话第二轮及之后的 user 内容中**不含**技能包正文（不重复注入）。

- [x] 3.3 脚本生成路径：`app/llm/generator.py` 的 `ScriptGenerator.generate` 前置对任务配置执行 `validate_rules`，违规时返回可读原因并拒绝生成；技能包明确要求「脚本从 `fields[].expression` 读取表达式，不得自行硬编码表达式逻辑」。
  - 验证：用含白名单外函数表达式的 TaskConfig 调用生成接口，返回表达式违规原因且不产生脚本；合规配置生成的脚本可正常 dry-run。

- [x] 3.4 在 LLM 审计事件中记录本次注入的技能名与指纹（如 `skills: ["expression@<sha8>"]`）。
  - 验证：发起一次草稿生成后，事件日志中该次调用可见技能名与指纹，可回溯「该次生成用了哪版规范」。

## 4. 生成后表达式校验回环 [P1, 依赖 3]

- [x] 4.1 草稿路径自检与修复：完整草稿轮与修订轮产出后立即用 `app/transform.py:validate_rules` 校验 `fields`；存在违规时把「违规字段 + 违规原因 + 规范要点摘要」作为**同一会话的下一轮**追加，调用同 provider 生成修复稿，**修复最多 1 次**（与输出非法重试独立计数），审计 `round="repair"`；仍违规则保留草稿并写入字段级告警。
  - 验证：用假供应商先返回含 `lower(email).strip()` 的草稿、再返回合规草稿，最终草稿该字段表达式合规且审计出现 `round=repair`；设为两次都违规时，最终返回草稿带告警，且 provider 调用次数不超过预期（不无界重试）。

- [x] 4.2 结构化告警输出：草稿响应在既有 `warnings` 外新增 `expression_issues: [{"field","target","message"}]`（纯新增字段，向后兼容），由 `POST /llm/tasks/draft` 与 `/draft/revise` 透出。
  - 验证：合规草稿的 `expression_issues` 为空数组；修复失败时逐条列出字段与原因；既有字段结构不受影响。

- [x] 4.3 脚本生成路径把表达式相关校验失败纳入生成结果反馈（`GenerateResult.violations` 与响应可读原因），违规脚本不得直接生效。
  - 验证：构造含违规表达式的配置，生成结果 `violations` 含表达式违规原因，`/llm-confirm` 流程不写入脚本版本。

- [x] 4.4 补测试 `services/backend/tests/test_llm_expression_loop.py`：覆盖「违规 → 自动修复成功」「达上限 → 告警且不再调用」「合规 → 不额外调用」三条路径。
  - 验证：`pytest tests/test_llm_expression_loop.py` 三条用例全绿，且通过断言 provider 调用次数确认「不额外调用」与「轮次受限」。

## 5. 表达式违规可见且不落库 [P1, 依赖 4]

- [x] 5.1 前端 `ConversationTask.jsx` 草稿卡按字段展示 `expression_issues`：违规字段标红并展示原因，通过的字段正常显示。
  - 验证：收到含 `expression_issues` 的响应时对应字段行标红并显示原因文本；为空时无任何标红。

- [x] 5.2 前端 `Tasks.jsx` 在表达式输入处展示保存时后端 422 返回的违规原因（含字段名与原因），与合法输入可区分。
  - 验证：在表单填入 `lower(email).strip()` 保存，页面在该字段处展示后端违规原因，未被通用错误提示吞掉。

- [x] 5.3 端到端确认「违规不落库」与「会话内修订」：含违规表达式的配置经保存接口被拒绝、任务列表不新增该任务；用户可针对告警在同一会话发起修订并得到合规表达式。
  - 验证：`POST /api/tasks` 提交含白名单外函数的配置返回 422 且任务未入库；在对话页按告警提出修订指令，修订稿通过 `validate_rules` 并可成功保存。

## 6. 验收与回归 [P1]

- [x] 6.1 真实 LLM 对照评测（表达式准确性）：准备 ≥10 条需要转换的自然语言需求（拼接、去空格、大小写、四舍五入、空值兜底、类型转换），分别在「不注入技能包」与「注入技能包」下各生成一轮，统计首次生成即通过 `validate_rules` 的比例。
  - 验证：给出两组结果对照表与实际复现步骤（描述 → 草稿 → 校验结果）；注入后首次通过率提升，且「本可用 `cast`/`null_fill` 却写成表达式」的情况明显减少。
  - **结果（2026-09-28，DeepSeek `deepseek-flash`，15 条用例 × 2 臂，真实调用；含 §7 的掩码用例）**：

    | 指标 | 注入技能包 | 不注入（对照） |
    |---|---|---|
    | 首次通过率（未触发修复轮） | **15/15 = 100%** | 7/15 = 46.7% |
    | 触发修复轮次数 | **0** | 8 |
    | 最终合规率（`expression_issues` 为空） | 100% | 100%（靠回环兜底） |
    | 规范符合率（kind 选择 + 写法） | **15/15** | 11/15 = 73.3% |

  - 对照臂首次写错的表达式（实测捕获）：`trim(customer_name)`、`trim(phone)`（SQL 习惯函数）；
    `cast(coalesce(nullif(active, ''), '0') as int)`、`COALESCE(CAST(active AS SIGNED), 0)`（SQL 语法混入）；
    `case when status = 1 then 'paid' else 'unpaid' end`（SQL CASE）；
    `concat(substr(customer_name,1,1),'*',substr(customer_name,3))`（SQL 取子串）。
  - **最严重的一类失败：回环修复后「静默丢功能」**。对照臂在掩码需求上修复轮仍无法合规，最终把字段降级为
    `direct`（T13 `customer_name`/`phone`、T15 `phone`）与 `constant`（T14 `customer_name`）——保存能通过，
    但**脱敏完全没做**（数据合规风险）；规范符合率因此只有 73.3%。注入臂对应字段稳定为 `expression` 且表达式正确。
  - 复现步骤：配置好 LLM → 在 `services/backend` 下执行
    `.\venv\Scripts\python.exe openspec\changes\expression-skill-for-llm-generation\eval\eval_expression_skill.py --only 6.1 --arms skill,baseline`；
    明细见同目录 `eval_result_2026-09-28.json`。

- [x] 6.2 真实 LLM 对照评测（多轮价值）：对同一草稿连续发起 ≥3 轮修订，统计未被指令涉及的字段映射保持不变的比例，并对比"不带 `history` 的单轮退化模式"下的同一指标。
  - 验证：给出两组对照数据与复现步骤；多轮模式下未涉及字段的稳定率高于单轮模式，修订轮无需重发草稿 JSON。
  - **结果（2026-09-28，同一模型，一次种子生成 + 3 轮修订 × 2 臂）**：
    - 指令集 A（「改成全量」「水位改 update_time」「再加一个拼接字段」）：两臂稳定率均为 **1.0（3/3 轮）** —— **平价，未证实假设**；原因是这些指令在当前草稿 JSON 下已能唯一确定动作，单轮退化不构成信息缺失。
    - 指令集 B（在 A 基础上把第 3 轮换成**必须回看用户原始需求**的指令：「请把我没点名的字段都去掉，只留我最初要求的那几个」）：
      - 多轮：3/3 轮未涉及字段保持不变（**5/5 字段保留**，无误删）；
      - 单轮退化：第 3 轮**误删用户最初点名要求的 `customer_name`、`status` 两列**（3/5 保留，稳定率 0.867）。
  - 结论：当修订指令**依赖前序轮次信息**时多轮优势明确；指令自洽时二者等价（这本身是合理的负面结论）。
    因此本任务的验收口径采纳指令集 B（原任务描述只给了 A 类指令，不足以区分）。
  - 追加的结构化证据（不依赖模型，由测试守证）：多轮修订**不再重发草稿 JSON**（`tests/test_llm_config_draft.py::test_revise_uses_session_history_without_resending_draft`），且原始需求始终在会话历史中（`test_draft_multiturn_history_appended_and_returned`）。
  - 复现步骤：同上脚本改为 `--only 6.2`。

- [x] 6.3 回归既有质量门与兼容性：`pytest`、`ruff`、前端 `vite build` 全绿；确认 `TaskConfig` 契约、`/api/llm/tasks/draft` 既有字段、单轮退化路径、手写 Python 路径与保存链路 422 语义均无破坏。
  - 验证：三条命令退出码为 0；手写 Python 脚本仍不受表达式白名单限制；不带 `history` 的请求行为与改造前一致。

## 7. 实测反馈补强（技能包 v2，用户报错驱动）[P1]

反馈来源：用户实际使用对话式建任务，输入「每天从 orders 订单抽取到 orders_dw，主键 id，将姓名的第二个字转换为*，手机号中间四位转换为*」，模型生成
`CONCAT(LEFT(customer_name, 1), '*', SUBSTRING(customer_name, 3))` 与 `CONCAT(LEFT(phone, 3), '****', RIGHT(phone, 4))`，
创建任务时被 422 拒绝（`函数不在白名单: CONCAT / LEFT / SUBSTRING`）。

根因（两条，均已修复）：

1. **技能包缺「取子串」的写法**——掩码需求必须用**切片**（`col[0]`、`col[a:b]`、`col[-n:]`），技能包此前只列了 16 个函数，模型在无现成函数时回落到 SQL 方言；
2. **修复轮提示只给「有什么函数」不给「怎么写」**——反而把模型推向函数式 SQL 写法，回环无法自愈（对照臂最终把字段降级为 `direct`/`constant` 静默丢功能）。

- [x] 7.1 技能包补齐「可用的操作符与切片」章节：切片取值/区间/取尾、`+` 拼接、比较、三元表达式，并明确「取子串一律用切片，不存在 left/right/substring」。
  - 验证：`concat(phone[0:3], '****', phone[7:])` 求值为 `'138****8001'`、`concat(customer_name[0], '*', customer_name[2:])` 对「张小明」为 `'张*明'`（`tests/test_expression_skill.py::test_masking_recipe_is_executable`）。
- [x] 7.2 技能包新增「SQL / 数据库习惯 → 合规等价写法」对照表（`CONCAT`→`concat`、`LEFT`→`s[:n]`、`RIGHT`→`s[-n:]`、`SUBSTRING`→`s[a-1:a-1+len]`、`COALESCE`→`null_fill`、`CASE WHEN`→三元、`CAST AS`→`cast`、`TRIM`→`strip`、`LENGTH`→`len`），并强调**函数名大小写敏感**（`CONCAT` 会被拒）。
  - 验证：`tests/test_expression_skill.py::test_skill_teaches_slicing_and_sql_dialect_mapping` 断言切片与 6 个 SQL 方言关键词均在技能包中。
- [x] 7.3 技能包新增脱敏/掩码配方（姓名、手机号、身份证、取前 N/后 N 位），并如实标注**无法表达**的需求（如按下标定位 `@` 做邮箱打码，因为没有 `index`/`find`/`split`）。
  - 验证：`examples.md` 的 POSITIVE 块新增 11 条切片/掩码用例，由 `test_examples_positive_all_evaluate` 逐条执行；REJECTED 块新增用户报错的两条表达式 + 8 条 SQL 方言写法，由 `test_examples_rejected_all_blocked` 逐条校验。
- [x] 7.4 修复轮提示改从技能包派生「写法要点」：新增 `<!-- WRITING_TIPS:BEGIN/END -->` 标记块与 `app/llm/rag.skill_block()`，修复提示改为「违规清单 + 写法要点（切片/拼接/小写）+ 禁止项 + 可用函数」，不再只给函数清单。
  - 验证：`tests/test_llm_expression_loop.py::test_illegal_expression_is_repaired_in_same_conversation` 断言修复提示含「写法要点」「切片」「小写」；且提示内容与技能包同源（无第三份口径）。
- [x] 7.5 把用户原话加入评测用例集（T13/T14/T15）并重跑对照评测：注入臂 15/15 一次通过且表达式为切片写法；对照臂 8 次修复轮后仍把掩码字段降级为 `direct`/`constant`（静默丢功能）。
  - 验证：`eval/eval_result_2026-09-28.json` 中 T13–T15 两臂对比；注入臂 `first_exprs` 即 `concat(customer_name[0], '*', customer_name[2:])`、`concat(phone[0:3], '****', phone[7:])`。

- [x] 7.6 修正任务表单表达式说明并将其纳入权威口径：`Tasks.jsx` 的 `EXPR_HELP` 抽出 `EXPR_FUNCTIONS` 列表，改掉**事实错误**（原写「源字段按**目标列名**直接引用」，实际只能引用**源列名**），补上「切片」「函数名必须小写」「无 SQL 写法（CONCAT/LEFT/RIGHT/SUBSTRING/COALESCE/TRIM/LENGTH/CASE WHEN/CAST AS）」与脱敏示例。
  - 验证：`tests/test_frontend_expr_help.py` 三项断言——函数集合 == `allowed_functions()`、示例表达式全部通过 `validate_expression` 且切片示例求值为 `'138****8001'`、文案含「源列名/切片/小写 + 9 个 SQL 方言关键词」且**不得出现「目标列名」**。
  - 守门有效性验证（临时探针，不修改仓库文件）：注入 4 类漂移——改回「目标列名」、删掉切片说明、函数清单去掉 `cast`、示例改成 `CONCAT(...)`——**全部被检出**；原始文件 3 项全过。
  - 说明：前端无 JS 测试运行器，故在 pytest 侧做跨端一致性校验；本项不新增对外表达式参考接口（仍遵守原 Non-Goal）。

- [x] 7.7 增加「技能包版本可见性」以排查「服务在跑旧规范」：`app/main.py` 启动时写 `llm_skill_loaded` 审计事件，记录本进程看到的技能包指纹与生效边界说明（技能包**内容**每次请求实时读取，**注入/提示词/回环代码**需重启进程）。
  - 验证：`tests/test_expression_skill.py::test_startup_event_records_skill_fingerprints` 断言事件含 `expression@<sha8>` 指纹。
  - 真实排查案例（2026-09-28）：用户二次报同一 422 错误，定位为后端进程 PID 31508 启动于 14:20:35、而代码/技能包改于 15:30 —— 该进程内**根本没有技能包注入**（其审计记录缺 `skills`/`conversation_id`/`turn`），故模型仍写 SQL。当前代码对同一句提示词实测输出 `concat(customer_name[0], '*', customer_name[2:])`、`concat(phone[0:3], '****', phone[7:])`，一次通过。**结论：无需改技能包，重启后端即可。**
  - 注意（本地开发无 `--reload`）：`uvicorn app.main:app --host 127.0.0.1 --port 8000` 启动的进程不会自动加载代码变更，改完本变更的代码后必须重启。
  - **重启后 HTTP 端到端复验（已完成）**：旧进程 PID 31508（14:20:35 启动）→ 新进程 PID 7100（15:55:45 启动，同命令同工作目录）。经真实 HTTP 接口走完整链路：`POST /api/llm/tasks/draft` 返回 200、`expression_issues` 为空、`customer_name` = `concat(customer_name[0], '*', customer_name[2:])`、`phone` = `concat(phone[0:3], '****', phone[7:])`；随后 `POST /api/tasks`（页面上的「确认创建任务」）**返回 200 创建成功**（验证后已删除该测试任务）。审计对照：重启前你的调用 `skills=None`（旧代码），重启后 `skills=['expression@05e4f29b']`。

### 评测环境与口径（2026-09-28）

- 模型：存储中已配置的 DeepSeek（`provider=deepseek`、`model=deepseek-flash`、`base_url=https://api.deepseek.com`），经 `apply_effective` 生效；
- 数据源：`MySQL-Test` 连接（`direction=both`），实际表 `orders` / `product` / `orders4` / `orders_z`，源表结构经 `information_schema` 实时解析；
- 评测脚本与明细随本变更归档在 `eval/`：`eval_expression_skill.py`（可重跑）、`eval_result_2026-09-28.json`（逐用例明细，含首次表达式与最终表达式）；
- 不依赖真实 LLM 的等价验证：`tests/test_expression_skill.py`（技能包与白名单同源、正反例结论成立）、`tests/test_llm_expression_loop.py`（回环三条路径）、`tests/test_llm_config_draft.py`（多轮历史追加与回传）。
- 注：模型输出存在采样波动（对照臂两次运行的首次通过率为 58.3% / 66.7%），注入臂两次均为 100%。
