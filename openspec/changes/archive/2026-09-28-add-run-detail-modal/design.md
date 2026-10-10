# Design: 运行历史详情弹窗

目标：让数据工程师在总览页运行历史中「点一条即可看清这次运行」，包括基本结果、失败原因、任务上下文与日志，且不引入后端契约变更与额外依赖。

## Context

- 现状：[`Overview.jsx`](../../../services/frontend/src/pages/Overview.jsx) 的「最近运行历史」表格只渲染 `run_id / task_key / trigger_via / status / rows_total / started_at` 六个字段，行不可点击，也没有任何详情入口；失败运行的 `error` 字段与日志完全没有暴露给用户。
- 可用数据（后端已存在，无需新增）：
  - `GET /api/runs/{run_id}` → run 记录，字段：`run_id, task_key, project, status, trigger_via, started_at, finished_at, rows_total, error, attempt`（[`monitor.py`](../../../services/backend/app/api/monitor.py) 第 90-92 行，存储层 `store.list_runs`）。
  - `GET /api/runs/{run_id}/logs?since=` → `{ run_id, logs: [{ts, level, line}], cursor }`，`since` 为游标，天然支持增量拉取（`monitor.py` 第 97-101 行）。
  - `GET /api/tasks/{task_key}` → `TaskConfig`：`source_conn_key / target_conn_key / source_object / target_object / mode / primary_keys / incremental / schedule / schedule_next_run`（[`tasks.py`](../../../services/backend/app/api/tasks.py) 第 119-124 行），任务不存在时 404。
  - `GET /api/metrics?task_key=` → 指标列表 `{run_id, cpu_percent, memory_mb, latency_ms, duration_s, data_rows}`，**没有** `run_id` 过滤参数。
- 约束：前端 macOS 设计语言（复用 `components/ui` 与 `ui.css`）；本变更不新增后端端点、不改数据模型；详情内容需满足 specs 中三条需求（基本信息/任务上下文/日志）与降级场景。
- 现有可复用样式：`mac-detail__item / mac-detail__label / mac-detail__value`（`Connections.jsx` 展开行已在用）、`mac-card__title`、`mac-callout--*`、`mac-chip`、`mac-badge`、`.num`；组件 `Modal / ModalFooter / Badge / Callout / Skeleton / Row`。

## Goals / Non-Goals

**Goals**
- 运行历史每一行可打开详情弹窗，展示运行基本信息（含耗时与尝试次数）、失败原因、任务配置上下文、运行日志。
- 运行中的记录可自动刷新状态与日志，结束后停止刷新；弹窗关闭即释放。
- 任一子数据源失败时局部降级，不影响其余区域。

**Non-Goals**
- 不新增/修改后端端点与数据模型。
- 不做日志推送（SSE/WebSocket）、重跑/终止/下载产物。
- 不把对账与告警事件关联进运行详情（二者按 `task_key` 关联，非 `run_id`）。

## Decisions

### 决策 1：详情数据由前端组合既有三个端点，不新增后端聚合接口

**决策**：弹窗打开后并行请求 `GET /runs/{run_id}`、`GET /runs/{run_id}/logs`、`GET /tasks/{task_key}`，在前端拼装为「基本信息 + 任务上下文 + 日志」三段；`task_key` 取自当前 run 记录。

**理由**：所需字段已全部可用；三个请求量级小且可并行，一次多请求与一次聚合请求对用户体验几乎无差异，却避免了新增后端契约与其测试维护成本。

**被否决的方案**：新增 `GET /runs/{run_id}/detail` 返回 run+task+logs 聚合体。否决理由：为前端一屏展示引入后端聚合契约，后续字段演进需前后端同步改；且与既有 `/runs/{id}`、`/runs/{id}/logs` 能力重复。

### 决策 2：弹窗内容分区，复用既有详情网格与 Callout 样式

**决策**：弹窗自上而下分为四段：
1. 头部摘要：状态 `Badge`（复用 `STATE_TONE / STATE_CN`）、`Trigger` chip（复用 `TRIGGER_CN`）、运行实例标识（等宽字体，便于复制）；
2. 基本信息网格：运行实例、任务、所属项目、触发方式、开始时间、结束时间、耗时、尝试次数、写入行数，使用 `mac-detail__item` 键值对；
3. 任务上下文网格：源连接、目标连接、源对象、目标对象、写入模式、调度（cron/下次运行）；不可获取时该区块显示 warning Callout；
4. 日志区：等宽字体滚动区（复用 `Monitor.jsx` 中 `<pre>` 日志展示写法），行内追加空状态或失败重试。

**理由**：`mac-detail__*` 已是项目内既有的键值详情样式，直接复用可与连接详情行保持视觉一致，零新增 CSS，且深浅色主题自动适配。

**被否决的方案**：新写表格型详情布局或引入第三方 JSON 查看器。否决理由：前者重复造样式且容易与设计语言漂移，后者增加依赖且不符合「面向数据工程师的精简信息密度」。

### 决策 3：入口为「整行可点击 + 行尾显式详情按钮」，不做行内展开

**决策**：表格行整体可点击打开弹窗（带 hover 手型与轻量高亮），同时在行尾操作区提供 `eye` 图标按钮作为显式入口（按钮点击阻止冒泡，避免重复触发）；已有的状态筛选（全部/成功/失败）保持不变。

**理由**：详情包含长日志与多段信息，弹窗可提供固定高度与滚动，且一次只打开一条，便于管理轮询；显式按钮保证键盘与不熟悉行点击的用户也能发现入口。

**被否决的方案**：仿照 `Connections.jsx` 的行内展开详情行（`mac-detail-row`）。否决理由：日志量大时行内展开会把表格高度撑成不可控，且多行可同时展开会导致多个轮询并存；筛选切换时的展开态管理也更复杂。

### 决策 4：运行中记录以 `since` 游标增量轮询日志，结束后自动停止

**决策**：以 `finished_at` 是否为空作为「是否仍在运行」的主判据（主状态枚举 `running / retrying` 作为辅助）。仍在运行时每 3 秒：用上次返回的 `cursor` 作为 `since` 增量拉取日志并追加（不重排已有条目）；同时重新拉取 `GET /runs/{run_id}` 刷新状态与结束时间。`finished_at` 出现后立即停止轮询。弹窗关闭或组件卸载时清理定时器。

**理由**：`/runs/{id}/logs` 已返回 `cursor`，增量拉取可避免全量重拉导致的滚动跳动与重复渲染；`finished_at` 比状态字符串更稳定地表达「已结束」。

**被否决的方案 A**：SSE/WebSocket 实时推送。否决理由：后端无事件总线，属于本变更 Non-Goal 范围。
**被否决的方案 B**：固定间隔整体轮询日志（不带 `since`）。否决理由：日志列表整体重建会打断阅读位置，且重复传输已读内容。

### 决策 5：局部降级，弹窗永远不因单一接口失败而整体不可用

**决策**：
- 打开弹窗时先用列表行数据渲染基本信息首屏（避免白屏），随后以 `/runs/{run_id}` 的返回覆盖；
- `/tasks/{task_key}` 返回 404 或失败 → 任务上下文区展示「任务配置不可获取（可能已被删除）」warning Callout，其余区域照常；
- `/runs/{run_id}/logs` 失败 → 日志区展示 danger Callout 与「重试」按钮；
- `/runs/{run_id}` 失败 → 弹窗内顶部 danger Callout，保留兜底数据并允许关闭。

**理由**：运行详情是排障入口，最需要「能拿到多少看多少」；整体失败会让用户退回到无信息的原始状态。

**被否决的方案**：任一请求失败即整屏错误或自动关闭弹窗。否决理由：与排障目标相悖，且会让用户误以为记录不存在。

### 决策 6：抽出独立组件 `RunDetailModal`，由页面传入 run 行数据

**决策**：新增 `services/frontend/src/components/RunDetailModal.jsx`，props 为 `{ run, onClose }`，内部自管数据拉取、加载态与轮询；`Overview.jsx` 只维护「当前打开的 run」状态。`Monitor.jsx` 的复用列为 P2。

**理由**：`Overview.jsx` 已承载统计卡与筛选逻辑，再把三段数据拉取与轮询塞进去会迅速不可维护；独立组件也为 Monitor/Tasks 后续复用留出入口。

**被否决的方案**：直接在 `Overview.jsx` 内实现弹窗与轮询逻辑。否决理由：单文件职责膨胀，且无法在不复制代码的情况下复用到其他运行列表。

### 决策 7（P2）：指标区块由客户端筛选，不新增后端过滤参数

**决策**：以 `GET /metrics?task_key=<task_key>` 拉取指标后，在客户端筛选 `run_id === 当前运行` 的记录，命中则在详情中追加「资源与指标」区块（CPU、内存、延迟、耗时、数据行数）；未命中则不渲染该区块。

**理由**：`/metrics` 无 `run_id` 参数，为可选区块修改后端不划算；客户端筛选在单任务指标量级下开销可忽略。

**被否决的方案**：为 `/metrics` 增加 `run_id` 查询参数。否决理由：超出本变更「不改后端」的边界，且收益仅限一个可选展示区块。

## Risks / Trade-offs

- [长时间停留导致轮询累积请求] → 仅对「未结束」的运行轮询，间隔 3 秒，使用 `since` 增量，弹窗关闭/卸载立即清理定时器。
- [历史运行的 task 已被删除] → `/tasks/{key}` 404 时降级为提示（spec 已约束），不影响基本信息与日志。
- [日志条目过多影响可读性] → 日志区固定高度内部滚动，按时间顺序追加，每行 `时间戳 [级别] 内容`。
- [首屏兜底数据与详情接口结果短暂不一致] → 不做乐观合并，接口返回后整体覆盖兜底值。
- [运行中判定依赖状态枚举] → 以 `finished_at` 为空为主判据，避免新增状态枚举时判定失效。
- [行整体可点击可能误触] → 行内已有操作（无破坏性操作）与详情按钮均阻止冒泡；不改变列表筛选与滚动行为。

## Migration Plan

纯前端增量改造：不涉及数据迁移与后端部署。上线即生效；如需回滚，撤销前端提交即可恢复到无详情入口的现状，存量数据与接口契约不受影响。

## Open Questions

- Monitor 页运行表格是否同步接入该详情组件（已列为 P2 任务，可在实现时确认）。
- 「资源与指标」区块是否随 P1 一起交付（P2，取决于 `/metrics` 中 `run_id` 的实际命中率，实测无数据则默认不渲染）。
