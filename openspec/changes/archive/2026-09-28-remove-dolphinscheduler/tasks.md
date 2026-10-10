## 1. 调度模块清理（[P1]）

- [x] 1.1 删除 [schedule/__init__.py](file:///home/soren/project/synetra/services/backend/app/schedule/__init__.py) 中的 `DolphinSchedulerClient`、`Scheduler`、`normalize_cron`、`_http_json` 及不再使用的 `urllib.request`/`json` 导入，保留 `LocalScheduler`、`new_run_id`、`run_with_retry`、`next_cron_time`、`_field_values`、`_parse_iso` 与 `DEFAULT_*` 常量，并同步更新模块 docstring 与 `__all__`。验证：`grep -nE "DolphinScheduler|class Scheduler|normalize_cron|urllib" services/backend/app/schedule/__init__.py` 无输出，且 `python -c "import app.schedule"` 通过。
- [x] 1.2 修改 [api/schedule.py](file:///home/soren/project/synetra/services/backend/app/api/schedule.py)，移除 `Scheduler`/`ds_bind_worker_group` 引用：`schedule_task` 仅持久化 cron 并清空 `schedule_next_run`，`patch_schedule` 更新 cron 分支同样去掉 `Scheduler.upsert` 调用，改为保存 cron 后置空 `schedule_next_run`。验证：`grep -nE "Scheduler|ds_bind" services/backend/app/api/schedule.py` 无输出，且导入 `from app.schedule import new_run_id, run_with_retry` 正常。

## 2. 配置清理（[P1]）

- [x] 2.1 删除 [config.py](file:///home/soren/project/synetra/services/backend/app/core/config.py) 中的 `ds_api_url`、`ds_api_token`、`ds_bind_worker_group`，并修正 `metadata_db_dsn` 与调度相关注释中残留的 DolphinScheduler 描述。验证：`grep -nE "ds_api|ds_bind|DolphinScheduler" services/backend/app/core/config.py` 无输出，且 `Settings()` 可正常实例化。

## 3. 测试调整（[P1]）

- [x] 3.1 清理 [test_monitor_schedule.py](file:///home/soren/project/synetra/services/backend/tests/test_monitor_schedule.py)：删除 `DolphinSchedulerClient`、`Scheduler`、`normalize_cron` 相关用例与导入，保留并确认 `LocalScheduler`、`next_cron_time`、`run_with_retry` 用例覆盖。验证：`pytest services/backend/tests/test_monitor_schedule.py -q` 全部通过。

## 4. 集成回归（[P1]，依赖 1、2、3）

- [x] 4.1 运行后端全量测试，确认删除 DS 后无破坏。验证：`pytest services/backend/tests -q` 全部通过（或返回既有通过基线）。
- [x] 4.2 端到端验证本地定时触发：启动后端，保存一个配置了 cron 的任务，观察 `LocalScheduler` 到期触发并在运行历史产生 `trigger_via=schedule` 记录。验证：`POST /api/tasks` 保存任务 → `POST /api/tasks/{key}/schedule` 设置 cron → 观察 `GET /api/runs` 出现 schedule 记录。

## 5. 部署与文档清理（[P1]）

- [x] 5.1 删除 [install.sh](file:///home/soren/project/synetra/deploy/install.sh) 中 `note_ds` 函数定义与 `main` 中的调用，并清理文件头注释里对 DolphinScheduler 的描述。验证：`grep -nE "DolphinScheduler|note_ds" deploy/install.sh` 无输出，`bash -n deploy/install.sh` 语法通过。
- [x] 5.2 修改 [openspec/config.yaml](file:///home/soren/project/synetra/openspec/config.yaml) 的 `context` 技术栈描述，移除「DolphinScheduler（调度执行，不自建执行器）」，改为自研本地调度执行（`LocalScheduler`）。验证：`grep -n "DolphinScheduler" openspec/config.yaml` 无输出，`openspec validate --change remove-dolphinscheduler` 通过。
- [x] 5.3 全代码库扫描确认无残留 DS 引用（排除历史归档 `openspec/changes/archive/`）。验证：`grep -rn "DolphinScheduler\|ds_api\|ds_bind_worker_group" --exclude-dir=archive --exclude-dir=node_modules --exclude-dir=.git .` 仅命中本 change 的 delta/设计文档中作为「被移除内容」的表述，无生产代码命中。