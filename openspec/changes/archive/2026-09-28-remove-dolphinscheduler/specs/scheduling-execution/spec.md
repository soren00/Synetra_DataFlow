## REMOVED Requirements

### Requirement: DolphinScheduler 定时调度
**Reason**: DolphinScheduler 集成长期处于预留骨架状态、未被实际启用，与「Linux 单机一键部署、无冗余组件」约束及本地调度唯一化目标冲突。
**Migration**: 定时执行交由本地调度触发（见 `### MODIFIED Requirements` 中的「本地调度触发支撑」），用户无需额外装配或连接 DolphinScheduler。

## MODIFIED Requirements

### Requirement: 本地调度触发支撑
系统 SHALL 通过本地调度能力作为唯一调度机制，按已启用任务的 cron 计划定时触发执行；本地调度依赖平台自身进程内轮询，不要求部署或连接任何外部调度引擎。

#### Scenario: 本地定时触发
- **WHEN** 系统本地调度开关开启，某已启用定时任务到达计划触发时间
- **THEN** 系统触发该任务执行并记录运行，触发方式标记为 schedule

#### Scenario: 停用任务不触发
- **WHEN** 定时任务处于停用状态
- **THEN** 到达计划时间系统不触发该任务执行