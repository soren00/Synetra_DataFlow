# Proposal: 修复定时任务管理「停用/改cron」故障并提供 cron 预设

## Why

「定时任务」管理页（`services/frontend/src/pages/Schedules.jsx`）存在两处缺陷，直接影响数据工程师对调度计划的自助管理：

1. **「停用」点击报错、「改cron」点击无反应**：前端 API 客户端 `api` 对象只暴露了 `get/post/put/del`，没有 `patch` 方法，而定时页的启停与改cron都调用 `api.patch`，运行时报 `TypeError: api.patch is not a function`。事故链：后端已有 `PATCH /schedules/{task_key}` 且工作正常，仅前端封装缺失导致调用失败。「改cron」在此之上还依赖 `window.prompt` 手动输入 —— 在真实浏览器（以及自动化/受限上下文）中 prompt 常被阻塞并返回 `null`，代码随即 `return`，用户看不到任何反馈，表现为「无反应」。
2. **cron 只能手动输入表达式**：改cron 没有复用「ETL 任务」表单里已有的常用频率预设下拉，用户必须记住并手敲 5 段 cron 表达式，门槛高、易出错。

## What Changes

- **前端 API 客户端新增 `patch` 方法**：`api.patch(path, body)` 以 PATCH + JSON body 调用后端。
- **「改cron」升级为预设 + 自定义的编辑弹窗**：将现有 `window.prompt` 替换为模态框（Modal），顶部是「常用频率」下拉（复用 ETL 任务已有的预设：每 10 分钟 / 每小时 / 每天凌晨 2 点 / 每周一凌晨 2 点 / 手动不调度 / 自定义），选择预设自动填充 cron 表达式；自定义 cron 文本框可手动输入并实时校验（5 段），非空校验、5 段校验与现有 ETL 任务校验保持一致。
- **「停用/启用」保持调用 `api.patch`** 但修复后走通；错误统一在页面 Callout 反馈。

## Non-Goals（明确不做）

- 不改动后端 `PATCH /schedules/{task_key}` 契约与行为；后端 cron 校验、Scheduler(DS) 同步逻辑维持现状。
- 不重新设计定时页整体交互，仅修复两个 bug 并为改cron 提供预设可选（与 ETL 任务表单对齐）。
- 不改本地/DolphinScheduler 调度触发机制。

## Assumptions

- 「停用」报错的根因即为 `api.patch` 缺失；修复前后端无其他相关改动。
- 预设 cron 复用 ETL 任务表单（Tasks.jsx）同一套 `CRON_PRESETS` 文案与表达式，保持一致体验。
- cron 校验规则沿用现有「非空 + 恰好 5 段（分 时 日 月 周）」约定，不新增更严格语法校验。
- 本变更纯前端，验收以本地启动后端后真实点击为准。

## Risks

- 若 `window.prompt` 目前被某些浏览器安全策略严格禁用，替换为 Modal 后行为受影响的仅限改cron 交互，风险低。
- 预设与自定义两种来源需被同一份结果态承接，避免「选了预设又改输入框」时的数据漂移（design 中以受控组件统一收敛到单一 schedule 值）。