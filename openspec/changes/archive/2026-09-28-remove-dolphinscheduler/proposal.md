## Why

DolphinScheduler（以下简称 DS）集成在项目中是「名存实亡」的预留骨架：`DolphinSchedulerClient`/`Scheduler` 仅为 REST 适配层、真实建工作流逻辑未完成，部署脚本也明确不自动安装 DS，实际触发始终走本地 `LocalScheduler` 轮询。这一双通道设计带来维护成本与文档/实现持续不一致，且与「Linux 单机一键部署、无冗余组件」的约束冲突。当前调度需求（单任务 5 字段 cron、手动触发、重试、告警、启停视图）本地已完整覆盖，故彻底移除 DS、将本地调度收敛为唯一实现。

## What Changes

- **BREAKING** 移除 DolphinScheduler 集成：删除 `DolphinSchedulerClient`、`Scheduler`、`normalize_cron`、`_http_json` 及 DS REST 适配层；调度唯一实现为本地 `LocalScheduler` 后台轮询线程（取代旧的 `Scheduler.upsert` 注册/更新通道）。
- 移除配置项 `ds_api_url`、`ds_api_token`、`ds_bind_worker_group`；保留 `local_scheduler_enabled`、`local_scheduler_interval_s` 作为本地调度开关。
- 定时注册/修改（`POST /tasks/{key}/schedule`、`PATCH /schedules/{key}`）不再创建 DS 工作流，仅持久化 cron、启用状态并推进 `next_run`。
- 手写 Python 任务的低权限执行身份改由本地子进程 + 资源配额沙箱（`resource.setrlimit`）实现，移除对 DS worker group 的依赖。
- 部署脚本 `deploy/install.sh` 移除 DS 装配说明；`openspec/config.yaml` 移除技术栈中的 DS 描述，改为自研本地执行器。
- 移除 DS 相关单元测试（`DolphinSchedulerClient` / `Scheduler` / `normalize_cron`）。

## Capabilities

### New Capabilities

（无新增能力）

### Modified Capabilities

- `scheduling-execution`: 移除「DolphinScheduler 定时调度」Requirement；将「本地调度触发支撑」由「未配置 DS 时的降级」改写为唯一调度机制。
- `etl-authoring`: 「手写路径低权限执行身份」Requirement 由「独立 DolphinScheduler worker group」改为本地子进程 + 资源配额沙箱的低权限/受限执行。

## Impact

- 后端调度模块 [schedule/__init__.py](file:///home/soren/project/synetra/services/backend/app/schedule/__init__.py)：删除 DS 客户端与 `Scheduler`，保留 `LocalScheduler`、`new_run_id`、`run_with_retry`、`next_cron_time`。
- 调度 API [schedule.py](file:///home/soren/project/synetra/services/backend/app/api/schedule.py)：移除 `Scheduler` 调用与 `ds_bind_worker_group`。
- 配置 [config.py](file:///home/soren/project/synetra/services/backend/app/core/config.py)：删除 `ds_api_url` / `ds_api_token` / `ds_bind_worker_group`。
- 测试 [test_monitor_schedule.py](file:///home/soren/project/synetra/services/backend/tests/test_monitor_schedule.py)：删除 DS 相关用例。
- 部署脚本 [install.sh](file:///home/soren/project/synetra/deploy/install.sh) 与项目上下文 [config.yaml](file:///home/soren/project/synetra/openspec/config.yaml)。
- 主规格 [scheduling-execution](file:///home/soren/project/synetra/openspec/specs/scheduling-execution/spec.md) 与 [etl-authoring](file:///home/soren/project/synetra/openspec/specs/etl-authoring/spec.md)。