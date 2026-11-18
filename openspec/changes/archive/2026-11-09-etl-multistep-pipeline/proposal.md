# Proposal

## Why

当前 ETL 任务的契约只允许「一个源 → 一次行级列投影 → 一个目标」：`TaskConfig` 只有一个 `source_conn_key` / `target_conn_key`，转换能力被压缩为 `fields[]` 的五类字段规则（`app/models/task_config.py:21-39`），执行体在生成的脚本里只跑一遍 `_transform` 就写入（`app/scripting/gen_config.py:99-131`），而 SDK 侧为多步转换预留的 `transforms.apply` 至今是空实现（`app/scripting/etl_runtime.py:50-54`）。

真实的数据清洗几乎总是多趟工序（`去空格 → 类型归一 → 码值映射 → 过滤 → 去重 → 聚合 → 落多目标`），一次列映射无法表达。用户今天的绕路只有三条：堆一条超长受限表达式（不可读、不可测）、改走手写 Python（丢掉向导/血缘/预览/LLM 全套能力）、拆成多个任务串中间表（污染数仓、血缘断裂、任务间无依赖）。对照 Kettle 的 Transformation（steps + hops 构成的 DAG、每步可预览、步级指标、错误行旁路），本项目缺的不是几个转换函数，而是「步骤图」这一层抽象。

同时，梳理执行链路时发现三处会让任何优化落空的硬伤：定时执行走的是 `dry_run_script`（`app/schedule/__init__.py:155-162`），定时任务只记数不写目标；增量水位依赖的 `SYNETRA_WATERMARK` 从未被执行器注入、成功后也未推进，配置增量实际等于每次全量；抽取与加载全量入内存并逐行 INSERT（`app/etl_sdk/relational.py:123-139`），大表会 OOM。这些必须与本变更同批修掉，否则多步 pipeline 只会把问题放大。

## What Changes

### P0 执行链路修正（先恢复正确性）

- 定时触发与手动触发统一走真实执行通道，运行记录与写入行数为真实值；不再以 dry-run 结果冒充成功。
- 增量水位持久化到任务运行状态：执行前注入水位、成功加载后推进、失败保持原水位；首跑策略（全量/仅增量）真实生效。
- 抽取与写入改为分批流式 + 批量提交（`batch_size` / `commit_size` 可配），避免全量入内存与逐行提交。

### P1 多步清洗流水线（本变更主体）

- 新增 `PipelineConfig`（节点 + 连线）作为任务配置的转换主体，支持 DAG 编排与多源合并；**不引入跨源 join**，多源通过 `union` 按列名对齐合并。
- 新增步骤注册表与核心步骤集：源（表 / 只读 SQL / CSV / API）、`union` 合并、行级步骤（字段选择与改名、行过滤、类型转换、受限表达式、码值映射、空值填充、字符串处理、常量、序列号）、整批步骤（排序、去重、分组聚合）、目标（关系库 append/upsert、CSV 文件）。
- 新增拖拽画布作为任务编排界面：节点拖拽与连线、连线合法性校验（无环、源仅出边、目标仅入边、`union` ≥ 2 入边）、节点配置抽屉复用既有字段规则 UI、节点级数据预览。
- 执行模型改为「配置解释执行」：`etl_sdk` 提供 pipeline 解释器，生成的脚本退化为 `run_pipeline(SPEC)` 薄壳，仍保留 current/versions 版本化与 dry-run 契约。**BREAKING**：取代「把转换逻辑内联渲染进生成脚本」的旧决策（`app/scripting/gen_config.py`）；已存在的 historical 脚本与手写脚本继续可执行，不强制迁移。
- **BREAKING**（配置形态）：`TaskConfig.fields` 降级为兼容语法糖 —— 保存时自动编译为 `source → transform → target` 三节点 pipeline；存量任务零迁移、行为不变，但新的能力不再通过 `fields` 表达。
- 多源合并：一个任务可声明 N 个源节点，经 `union` 合并为单行流后继续清洗，解决「多源数据合并或处理」而无需落中间表。
- 步级可观测：每次运行记录每步输入/输出/拒绝行数与耗时，画布节点上直接展示；失败时自动把该步样本快照落到本地运行目录（不写目标库、不建 staging 表）。
- 节点级血缘：按 pipeline 节点静态推导字段级血缘，多步链路可追溯到每一步的字段来源与去向。

### 明确不在本变更范围（后续变更）

- 跨源 join / lookup 关联（本变更不做，`value_map` 仅支持静态码表映射）。
- 单任务多目标扇出与错误行旁路（error hop）。
- 任务间依赖 DAG 与条件 hop（Kettle Job 式编排）。
- 从中间步骤断点续跑。

## Capabilities

### New Capabilities

- `etl-pipeline`: 多步清洗流水线的契约与执行引擎 —— PipelineConfig 结构（节点/连线）、步骤注册表与核心步骤语义、批次流式执行与内存溢出策略、多源 `union` 列对齐规则、`run_pipeline` 解释执行与 dry-run 步级报告、旧 `fields` 到 pipeline 的编译等价性。

### Modified Capabilities

- `etl-authoring`: 任务编排从「字段规则表单」升级为「拖拽画布 + 节点配置」；新增画布交互与连线校验、节点级预览、多源合并配置；既有字段规则成为 transform 节点的内部配置；向导/LLM 路径产出的配置需与 pipeline 契约对齐。
- `scheduling-execution`: 定时触发与手动触发共用真实执行通道；增量水位在执行前注入、成功后推进、失败不推进；分批与批量提交参数作为执行语义的一部分。
- `monitoring-reconciliation`: 运行详情新增步级指标（每步 in/out/rejected/耗时）与步级失败定位；画布节点展示步级徽标；步级样本快照的落盘位置与保留策略。
- `metadata-lineage`: 字段级血缘由「任务级单跳」升级为「按 pipeline 节点逐跳推导」，多步与多源合并链路可追溯；兼容旧 tasks 的三节点编译结果。

## Impact

- **后端模型**：`app/models/task_config.py`（引入 pipeline 字段与编译入口）、新增 `app/models/pipeline.py`；`store.py` 增加水位状态、运行步级指标表与配置版本兼容。
- **执行内核**：新增 `app/etl_sdk/pipeline/`（解释器 + 步骤注册表 + 各步骤实现）；`app/scripting/gen_config.py` 改为渲染薄壳；`app/scripting/executor.py` 注入水位与批次参数；`app/etl_sdk/relational.py` 增加分批读取与批量提交（方言逻辑不变）。
- **调度**：`app/schedule/__init__.py` 的 `_default_run` 改用真实执行通道；`app/api/schedule.py` 与 `app/api/tasks.py` 对齐。
- **API**：`POST /api/tasks` 接受 pipeline 配置并在缺省时编译 `fields`；新增节点级预览端点（按节点执行到该点并返回样本与指标）。
- **元数据**：`app/metadata/catalog.py` 的字段血缘推导改为按节点遍历。
- **前端**：`pages/Tasks.jsx` 任务表单改为画布入口；新增画布组件（建议引入 `@xyflow/react` 作画布内核，节点与连线用现有设计 token 自绘）、节点配置抽屉、节点徽标与预览。
- **依赖**：前端新增 `@xyflow/react`；后端无新增外部依赖（解释器基于标准库）。
- **兼容性**：存量 `TaskConfig`（含 `fields`）可直接加载与运行；已生成的 historical 脚本、手写脚本不受影响。
- **测试**：新增 pipeline 契约/步骤语义/编译等价性/水位推进/分批提交的单元与集成测试；既有 `etl-authoring`、`scheduling-execution` 相关测试需保持通过。
