# Tasks: LLM ETL 后续增强

全部实现并验证于归档后迭代；验证含真实运行（任务 wlC0D0H7 自动建表成功）与回归（pytest 142 passed + ruff + vite build）。

### Task 1：目标表缺失自动建表
- [x] `etl_sdk/relational.py` `load()` 前探测目标表，缺失时按字段映射（cast_to/kind）自动 CREATE；方言类型映射 MySQL/PG/Oracle/SQL Server；主键约束；标识符白名单防注入；幂等（IF NOT EXISTS）；Oracle 特殊处理。
- [x] 调用链透传列定义：`etl_runtime.buffered/_real_load` 与 `gen_config` 渲染脚本传入 `FIELDS`；LLM 手写脚本不传时按样例值推断。
- [x] 验证：任务 wlC0D0H7 此前报 `Table 'orders2' doesn't exist`，修复后运行 success（自动建表后写入）；新增 4 个测试（DDL 断言/样例推断/非法标识符拒绝/方言探测）。

### Task 2：运行失败根因透出
- [x] `run_task` 失败时返回 `detail`（traceback 末行 ≤300 字符）；前端移除「任务尚未生成执行脚本」误导文案，展示真实原因。
- [x] 验证：复跑失败任务接口返回 `detail` 含真实 ConnectorError；pytest 全绿。

### Task 3：LLM 请求层错误处理
- [x] 解析供应商 `error.message`；4xx/5xx/网络错误不重试直接 502 + 原因；`ProviderNotConfigured` 仍 503 引导。
- [x] 验证：新增测试断言 400 不重试并透出 502；pytest 142 passed。

### Task 4：草稿连接指定覆盖
- [x] `DraftRequest` 新增可选 `source_conn_key/target_conn_key`；骨架 prompt 声明必须使用指定连接，解析后强制覆盖 LLM 输出；`project` 改为可选。
- [x] 验证：新增测试（LLM 骨架输出错误连接被用户指定值覆盖）；pytest 142 passed。

### Task 5：AI ETL 任务页交互增强
- [x] 三个选择框并排（项目可选/数据源/目标数据库）；示例文本点击填入；描述框 3→12 行；生成/修订等待转圈气泡；确认创建成功/失败弹窗提醒。
- [x] 验证：vite build 成功；dev server 热更新生效；真实创建任务弹窗成功提示。

### Task 6：模型名称可输入
- [x] LLM 配置页模型字段改为 Input + datalist（可自由输入，测试连通后仅空时回填首个模型）。
- [x] 验证：vite build 成功；dev server 热更新生效。
