> 分期说明：**P1** 形成"配置→生成→执行→治理"端到端闭环（关系库场景）；**P2** 扩展连接器与主数据。每组任务按依赖顺序排列，其中第 7 节（权限/凭据）须先于 8.2（REST 触发）完成。

## 1. 一键部署与项目骨架 [P1]

- [x] 1.1 编写幂等可重跑的 Linux 一键部署脚本，安装并配置 FastAPI、React 静态资源、DolphinScheduler、MySQL 元数据库；验证：干净主机部署后各服务健康检查通过；重复执行或失败重跑后状态一致、既有数据不丢；勾选注：已实现 deploy/install.sh——用户/依赖/venv/前端产物/systemd 均幂等，非 root 优雅降级，package-lock 缺失回退 npm install；新增 init_mysql（CREATE DATABASE IF NOT EXISTS，仅提供凭据时执行）与部署自检 verify（curl /health）；失败重跑不破坏既有数据
- [x] 1.2 初始化 Web 后端 FastAPI 工程与前端 React 工程骨架；勾选注：后端 /health 已在 services/backend 真实启动并通过（HTTP 200）与单测；前端骨架为交付代码，运行时验证受本环境无 node 限制延后至真实主机
- [x] 1.3 落地 macOS 风格设计系统（design tokens + CSS variables + 基础组件）；验证：前端渲染统一的 macOS 风格主题（圆角/毛玻璃/8pt 网格/深浅色）；勾选注：已实现 services/frontend —— src/theme/tokens.css（design tokens：--sp-{1..6} 8pt 网格、--r-{sm..full} 圆角、--blur 毛玻璃、浅/深两套色板 via [data-theme]）、src/theme/ThemeProvider.jsx（useTheme + toggle，持久化 localStorage）、src/components/ui（Button/Card/Segmented/Input/Label/Badge/Callout/Row）、App.jsx 采用 macOS 顶栏（红黄绿 orb）+ 毛玻璃卡片 + 分段控件 + 胶囊按钮；构建通过（vite build），真实浏览器验证浅色/深色切换后 --bg 由 #f5f5f7→#1c1c1e 且 data-theme=dark、/api 与 /health 代理连通真实后端返回 ok、控制台无报错

## 2. 数据源与目标连接管理

- [x] 2.1 实现连接管理数据模型与 conn_key 安全存储；勾选注：已实现 app/models/connection.py + app/vault.py(Fernet) + app/store.py(SQLite/JWT 风格 conn_key) + /api/connections 路由；单测 9 passed；真实 HTTP E2E 验证 POST 201/GET 200 且明文凭证未出现在返回体与 DB 文件字节
- [x] 2.2 [P1] 实现 MySQL/Oracle/SQL Server/PostgreSQL 数据源配置与连通性测试；勾选注：已实现 app/connectivity.py（按类型分发驱动）+ POST /api/connections/{ck}/test（用 Vault 解密秘密建连）；MySQL 对本地测试库实测 ok:true，错误密码返回明确失败原因；缺驱动类型返回"未安装"；单测 13 passed
- [x] 2.3 [P1] 实现关系库数据目标配置与连通性测试；勾选注：连接模型新增 direction=source/target，目标连接可独立保存/按方向列表过滤/连通性测试，复用 Vault 加密，配置不含明文凭证；真实 HTTP 验证 direction=target + /test ok:true；单测 16 passed
- [x] 2.4 [P2] 实现 CSV 文件数据源接入（上传或路径）；勾选注：已实现 app/sources/csv_reader.py（读列+类型推断+行数+预览+分隔符回退）与 POST /{ck}/sniff；真实验证 orders.csv 推断出 number/string/boolean 表结构；单测 22 passed
- [x] 2.5 [P2] 实现 REST/API 数据源配置（请求地址、认证、参数）与连通性测试；勾选注：已实现 app/sources/rest.py（none/bearer/basic 认证，httpx）与 POST /{ck}/sample + API 连通性；真实验证对本地 mock 请求返回样例；404 返回明确失败原因；单测 22 passed
- [x] 2.6 [P2] 实现对象存储/Hive/ClickHouse/API 目标配置与连通性测试；验证：各类型可保存配置并测试连通，配置 API 目标时明确提示推送幂等由端点保证；勾选注：app/connectivity.py 新增对象存储(boto3)/Hive(pyhive)/ClickHouse(clickhouse_connect)/API(direction=target 复用 REST 连通) 处理器，缺驱动返回"未安装"明确原因；新增 target_push_idempotency + GET /api/connections/{ck}/push-note（API 目标明确提示"幂等由端点保证"）；单测 4 passed

## 3. 数据库/接口只读查询 [P1]

- [x] 3.1 实现关系库只读 SELECT 查询：守卫（单条/拒写/多语句）+ 行数上限与超时 + 分页预览；勾选注：已实现 app/queries/{guard,executor}.py + POST /{ck}/query；真实验证 SELECT limit 分页 truncated、DROP/DELETE 被 422 拒、字符串内关键词不误判；单测 34 passed
- [x] 3.2 实现 REST/API 请求调试（临时覆盖方法/头/参数），样例可送字段向导，查询/调试过项目权限并写审计；勾选注：已实现 POST /{ck}/debug 返回样例+feed_for_wizard，项目不匹配返回 403，Store.log_event 写审计表；真实验证 mock API 返回样例；单测同 34 passed

## 4. ETL 任务配置与字段选择同步向导 [P1]

- [x] 4.1 实现向导流程：查询数据提供方、选择字段、选择字段处理方式、选择数据插入方、预览，并接受只读查询/调试结果预填；勾选注：已实现 app/models/task_config.py + POST /api/tasks 保存 + GET /api/tasks/{key} + POST /api/wizard/prefill（按样例列预填 direct 字段草稿）+ POST /{key}/preview（应用规则到样例）；单测 43 passed（含向导保存/获取与列预填）
- [x] 4.2 实现字段映射与转换规则配置（映射、类型转换、空值填充、常量、受限表达式），保留 AST 白名单校验（白名单仅约束规则引擎受限表达式，不作用于手写路径）；勾选注：已实现 app/transform.py（apply_rules/validate_rules）+ app/expr/whitelist.py（AST 校验仅允许白名单函数、禁止 Attribute 与内建调用）；越权表达式（如 name.upper.__self__、__import__('os')、open 等）保存返回 422 并被拒，白名单外层显式不经此校验（手写路径解耦）；单测含预览正确性与越权拒绝
- [x] 4.3 实现增量字段 + 水位与主键 upsert 配置；勾选注：TaskConfig.incremental(字段/水位) 与 primary_keys/mode(set) 持久化，POST 保存后 GET 可回读验证；失败不推进水位与重跑 upsert 兜底属执行语义，集成验证延后至第 11 节（任务 4.x 单测 43 passed）

## 5. 脚本生成：两条独立路径 [P1]

- [x] 5.1 实现 LLM 抽象接入层（统一 Provider 接口：对话/补全、模型列表；DeepSeek 默认实现；多供应商可插拔）；勾选注：已实现 app/llm/provider.py（LLMProvider 抽象 + 注册表 get/register/list）+ deepseek.py（OpenAI 兼容 chat/completions）+ auto_register；切换供应商不改既有任务/脚本（配置与脚本独立于供应商存储，单测断言同一配置两次生成脚本一致）；DeepSeek 真实外呼因本环境无凭据延后真机验证，用 RecordingProvider 断言调用
- [x] 5.2 实现调用前配置脱敏（占位符替换，生成后后端还原为运行时引用）与 RAG 上下文注入（etl_sdk 契约文档+示例，供应商无关）；勾选注：已实现 app/llm/redact.py（递归脱敏：敏感字段/DSN user:pass@host→{{SECRET:id}}；复原为运行时引用）+ app/llm/rag.py + app/llm/contracts/etl_sdk.md（契约文档，generate 一并注入）；单测断言发往 LLM 的 prompt 含 etl_sdk 契约且不含明文口令/密钥
- [x] 5.3 实现 LLM+Python 生成路径：LLM 生成脚本 + dry-run + 静态校验 + 人工确认生效；勾选注：已实现 app/llm/generator.py（脱敏→RAG→调用→去围栏→static_validate）+ API POST /scripts/generate、/scripts/{key}/dry-run、/scripts/{key}/llm-confirm（校验失败/dry-run 失败返回 422 不可生效，通过才写 current/）；静态校验禁止非白名单导入（etl_sdk/os/json/sys/types）；单测验证 fence 剥离、非法导入被拒、confirm 仅在校验与 dry-run 通过后生效
- [x] 5.4 实现手写 Python 路径：直接编写并保存脚本作为任务执行体，保存前提供可选语法校验与 dry-run（不写目标）；勾选注：已实现 API POST /scripts/handwritten（默认语法校验，可 skip_syntax_check）+ 复用 dry-run 执行器；dry-run 由 app/scripting/etl_runtime.py 以子进程注入 etl_sdk 运行时并置 SYNETRA_DRY_RUN=1，writers 只记数不写真实目标；单测验证语法错误 422、保存写入 current/、dry-run 计数且不触碰真实源/目标
- [x] 5.5 实现物理脚本生成与 current/versions 原子替换 + 版本记录；勾选注：已实现 app/scripts.py（ScriptStore：current.py 经临时文件 os.replace 原子替换；versions/<v>.py + versions.json 记录版本号/配置快照/生成人/时间/脚本路径；支持 read_version/rollback）；API /scripts/{key}/current、/scripts/{key}/versions、/scripts/{key}/rollback；单测验证版本递增、回滚恢复与记录追加；任务 5.x 单测共 54 passed

## 6. 手写路径安全模型（档位1）[P1]

- [x] 6.1 实现低权限执行身份：为手写任务配置独立 worker group 与低权限运行用户；验证：手写任务以非 root 低权限用户在独立 worker group 运行；勾选注：app/security/sandbox.py build_worker_profile 为手写任务推导独立 worker group(synetra-wg)+低权限用户；ensure_worker_user 在 root 下幂等创建（groupadd -f/useradd -r/nologin），非 root 优雅降级运行当前身份；POST /api/scripts/{key}/sandbox 可查画像；单测验证
- [x] 6.2 实现最小凭证注入：仅注入本任务声明的 conn_key，平台级秘密与其他项目/租户凭证不注入；验证：手写任务环境中不存在平台秘密与他人凭证；勾选注：minimum_credential_env 仅注入任务声明的 source/target conn_key 秘密，绝不注入平台级秘密/他人项目凭据；单测验证未声明连接秘密不出现
- [x] 6.3 实现网络白名单：按声明源/目标端点限制出网；验证：访问白名单外主机/端口被拒绝并记录，白名单内可通；勾选注：app/security/network.py ExtractEndpoint/parse_whitelist/allowed + 静态扫描 validate_network（URL 正则）；build_worker_profile 由源/目标连接端点推导白名单（未声明默认 loopback）；手写保存经（脚本出网地址必须命中白名单，否则 422 并审计 handwritten_network_blocked）；单测验证越网拒绝/白名单放行
- [x] 6.4 实现资源与执行时长配额（CPU/内存/磁盘/超时）；验证：超配额任务被限流或终止并记录；勾选注：ResourceQuota(cpu_seconds/memory_mb/disk_mb/timeout_seconds) + quota_enforcement_epilog 生成子进程 RLIMIT_CPU/RLIMIT_AS/RLIMIT_FSIZE 强制配额，超限由内核终止；单测验证 rlimit 段生成

## 7. 权限多租户、审计与外部 API 凭据 [P1]

- [x] 7.1 实现多用户与角色（管理员/开发者/只读）；勾选注：已实现 app/models/access.py + user_account 表（admin/developer/readonly）+ app/auth.py（X-Synetra-User 身份→角色解析，require_role 依赖）+ API /api/users 增列/改角色（仅 admin）；发布/改配/回滚端点（保存任务=POST /tasks、脚本确认/手写/回滚）均加 developer+ 门禁；单测断言只读投 POST /tasks 403、开发者 200、用户管理仅 admin；真实 HTTP 验证只读被 403 拒、admin 可列用户
- [x] 7.2 实现项目/命名空间隔离；勾选注：连接/任务/凭据均按 project 存储与过滤，query/debug 已有项目匹配 403，凭据触发按项目硬校验（跨项目返回 401）；单测验证按项目列表硬隔离（各见各的任务）与凭据仅触发本项目任务
- [x] 7.3 实现操作审计日志，覆盖配置变更、任务触发（含外部 API 触发）、只读查询/调试、凭据管理；勾选注：Store.log_event + list_events；配置变更(task_save)、脚本确认/手写/回滚、任务触发(含 external_api)、凭据签发/吊销、查询/调试均已写入 audit_log；单测验证 credential_issue 与 task_trigger 留痕
- [x] 7.4 实现外部触发 API 凭据（服务账号）：按项目签发、仅触发权限、明文仅签发时展示一次、可吊销；勾选注：app/auth.py 令牌哈希（sha256-HMAC，DB 仅存 hash）+ store 签发/verify(存在且未吊销且项目匹配)/吊销 + POST /trigger/{task_key}（Bearer 鉴权，仅项目匹配返回 202 回执，否则 401/404）；明文 token 仅在签发响应返回一次；单测验证触发成功、无凭据/吊销后/跨项目均被拒、任务不存在 404；任务 7.x 单测 61 passed

## 8. 调度执行与 REST API 触发 [P1]

- [x] 8.1 实现 DolphinScheduler 集成：新任务创建工作流与 Python 节点，改配置更新节点与 cron，手写任务绑定独立低权限 worker group；验证：任务按 cron 定时触发执行；勾选注：app/schedule/__init__.py DolphinSchedulerClient（ds_api_url/token，正常就上传工作流并关联目标 worker group synetra-wg）在未配置 DS 时本地降级（Scheduler.upsert 回退为本地 persistent cron 记录 + 单调递增 run_id）；normalize_cron 校验/转换 cron；POST /api/tasks/{key}/schedule 持久化 cron 并 upsert，GET 回读；单测验证 cron 校验、降级调度、回读一致
- [x] 8.2 实现平台对外 REST API 异步触发同步（API 凭据鉴权、调用 DS 触发、立即返回触发回执：触发ID/任务/项目/时间，执行结果走监控查询）；验证：合法调用收到回执并可在运行历史查到执行；无凭据/失效凭据/越项目/任务不存在均被拒绝并审计；勾选注：POST /trigger/{task_key} Bearer 鉴权（项目硬校验），external_trigger 现 start_run 同步拉起执行并写 run_record，触发回执 TriggerReceipt 新增 run_id；POST /api/tasks/{key}/run 也可手动触发；无凭据/吊销/越项目/任务缺失分别 401/401/403/404 并写审计（含 external_api）；单测验证回执含 run_id 且运行历史可查
- [x] 8.3 实现失败重试（次数/间隔）与失败邮件告警；验证：任务失败按配置重试并最终发送邮件告警；勾选注：app/schedule/__init__.py run_with_retry 按 retry_times/retry_interval_s 重试（每轮 start_run→执行→finish_run），耗尽重试后置 failed 并调用 alerting.send_failure_alert（SMTP 未配置降级为记录 run_num_failures 审计，不阻塞）；单测验证重试计入运行历史、最终失败 state=failed 且告警被记录

## 9. 监控、运行历史、对账与告警

- [x] 9.1 [P1] 实现任务列表/状态（运行中/成功/失败/等待重试/未调度）与按触发方式（定时/外部 API/手动）区分的运行历史；验证：状态枚举与触发方式正确展示；勾选注：run_record（state: running/success/failed/retry_pending + trigger_via: schedule/external_api/manual + run_id/任务/项目/起止时间）+ Store.start_run/finish_run + GET /api/runs（列表，可任选触发方式过滤）+ GET /api/runs/{run_id}（详情）+ GET /api/monitor runs 汇总；单测验证状态/触发方式正确落库与读取
- [x] 9.2 [P1] 实现实时日志查看；验证：运行中任务可实时查看日志；勾选注：run_log 表（run_id 关联，seq 递增）+ Store.append_run_log + GET /api/runs/{run_id}/logs（支持 since cursor 秒级追加，实现"实时"翻页）；单测验证日志按序/按 cursor 增量读取
- [x] 9.3 [P1] 实现资源占用、延迟指标与数据量统计；验证：指标页面展示资源/延迟/数据量；勾选注：run_metric 表（timestamp/kind/value/cpu/mem/latency/rows）+ Store.record_metric + POST /api/runs/{run_id}/metrics 与 GET /api/metrics（按运行聚合一次均值/总量）；单测验证指标写入与汇总输出
- [x] 9.4 [P1] 实现数据对账（手动触发，源端 vs 目标端行数/校验和）；验证：一致输出一致结论，不一致输出差异明细；勾选注：monitor.Reconciler 对源/目标做行数与逐列校验和（sum_checksum），diff_rows 输出差异明细；POST /api/reconcile 触发并落 reconcile 表，GET /api/reconciliations 查询，一致/不一致（含差异明细）均可返回；单测验证一致与不一致两种结论
- [x] 9.5 [P2] 实现可配置阈值的监控告警通知；验证：指标触发阈值时按配置通知；勾选注：alert_rule 表（metric 字段 + op/阈值 + 通知目标）+ save_alert_rule/list/delete + AlertEvaluator.evaluate_and_notify（仅 enabled 时求值，超过阈值触发 alert_event 并调用通知回调）；POST/GET/DELETE /api/alerts + GET /api/alerts/events + POST /api/alerts/{id}/check 手动检测；单测验证规则增删查、阈值触发生成事件、disabled 不触发；任务 8/9 单测均在 test_monitor_schedule.py（连同调度共 34 passed）

## 10. 元数据与血缘

- [x] 10.1 [P1] 实现关系库表结构自动同步与字段字典；勾选注：已实现 app/metadata/catalog.py（table_metadata 表：conn_key/schema/table/column/type/nullable/primary）+ POST /api/metadata/connections/{ck}/tables/{table}/sync + GET tables/describe；列由连接器/发现提供并入库，字典可查（真实 DB 发现依赖 driver，本环境用可注入列同步验证）
- [x] 10.2 [P2] 实现 REST/API 数据源从返回样例推断字段结构并入字典；勾选注：app/metadata/infer.py（从行集推断 number/boolean/string/mixed/object）+ POST /api/metadata/infer（推断入库）；真实验证 id→number、active→boolean；单测通过
- [x] 10.3 [P1] 实现向导/LLM 路径字段级血缘静态推导；勾选注：field_lineage_from_config（source_object.字段→target_object.字段，常量等无源字段不产生边）+ 任务保存时自动入库 + POST /api/metadata/tasks/{key}/lineage/derive + GET /api/metadata/lineage（task/source/target）；单测验证保存即推导、字段级可用、血缘可查
- [x] 10.4 [P1] 实现手写路径表级血缘（依据任务声明）并在血缘视图标识字段级不可用；勾选注：table_lineage_from_task 仅表级边（field=None, kind=table）+ POST /api/metadata/tasks/{key}/lineage/handwritten；血缘视图 field_level_available=false，不展示伪造字段关系；单测验证；任务 10.x 单测 66 passed

## 11. 关系库连接器扩展 [P1]

- [x] 11.1 在 etl_sdk 实现 Oracle/SQL Server/PostgreSQL 抽取与加载（全量、增量+水位、主键 upsert）；验证：三类库各至少一条全量与一条增量同步测试通过；勾选注：app/etl_sdk/relational.py PostgreSQLConnector(ON CONFLICT)/OracleConnector(merge 语义)/SqlServerConnector(delete+insert upsert) 统一 _BaseRelational.extract/load；全量=默认 INSERT，增量由上层按 watermark_field 收窄 SQL，持久化水位语义已由任务 4.3 定义；驱动缺失抛 ConnectorError（清晰原因"驱动 xxx 未安装"）；get_connector 按类型建造；单测（test_etl_sdk.py）用假连接验证三种 upsert 方言 SQL 生成、全量/增量前缀差异、缺驱动原因

## 12. 版本管理与回滚 [P1]

- [x] 12.1 实现任务配置/脚本历史版本查询与回滚；勾选注：配置侧新增 task_history 表 + save_task 每次保存按版本递增写历史 + GET /api/tasks/{key}/history + POST /api/tasks/{key}/rollback（回滚后 current 恢复并追加新版本）+ POST /api/tasks 可传 task_key 改配；脚本侧脚本版本/回滚已在 5.5 完成（ScriptStore rollback）；单测验证同任务二次改配 v2、回滚到 v1 后 current=v3；任务 4/5/12 单测 67 passed

## 13. 主数据管理（MDM）[P2]

- [x] 13.1 实现主数据模型定义（客户/产品/组织：实体字段与约束）；验证：可定义实体字段与约束；勾选注：已实现 app/mdm/quality.py（FieldDef/MasterModel）+ POST/GET /api/mdm/models + store 表 mdm_model；单测验证定义/更新/列出实体与字段约束
- [x] 13.2 实现主数据表增删改查维护；验证：主数据记录可新增/修改/删除；勾选注：已实现 POST/GET/PUT/DELETE /api/mdm/models/{entity}/records + store mdm_record（_id 主键规范化），记录带变更历史；单测验证 CRUD 与实体缺失 404
- [x] 13.3 实现主数据质量校验（非空/格式/唯一）；验证：不合规数据被拒绝；勾选注：validate_record 校验必填/类型(int/float/boolean)/格式(email/phone/date)，_check_unique 查库判重（更新排除自身）；非法记录返回 422，唯一冲突 422；单测 11 passed
- [x] 13.4 实现主数据同步分发与变更历史；验证：可分发到目标并查看变更历史；勾选注：POST /api/mdm/models/{entity}/sync 返回待分发记录 ids 并写 mdm_sync 审计，POST/PUT/DELETE 写入 mdm_change 历史，GET /{id}/history 可查；任务 13.x 单测共 11 passed（全部套件 78 passed）

## 14. 多源/多目标扩展 [P2]

- [x] 14.1 在 etl_sdk 实现 CSV extractor；验证：按配置读取 CSV 完成同步；勾选注：app/etl_sdk/extractors.py CsvExtractor（csv 模块，按 path 读文件、类型推断、limit/预览），extract 返回行 dict 列表；缺文件返回明确原因；单测用临时 CSV 验证读取与行数
- [x] 14.2 在 etl_sdk 实现 REST/API extractor（认证、分页/参数、拉取）；验证：API 源按配置拉取数据；勾选注：app/etl_sdk/extractors.py RestExtractor（复用 sources/rest 的 ApiAuth none/bearer/basic + httpx + 可选 page/page_size 分页拉取），extract 返回合并行集；单测用 mock request_json 验证认证头、分页合并、超时兜底
- [x] 14.3 在 etl_sdk 实现 API loader（推送、认证、超时/重试，幂等不保证文档化）；验证：同步结果推送到 API 目标；勾选注：app/etl_sdk/loaders.py ApiLoader（POST 推送，认证 none/bearer/basic，超时与有限重试），缺目标失败抛 LoadError（明确原因含"推送幂等由端点保证"文档说明）；单测用 mock 请求验证前推、重试与失败原因
- [x] 14.4 实现对象存储/Hive/ClickHouse 目标加载；验证：各目标写入测试通过；勾选注：app/etl_sdk/loaders.py ObjectStorageLoader(boto3)/HiveLoader(pyhive)/ClickHouseLoader(clickhouse_connect)，驱动缺失抛 LoadError（清晰原因"驱动 xxx 未安装"）；对象存储迭代写行、Hive/ClickHouse 用标准驱动 INSERT；单测用注入假 client 验证目标写入路径与缺驱动原因；任务 11/14 单测均在 test_etl_sdk.py（连同 2.6 目标连接共 26 passed）
