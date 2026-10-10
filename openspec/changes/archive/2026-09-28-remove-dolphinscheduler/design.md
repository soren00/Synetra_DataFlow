## Context

当前调度层为「DS + 本地降级」双通道：`DolphinSchedulerClient` 与 `Scheduler` 只是 REST 适配骨架，真实建工作流逻辑未完成；实际触发始终走 `LocalScheduler` 后台轮询线程（见 [schedule/__init__.py](file:///home/soren/project/synetra/services/backend/app/schedule/__init__.py)）。动机与范围见 proposal.md。本次改动是把本地调度收敛为唯一实现，删除 DS 通道及其配置、测试、部署与文档痕迹。

## Goals / Non-Goals

**Goals:**
- 删除所有 DS 代码/配置/测试/部署说明，使 `LocalScheduler` 成为调度唯一实现。
- 定时注册/修改接口语义收敛为「仅持久化 cron、启用状态与 next_run」，不再有远端工作流副作用。
- 消除 spec/config 中 DS 相关描述，保持文档与实现一致。

**Non-Goals:**
- 不增强本地调度的能力（不新增 DAG、不引入分布式调度、不改执行器本身）。
- 不改动「手写任务低权限执行」的既有资源配额沙箱行为，仅将其从 DS worker group 表述改为本地沙箱表述。
- 不重写 `openspec/changes/archive/` 下的历史归档（历史记录保持原样）。

## Decisions

**决策 1：本地调度作为唯一机制，删除 DS 客户端与 `Scheduler` zip**
- 理由：DS 通道从未真正启用，保留它只是维护负担，且与单机一键部署约束冲突。
- 被否决方案：保留 DS 客户端骨架作「未来扩展点」。否决原因——未使用的死代码误导后续维护，未来真要分布式调度时再引入也不迟。

**决策 2：定时注册/修改语义改为「本地持久化」**
- `POST /tasks/{key}/schedule` 与 `PATCH /schedules/{key}`（更新 cron）不再调用 `Scheduler.upsert`，改为：保存 cron → 清空并重算 `schedule_next_run`（由 `LocalScheduler.tick` 在下一轮用 `next_cron_time` 推进）。
- 理由：DS 工作流副作用不存在了，剩下的只有「把 cron 落库 + 触发时间推进」。
- 被否决方案：保留一个「空 `Scheduler`」占位调用。否决原因——增加无用抽象且误导。

**决策 3：手写任务低权限执行改由本地沙箱表述**
- 删除「独立 DolphinScheduler worker group」依赖，执行仍是本地受限子进程 + `resource.setrlimit` 配额（[sandbox.py](file:///home/soren/project/synetra/services/backend/app/security/sandbox.py) 已具备 CPU/内存/磁盘限制）。
- 理由：无 DS 后可执行体就是子进程，隔离应在本地完成。
- 被否决方案：在 spec 中继续保留 worker group 措辞。否决原因——与实际执行链路不符。

**决策 4：删除配置项 `ds_api_url` / `ds_api_token` / `ds_bind_worker_group`**
- 理由：删除后无消费方；`pydantic-settings` 的 `extra="ignore"` 保证旧 `.env` 残留项不会报错。
- 被否决方案：保留配置项并在代码中忽略。否决原因——多余配置项仍会出现在配置面，误导用户以为可接入 DS。

**决策 5：历史归档不改写**
- `openspec/changes/archive/2026-09-16-dataflow-v2/` 等历史变更中的 DS 表述属历史记录，不予修改，避免破坏归档完整性。

## Risks / Trade-offs

- [单点调度，无高可用] 本地轮询随主进程单实例运行，进程重启期间不触发。→ 可接受：单机部署约束；`next_run` 已持久化于任务表，重启后由 `tick` 自动续算，不丢计划。
- [无分布式扩容能力] 移除 DS 后，若未来任务量大到需多 worker 横向扩展将缺载体。→ 属 Non-Goals；需求出现时另行评估引入专用调度器。
- [`next_run` 初始化依赖轮询] 首次保存 cron 后，首次触发需等下一轮 `tick`（默认 20s）才写入 next_run。→ 可接受：间隔可配，且手动「运行」不受影响。

## Migration Plan

- 无 DS 外部状态需迁移；DS 未接入过真实服务。
- `.env` 中若已存在 `SYNETRA_DS_API_*` 残留项，因 `extra="ignore"` 被忽略，无需迁移。
- 回滚：本改动为纯删除与收缩，回滚即还原被删除的 DS 代码/配置/测试；schedule API 的持久化语义对本地调度透明。