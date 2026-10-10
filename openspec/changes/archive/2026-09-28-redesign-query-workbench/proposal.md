# Proposal: 只读查询工作台重设计（连接 / 库表视图 / SQL / 结果四区布局）

## Why

现有「只读查询 / 接口调试」页只有一个连接下拉 + 一个 SQL 文本框 + 结果表格：用户看不到连接里有哪些库、表和视图，必须切到连接管理或元数据页另找表名，再凭记忆手写 SQL。查询页因此无法承担「发现数据 → 试查 → 看结果」的日常闭环——这正是需要重设计的原因。

参照桌面数据库客户端（DBeaver / Navicat 式）的成熟布局重设计：左列管「连接与库表视图」，右列管「SQL 编辑与结果预览」，让数据工程师在一屏内完成选连接、看表、点表取 SQL、执行、看数据。

## What Changes

- **四区工作台布局（前端重设计）**：查询页改为左右分栏——左列上方为「数据连接选择器」，左列下方为「当前连接的表/视图列表」；右列上方为「SQL 编辑器 + 执行」，右列下方为「数据结果预览区」。REST/API 调试模式保留在右列上方（保持既有 Segmented 切换与 `/debug` 语义）。
- **关系库库表/视图实时枚举（后端新增接口）**：对已授权的关系库连接，实时查询目标库的 schema/表/视图清单（MySQL 走 `information_schema.TABLES`，PostgreSQL 走 `information_schema` 并过滤系统 schema），**不复用**本地已同步的元数据字典（`metadata` 目录库）——本地字典可能从未同步或已过期，无法作为导航依据。
- **点击表名预填 SQL**：点击左侧列表中的表/视图名，将基础只读查询语句（`SELECT * FROM <限定标识符> LIMIT 100;`）写入右侧 SQL 编辑器，按 schema 限定并按标识符安全引用；只预填、不自动执行，用户可改可加条件后再点执行。
- **列表检索与对象类型过滤**：左侧表列表提供关键字检索框，并提供「表 / 视图」类型切换（对齐参照图的搜索框与类型勾选），便于在数百张表中定位。
- **只读契约保持不变**：查询仍走既有 `POST /connections/{conn_key}/query`，单条 SELECT、拒写/拒多语句、行数上限（≤1000）与执行超时、项目权限校验与审计日志全部沿用，本次不改查询执行语义。

## Non-Goals（明确不做）

- 不做 SQL 自动补全、语法高亮、参数化模板（参照图中的 `\${abc}` 参数表）等编辑器增强能力。
- 不做结果导出/持久化（保持既有「查询结果不持久化、不提供导出」的边界）。
- 不新增数据库连接器类型（仍仅 MySQL / PostgreSQL 支持只读查询）；Oracle / SQL Server / Hive / ClickHouse 的查询执行与枚举不在本次范围。
- 不改动 `POST /connections/{conn_key}/query` 的请求/响应契约（`columns/rows/count/truncated/limit/offset`），避免破坏任务预览等既有消费方。
- 不做多连接/多结果集并排（多标签页查询窗口）。

## Capabilities

### New Capabilities

（无：本次为既有只读查询能力的界面与导航增强，落在既有能力域内。）

### Modified Capabilities

- `data-connections`: 新增「关系库库表与视图实时枚举」与「查询工作台布局与点击表名预填」两项 Requirement；既有「关系库只读查询」「只读查询展示真实数据」「查询审计与边界」需求语义不变（枚举与预填均为只读，仍受项目权限与审计约束）。

## Impact

- **前端**：`services/frontend/src/pages/Query.jsx` 重写为四区布局（新增左侧连接 + 库表视图面板，右列 SQL + 结果）；`services/frontend/src/api/client.js` 复用现有封装；`App.jsx` 中查询页注册与图标不变。
- **后端**：新增库表枚举接口（独立路由模块，或在 `app/api/metadata.py` 之外的查询域新增 `app/api/catalog.py` 并注册到 `app/api/__init__.py`）；查询执行域新增枚举函数（`app/queries/` 下，与 `executor.py` 同层，复用连接参数与密钥读取）；可复用 `app/queries/guard.py` 的只读语义仅作为查询执行约束，枚举走固定的 `information_schema` 查询。
- **接口契约**：新增 `GET` 枚举端点（请求带 `project` 做权限校验，返回 schema 列表与对象列表）；既有端点零破坏性变更。
- **权限与审计**：枚举与预填不得绕过项目权限校验；枚举操作计入审计（与查询留痕同一 `log_event` 通道）。
- **依赖**：不新增第三方依赖（`pymysql` / `psycopg` 已在用）。
- **测试**：新增枚举接口的单测（含 MySQL/PG 分支与权限拒绝），回归既有 `tests/test_query_endpoint.py`。
