# Design: LLM ETL 后续增强

## 决策

### D1：自动建表（尽力而为）
- 落点：`etl_sdk/relational.py` 的 `load()` 前 `_ensure_table`：先探测 `SELECT 1 FROM t LIMIT 1`（方言探测 SQL），表缺失才 CREATE。
- 列定义：优先字段映射 `columns`（`cast_to`/`kind` → 基础类型），未覆盖列按样例行值推断，主键列缺省按 BIGINT 补列。
- 方言：MySQL/PG/SQL Server 用 `CREATE TABLE IF NOT EXISTS`；Oracle 不支持该子句，改用探测 + `ORA-00955` 忽略。
- 标识符安全：表/列名匹配 `^[A-Za-z_][A-Za-z0-9_]*$`，否则拒绝（防注入）。
- 主键：建表时按 `primary_keys` 声明 PRIMARY KEY。
- 空结果且无列定义 → 明确报错提示手动建表。

### D2：运行失败根因透出
- `run_task` 失败时从 run 记录提取 traceback 末行（≤300 字符）作为 `detail` 返回；前端去掉「任务尚未生成执行脚本」误导兜底文案。

### D3：LLM 请求层错误处理
- `DeepSeekProvider.complete/list_models` 解析供应商响应体 `error.message` 作为可读原因。
- 请求层错误（HTTP 4xx/5xx/网络）不触发「非法输出」重试，直接 502 + 原因；`ProviderAuthError` 走 503 引导。

### D4：草稿连接指定覆盖
- `DraftRequest` 新增可选 `source_conn_key/target_conn_key`；骨架轮 prompt 声明「必须使用指定连接」，解析后强制覆盖 LLM 输出，避免选错。

### D5：AI ETL 任务页交互
- 三选并排（项目可选/数据源/目标数据库 flex 均分）。
- 示例文本可点击填入输入框；描述框 3→12 行；busy 时消息流内转圈气泡（`mac-spinner`）；确认创建成功/失败弹 Modal（成功/失败 Callout）。

### D6：模型名称可输入
- LLMConfig 模型字段改为 Input + datalist：可自由输入，测试连通后仅空时回填首个模型，不覆盖用户输入。
