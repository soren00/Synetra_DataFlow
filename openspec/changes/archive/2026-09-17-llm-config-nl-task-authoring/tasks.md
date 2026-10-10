# Tasks: LLM 配置管理与自然语言对话式建任务

依赖说明：P1 按「LLM 配置闭环 → 草稿生成后端 → 对话页前端」推进；T1/T2 完成后 T3 才能联调生成，T4-T7 依赖 T2 的草稿管线。验证均含本地启动后端（:8000）的可观察行为，并回归既有测试（pytest + ruff + vite build）。

## P1（端到端闭环）

### Task 1：启动注册 LLM provider + 注册表热切换 [P1]
- [x] 在 `app.main` 启动生命周期调用 `auto_register(settings)`，使环境变量配置（`Settings.llm_provider/deepseek_*`）生效。
- [x] 新增注册表热更新能力：保存配置后按新参数重新 `register_provider`（同名覆盖）。
- [x] 验证：未配 API Key 时 `POST /api/scripts/generate` 返回 503 且文案含「配置」引导；配置后同接口不再 503。

### Task 2：LLM 配置后端（Vault 存取 + 掩码 + 测试连通 + 吊销）[P1, 依赖 1]
- [x] 新增 `GET /api/llm/config`（key 掩码尾 4 位）、`PUT /api/llm/config`（保存即热切换，api_key 可空表示不更新）、`POST /api/llm/config/test`（调 `list_models` 验连通返回模型列表）、`DELETE /api/llm/config/key`（清除密文并重注册为降级态）。
- [x] api_key 经 `SecretVault` 加密落库；仅 admin 角色可访问。
- [x] 验证：PUT 含 api_key → GET 返回掩码不含明文；test 对无效 key 返回失败与原因；DELETE key 后再 `generate` 返回 503 引导。

### Task 3：LLM 配置前端页（管理员专属）[P1, 依赖 2]
- [x] 新增「模型配置」页：供应商/Base URL/模型（下拉来自 test 返回）/API Key（明文仅本次输入展示）、「测试连通」「保存」「吊销」；导航增加入口。
- [x] 非 admin 会话隐藏入口且后端 403。
- [x] 验证：以 admin 登录保存 DeepSeek 配置并测试连通成功；以 developer 登录访问该页显示无权限/403。

### Task 4：草稿生成后端——两轮 RAG + 单轮修订 [P1, 依赖 2]
- [x] 新增 `POST /api/llm/tasks/draft`（首轮生成：body 为自然语言描述 + 上下文连接清单）与 `POST /api/llm/tasks/draft/revise`（修订轮：body 为草稿 JSON + 修订指令，单轮生成）。
- [x] 骨架轮输出经校验后进入第二轮；第二轮注入源表结构后输出完整 `TaskConfig` JSON；两轮均做输出反序列化，非法输出带错误信息自动重试一次。
- [x] 生成/修订调用写 `log_event` 审计（actor/provider/model/轮次/成败）。
- [x] 验证：本地 mock provider（扩展 RecordingProvider）分别返回骨架与完整草稿，接口返回结构化草稿；非法 JSON 首次返回重试后成功；审计日志可见调用记录。

### Task 5：表结构三级降级链 [P1, 依赖 4]
- [x] 第二轮表结构来源：① Catalog → ② 连接只读查 information_schema（复用 `run_select`，仅 MySQL/PostgreSQL 生效）→ ③ LLM 推断并标记「字段为推断，待核对」。
- [x] 验证：对 Catalog 已有表走 ① 不额外查询；对 Catalog 缺失且连接可达的表走 ② 返回真实列；连接不可达时草稿带推断标记仍可生成。

### Task 6：草稿校验与佐证（conn_key 校验 + 目标列警告 + 样例预览）[P1, 依赖 4/5]
- [x] 草稿返回前校验：`TaskConfig` 反序列化成功；`source_conn_key/target_conn_key` 必须存在于连接库，否则拒绝并提示；目标字段不在目标表已知列内时返回 `warnings[]`。
- [x] 样例预览：源连接可读时内嵌 10 行样例（复用查询预览），不可读不阻断。
- [x] 验证：虚构 conn_key 的草稿被拒；目标列不存在时草稿带 warning；MySQL 源可读到样例数据；源不可达时草稿仍返回且样例为空提示。

### Task 7：对话式建任务前端页 [P1, 依赖 6]
- [x] 新增「对话式建任务」页（独立导航）：对话区 + 草稿卡（字段映射表、目标列警告、10 行样例、修订输入）、「确认创建」。
- [x] 确认后调既有 `POST /api/tasks` 保存，成功后跳转任务列表可见该任务。
- [x] 验证：输入「每天从 orders 增量抽已支付订单到 orders_dw，主键 id」→ 草稿卡展示映射/样例；输入「改成全量」→ 修订后 mode=append；确认创建后任务列表出现该任务，运行可触发。

### Task 8：测试补全与回归 [P1, 依赖 1-7]
- [x] 扩展 `RecordingProvider` 覆盖两轮/修订/非法 JSON 重试；配置页 vault 加解密往返、吊销后 503、非 admin 403；三级降级链三路；全量 `pytest + ruff + vite build` 通过。
- [x] 验证：`pytest` 全绿；`ruff check` 无错误；前端 `vite build` 成功；本地启动后按 Task 3/6/7 的真实交互复现通过。

## 验收补充
- 以上 1-8 均须在本地启动后端（:8000）后通过真实交互或 API 验证，并回归既有测试不落地破坏。
- 凭据安全验收需给出实际复现步骤：保存 API Key → GET 仅见掩码 → 吊销 → 生成返回 503 引导。
