# Design: ETL 功能优化

目标：在不破坏既有数据契约与部署的前提下提升连接、查询、任务表单、定时与 API 调试的可用性。

## 决策 1：双向连接（direction=both）

**决策**：`ConnectionDirection` 枚举新增 `BOTH = "both"`；前端连接表单方向选项增加「双方」；任务表单的源下拉展示 `source ∪ both`，目标下拉展示 `target ∪ both`。

**理由**：一个可读写的库（如 MySQL 同库回灌）天然既当源又当目标，强制二选一制造了阻力；枚举字段加一个值即可覆盖，存量 source/target 无需迁移。

**被否决的方案**：取消 direction 语义、任务直接展示全部连接。否决理由：方向语义仍是平台对连接角色的一种有效标注与过滤，彻底移除会弱化列表可读性，且改动面大于加一个枚举值。

文件影响面：
- 后端 [connection.py](file:///home/soren/project/synetra/services/backend/app/models/connection.py#L26-L30) 加 BOTH
- 前端 [Connections.jsx](file:///home/soren/project/synetra/services/frontend/src/pages/Connections.jsx#L5-L6) 加「双方」选项
- 前端 [Tasks.jsx](file:///home/soren/project/synetra/services/frontend/src/pages/Tasks.jsx#L38-L39) 源/目标过滤条件放宽（both 双向可见）

## 决策 2：只读查询统一显示占位符 → 修正为按列下标渲染

**根因**（已在 explore 阶段定位）：后端 `run_select` 返回 `QueryResult.rows: list[list]`（位置数组，[executor.py](file:///home/soren/project/synetra/services/backend/app/queries/executor.py#L26-L27)），而前端用 `row[c]`（把行当对象键取列名）渲染，导致每个 `row[列名]` 恒为 `undefined` → 显示 `"—"`。受影响处：[Query.jsx](file:///home/soren/project/synetra/services/frontend/src/pages/Query.jsx#L72)、[Tasks.jsx](file:///home/soren/project/synetra/services/frontend/src/pages/Tasks.jsx#L201)。

**决策**：保持后端 rows 为位置数组的契约不变，仅前端渲染改为按「列下标」取值：`columns.map((c, i) => row[i])`。API 样例（对象数组）走 `row[c]`，二者在渲染层做一次归一化判断（`Array.isArray(row)`）。

**理由**：改动最小、不破坏 query API 的既有消费方；API 样例本就是对象数组，故不能无脑全部按下标，需类型判断。

**被否决的方案**：后端把 `rows` 改为 `list[dict]`。否决理由：会破坏 `rows: list[list]` 契约，影响测试、脚本与任何下游按索引的消费；改动面大于纯前端适配。

## 决策 3：任务表单交互增强（预览弹窗 / 规则说明 / 抽取模式组件 / 定时可选）

**决策**：
- 预览段从模态框内滚动块抽为独立的可关闭 `Modal`；「查询预览」按钮打开，关闭不丢表单上下文。
- 字段转换规则增加说明文案与 tooltip；受限表达式增加「表达式助手」：读取 `app/expr` 支持函数/操作符，展示字段引用写法与示例。
- 抽取模式由单一 `Select(mode)` 改为条件组件：增量展开 `incremental_field / watermark_field / 首跑策略`，upsert 展开主键多值（逗号分隔 → list）。
- 定时由 cron 文本框改为「常用频率下拉 + 自定义 cron」；`form.schedule` 统一产出 cron 字符串，向后端契约不变。

**理由**：全部是前端展示层增强，后端 `TaskConfig`（[task_config.py](file:///home/soren/project/synetra/services/backend/app/models/task_config.py#L47-L62)）字段已足以承载这些配置，无需改数据模型。

**被否决的方案**：把抽取模式/定时改成后端接口动态拉取选项。否决理由：选项集合固定且体量小，前端常量即可，避免无谓网络往返。

## 决策 4：新增定时任务管理视图 + 本地调度触发

**现状**：此前定时只记录为任务的裸 cron 字符串，缺少本地轮询触发，故定时从不真正触发——这正是「没有定时任务管理」的根因。

**决策**：
1. `TaskConfig`/任务记录增加调度启用状态（`schedule_enabled: bool = True`）与调度元信息（首次/下次运行时间）。
2. 新增后端调度 CRUD 端点：`GET/POST/PATCH/DELETE /schedules`（或复用 `/tasks/{key}/schedule` 扩展），返回 cron、enabled、last_run_at、next_run_at。
3. 新增本地轮询调度器：后台线程/任务（config 开关 `local_scheduler_enabled`，默认开启，可显式关闭）按启用任务的 next_run_at 触发 `POST /tasks/{key}/run` via=schedule。
4. 前端新增独立「定时任务」管理页：列出任务、cron、启用开关、上次/下次运行、操作。

**理由**：把计划从「任务的裸 cron 字符串」提升为可管理的实体，且让本地能真正按计划触发，满足「能跑」的验收；对应 spec 的“本地调度触发支撑”。

**被否决的方案**：引入 APScheduler 作为硬依赖常驻。否决理由：加重部署；用轻量自实现（epoch 轮询 + sqlite 记录）即可满足单机需求，且可在 config 关闭。

## 决策 5：Postman 式 REST/API 连接

**决策**：连接表单 api 类型的元数据由「松散字符串」升级为结构化：`method`（下拉：GET/POST/PUT/DELETE…）、`auth`（下拉：none/bearer/basic）、`headers`/`params`（键值对列表，序列化为 `dict` 存 meta）。后端 `fetch_sample` 已支持 method/headers/params/auth_scheme（[query.py](file:///home/soren/project/synetra/services/backend/app/api/query.py#L90-L114)），几乎零改动；主要工作量在 [Connections.jsx](file:///home/soren/project/synetra/services/frontend/src/pages/Connections.jsx#L16) 的表单 UI。

**理由**：方法与认证用下拉可消除误输；键值对结构化匹配数据工程师调试 REST 的习惯，且直接映射 `fetch_sample` 参数。

**被否决的方案**：后端 meta 改强类型 schema。否决理由：`meta: dict[str,str]` 契约广泛使用，改强类型侵入面大；前端结构化序列化回字符串 dict 即可满足现契约。

## 决策 6：Kettle 对齐（轻量）

**决策**：本变更只做「连接表单字段下拉化」与「任务运行明细/日志入口增强」等低成本易用性项，对齐 Kettle 的字段选择与运行可见性；不做可视化步骤流水线画布。

**理由**：步骤流水线（多步串联、多端点、单步测试）是 Kettle 的精髓但改动极大，与 1-5 同批实现会失控；拆成独立迭代更稳。

**被否决的方案**：本批实现完整 Kettle 步骤画布。否决理由：范围爆炸，超出本变更一次性交付；已明确列入 proposal Non-Goals。

## Open Questions（不影响 P1 规格与任务分解）

- 定时管理入口形态（独立页 vs 并入任务页）——设计倾向独立页，implement 时按此推进。
- cron 合法性校验与「下次运行时间」计算的后端实现细节不影响任务分解。