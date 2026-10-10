# Tasks: 修复「停用/改cron」缺陷并提供 cron 预设

验收标准：所有任务均须在本地启动后端（:8000）后通过真实页面交互或 API 验证，并回归既有测试（pytest + ruff + vite build）无破坏。

## 任务清单

### Task 1：API 客户端新增 patch 方法
- [x] 在 [client.js](file:///home/soren/project/synetra/services/frontend/src/api/client.js#L53-L58) 的 `api` 对象新增 `patch: (path, body) => request("PATCH", path, { body })`。
- [x] 验证：前端代码中 `api.patch` 不再为 `undefined`；`vite build` 通过。

### Task 2：修复「停用/启用」调用故障
- [x] 保持 [Schedules.jsx](file:///home/soren/project/synetra/services/frontend/src/pages/Schedules.jsx#L18-L26) `toggle` 调用 `api.patch`，借助 Task 1 使其走通。
- [x] 验证：在定时任务页点击「停用」→ 状态徽标由「已启用」切为「已停用」，按钮切为「启用」，并显示成功提示；再点「启用」恢复；期间不再出现 `TypeError: api.patch is not a function`。

### Task 3：改cron 升级为「预设 + 自定义」编辑弹窗
- [x] 在 [Schedules.jsx](file:///home/soren/project/synetra/services/frontend/src/pages/Schedules.jsx#L28-L40) 移除 `window.prompt`，改为受控 `Modal`：常用频率预设下拉（复用 ETL 任务同一套预设）+ 自定义 cron 文本框，单一 `cronDraft` 结果态。
- [x] 校验沿用「非空 + 恰好 5 段」，不通过则提示且不提交；取消/关闭不提交。
- [x] 验证：
  - 选择「每 10 分钟」→ 生成 `*/10 * * * *`，保存后列表 Cron 更新；
  - 选择「自定义 cron」输入 `0 3 * * *` → 保存后更新；
  - 输入非法（如 `0 2 * *`）→ 拦截并提示，不提交；
  - 取消 → 原 cron 不变。

## 验收补充
- 全流程复用本地已启动后端，回归既有 pytest（`.venv/bin/python -m pytest`）与 `ruff check`、`npm run build`，确认无回归破坏。
- 交付物仓库不引入未使用的冗余文件。