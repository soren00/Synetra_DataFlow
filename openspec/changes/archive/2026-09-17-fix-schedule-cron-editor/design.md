# Design: 修复「停用/改cron」缺陷并提供 cron 预设

## 根因分析

1. **`api.patch` 缺失（两个缺陷的共同根因）**
   - [client.js](file:///home/soren/project/synetra/services/frontend/src/api/client.js#L53-L58) 的 `api` 对象仅暴露 `get/post/put/del`，无 `patch`。
   - [Schedules.jsx](file:///home/soren/project/synetra/services/frontend/src/pages/Schedules.jsx#L21) 的 `toggle` 与 `editCron` 都调用 `api.patch`，运行时抛出 `TypeError: api.patch is not a function`。
   - 后端 `PATCH /schedules/{task_key}` 已实现（[schedule.py#L124](file:///home/soren/project/synetra/services/backend/app/api/schedule.py#L124-L163)），接受 `{enabled}` 或 `{schedule}`。
2. **「改cron」依赖 `window.prompt`**
   - [Schedules.jsx#L28-L40](file:///home/soren/project/synetra/services/frontend/src/pages/Schedules.jsx#L28-L40) 用 `window.prompt` 手动输入；真实浏览器（尤其受限/自动化上下文）中 prompt 常返回 `null` 而直接 `return`，无任何反馈，表现为「无反应」。
3. **cron 无法预设**
   - 改cron 未复用「ETL 任务」表单已有的预设下拉：[Tasks.jsx#L10-L12](file:///home/soren/project/synetra/services/frontend/src/pages/Tasks.jsx#L10-L12) 定义 `CRON_PRESETS`。

## 技术方案

### 1) API 客户端新增 `patch`
在 [client.js](file:///home/soren/project/synetra/services/frontend/src/api/client.js#L53-L58) 的 `api` 对象中新增：

```js
patch: (path, body) => request("PATCH", path, { body }),
```

`request` 已支持任意 method + JSON body，无需其他改动。修复后「停用/启用/改cron」的 PATCH 调用均走通。

### 2) 改cron 改为「预设 + 自定义」编辑弹窗
在 [Schedules.jsx](file:///home/soren/project/synetra/services/frontend/src/pages/Schedules.jsx) 中：

- 移除 `window.prompt`，改为受控的 `Modal` 编辑态。新增本地 state：
  - `editing`（当前正在编辑的任务对象，非空时渲染 Modal）
  - `cronPick`（选中的预设值，`""` 手动、`"custom"` 自定义、或具体表达式）
  - `cronDraft`（编辑中的最终 cron 字符串，单一结果态）
- 复用与 Tasks.jsx 一致的预设下拉列表（建议抽为共享常量或就地定义同一份文案）：

  ```
  ["", "手动（不调度）"], ["*/10 * * * *", "每 10 分钟"], ["0 * * * *", "每小时"],
  ["0 2 * * *", "每天凌晨 2 点"], ["0 2 * * 1", "每周一凌晨 2 点"], ["custom", "自定义 cron…"]
  ```

  选择预设时把 `cronDraft` 设为该预设的表达式；选择「自定义」时让用户编辑 `cronDraft` 文本框。
- 校验沿用现有规则（与 Tasks.jsx `validCron` 一致）：非空 + 恰好 5 段（分 时 日 月 周）。不通过则提示且不提交。
- 确认保存：`await api.patch(`/schedules/${task.task_key}`, { schedule: cronDraft.trim() })`，成功刷新列表并展示成功 Callout；失败展示错误 Callout。
- 取消/关闭 Modal：不提交，原 cron 不变。

### 3) 停用/启用保持不变
`toggle` 逻辑不变（仍走 `api.patch` + `{ enabled: target }`），bug 修复后即恢复正常，错误反馈沿用已有 Callout。

## 涉及文件

- `services/frontend/src/api/client.js` —— 新增 `patch`
- `services/frontend/src/pages/Schedules.jsx` —— 移除 prompt，新增 cron 编辑弹窗与预设选择

后端文件无需改动。