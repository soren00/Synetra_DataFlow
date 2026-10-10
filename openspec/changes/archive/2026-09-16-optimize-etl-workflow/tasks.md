# Tasks: ETL 功能优化

依赖说明：P1 形成各能力端到端闭环，按「连接 → 查询 → 任务表单 → 定时 → API 连接」推进。A 组跨到前端，验证均含本地启动后端的可观察行为。

## P1（端到端闭环）

### Task 1：双向连接（direction=both）[P1]
- 后端 `ConnectionDirection` 增加 `BOTH`；`list_connections` 不做阻断性过滤。
- 前端连接表单方向下拉增加「双方」；连接列表展示新增方向中文映射。
- 前端任务表单源下拉= `source ∪ both`，目标下拉= `target ∪ both`。
- 验证：创建 direction=both 的连接后，在新建任务时该连接同时出现在源与目标下拉可选中；存量 source/target 连接行为不变。

### Task 2：只读查询与任务预览真实数据展示 [P1]
- 修复 [Query.jsx](file:///home/soren/project/synetra/services/frontend/src/pages/Query.jsx#L72) 与 [Tasks.jsx](file:///home/soren/project/synetra/services/frontend/src/pages/Tasks.jsx#L201)：按列下标渲染位置数组行，`Array.isArray(row)` 时 `row[i]`，对象行 `row[c]`。
- 验证：对 MySQL 连接执行 `SELECT id, name FROM src_orders`，查询页展示 id/name 真实值而非「—」；任务预览同样显示真实值。

### Task 3：任务表单——可关闭预览弹窗 [P1]
- 数据预览抽为独立可关闭 `Modal`，移除内嵌滚动块。
- 验证：点「查询预览」弹窗展示样例，关闭后表单已填项不丢失；空源/未填对象时给出提示。

### Task 4：任务表单——转换规则/表达式可理解性 [P1]
- 每条字段转换规则增加说明与示例文案；受限表达式增加「表达式助手」（读取 `app/expr` 支持函数/操作符，展示字段引用写法与示例）。
- 验证：选择 expression 规则时前端展示白名单函数与示例；输入白名单外函数，后端仍按既有校验拒绝并提示。

### Task 5：任务表单——抽取模式多字段组件 [P1]
- 模式从单值下拉改为条件组件：增量展开增量字段/水位/首跑策略，upsert 展开主键多值；向后端 `TaskConfig` 输出不变。
- 验证：选增量显示三个增量相关控件并保存；选 upsert 可维护多主键；旧任务配置仍可正确解析加载。

### Task 6：任务表单——定时频率可选 [P1]
- 定时控件改为「常用频率下拉 + 自定义 cron」，产出 cron 字符串写入 `form.schedule`。
- 验证：选「每10分钟」生成 `*/10 * * * *` 并保存；切自定义 cron 可输入并校验非法 cron 拦截。

### Task 7：定时任务管理（后端计划实体 + 启停 + next_run）[P1]
- 任务记录/`TaskConfig` 增加 `schedule_enabled` 与下次运行字段；新增计划聚合端点（列举、启停开关、返回 cron/enabled/last_run_at/next_run_at）。
- 验证：对已配定时任务开启/停用后，接口返回对应 enabled 与 next_run_at；停用后计划不再被轮询触发。

### Task 8：定时任务管理（前端页 + 本地调度触发）[P1, 依赖 7]
- 新增独立「定时任务」管理页：列出任务、cron、启用开关、上次/下次运行、操作。
- 本地调度轮询器（config 开关，未配 DS 时默认开启）按启用任务 next_run_at 触发 `POST /tasks/{key}/run` via=schedule。
- 验证：本地开启开关、把某任务 cron 设为每1分钟且该任务有可执行脚本，等待触发后 run_history 出现 via=schedule 的记录，触发方式标记为 schedule；停用后不再触发。

### Task 9：Postman 式 REST/API 连接 [P1]
- 连接表单 api 类型：method/auth 改下拉；新增 Query Params 与 Headers 键值对编辑，序列化为 dict 存 `meta`。
- 验证：用下拉选择 GET + bearer，填写 kv Params/Headers 保存；对本地 HTTP 端点执行样例拉取返回正确结果；API 连接编辑回填写完整。

## P2（扩展/轻量）

### Task 10：Kettle 对齐轻量项——连接字段下拉化与运行明细入口 [P2, 依赖 1/9]
- 连接表单其余类型（MySQL 端口等）尽可能下拉/占位提示；ETL 任务列表增加运行明细/日志查看入口。
- 验证：MySQL 连接端口等常用值可下拉或带占位提示；任务列表可查看某次运行的明细日志。

## 验收补充
- 上述 1-9 均须在本地启动后端（:8000）后通过真实交互或 API 验证，并回归既有测试（pytest + ruff + vite build）不落地破坏。
- 定时本地触发的验收需给出实际复现步骤（开启开关 → 到点触发 → run_history 可见）。