# Design: 只读查询工作台重设计

## Context

现有只读查询页（[Query.jsx](file:///d:/Projects/synetra/services/frontend/src/pages/Query.jsx)）为单列结构：一个 `Segmented`（关系库 / REST/API）、一个连接下拉、一个 SQL `textarea`、一个结果 `Table`。页面本身不提供任何「库里有哪些表」的信息，用户必须离开页面自行查找表名。

后端现状：
- 查询执行已完整：`POST /connections/{conn_key}/query`（[query.py](file:///d:/Projects/synetra/services/backend/app/api/query.py#L62-L87)）→ `run_select`（[executor.py](file:///d:/Projects/synetra/services/backend/app/queries/executor.py#L32-L57)）→ 只读守卫（[guard.py](file:///d:/Projects/synetra/services/backend/app/queries/guard.py)），连接参数从 `conn.meta` 读 `host/port/db/user`，密码从 `store.get_secret(conn_key)` 取。
- 已有「列连接」`GET /connections`（[connections.py](file:///d:/Projects/synetra/services/backend/app/api/connections.py#L37)）与「列表」`GET /metadata/connections/{conn_key}/tables`（[metadata.py](file:///d:/Projects/synetra/services/backend/app/api/metadata.py#L82-L84)）。
- **关键约束**：`/metadata/.../tables` 走的是本地目录库 `Catalog.list_tables`（[catalog.py](file:///d:/Projects/synetra/services/backend/app/metadata/catalog.py#L66)），读的是已同步进 `synetra.db` 的 `table_metadata`，**不是**实时查目标库；未同步或已过期的连接会返回空或陈旧列表。
- 连接模型 `meta` 为 `dict[str,str]`（[connection.py](file:///d:/Projects/synetra/services/backend/app/models/connection.py#L34-L47)）：关系库使用 `host/port/db/user`，MySQL 无独立 schema 概念、PostgreSQL 有 schema。
- 只读查询仅支持 `MYSQL` / `POSTGRESQL`（[query.py](file:///d:/Projects/synetra/services/backend/app/api/query.py#L67)）。

参照目标（用户提供的桌面客户端截图）：左列上=连接选择，左列下=表/视图列表（含检索框与类型过滤）；右列上=SQL 编辑，右列下=数据预览；点击表名把基础 SQL 送入编辑器。

## Goals / Non-Goals

**Goals:**

- 查询页形成「选连接 → 看库表 → 点表取 SQL → 执行 → 看结果」的一屏闭环。
- 库表/视图导航数据来自目标库实时枚举，未同步的本地字典不再成为导航前置条件。
- 点击表名生成「可直接执行」的基础只读查询语句（方言正确的标识符引用 + schema 限定 + 行数上限），且不自动执行。
- 查询执行契约、只读守卫、权限校验与审计语义零变化。

**Non-Goals:**

- SQL 自动补全、语法高亮、`${param}` 模板参数表。
- 结果导出/持久化。
- 支持 MySQL / PostgreSQL 之外的关系库（Oracle、SQL Server、Hive、ClickHouse）的查询与枚举。
- 本地元数据字典（`table_metadata`）与血缘能力的改造；实时枚举不写入该字典。

## Decisions

### 决策 1：四区布局用「左右两栏 + 各自上下分区」实现，左列固定宽度

**决策**：查询页改为 CSS 栅格两栏：左列（宽约 260–300px）上为连接 `Select`、下为表/视图面板（检索框 + 类型切换 + 可滚动列表）；右列上为 SQL 编辑卡片（编辑器 + 执行按钮）、下为结果卡片。REST/API 模式仍居右列上方，复用既有 `Segmented` 与 `/debug`。

**理由**：这是数据工程师最熟悉的数据库客户端心智模型；表列表与结果同时可见，点表→执行→看结果无需切换视图。macOS 设计语言下的卡片（`Card solid`）+ `mac-list__item`（现有样式，[ui.css](file:///d:/Projects/synetra/services/frontend/src/components/ui/ui.css#L183-L185)）已足以承载列表。

**被否决的方案**：把表列表放进 `Modal` / 抽屉，主区仍保留单列。否决理由：模态会遮住 SQL 编辑器与结果区，用户每次取表名都要开关弹窗，反而破坏了本次要建立的闭环。

### 决策 2：库表枚举走**实时系统目录**新接口，不复用本地字典接口

**决策**：新增实时枚举能力，直接查目标库系统目录（MySQL `information_schema.TABLES` 按 `TABLE_SCHEMA = meta["db"]`；PostgreSQL `information_schema.tables` 按 `table_schema`，排除 `pg_catalog` / `information_schema`），返回 schema 清单与对象清单。**不修改** `GET /metadata/connections/{ck}/tables` 的语义。

**理由**：本地字典必须先经「同步表结构」流程写入，且可能过期；用它做导航会出现「库里有表但左列空」「表已删但左列还在」这两类误导。实时目录查询是轻量只读查询，成本可控。

**被否决的方案**：在既有 `/metadata/connections/{ck}/tables` 上加 `live=true` 参数复用同一路径。否决理由：同一 URL 承载「本地缓存字典」与「实时探库」两种语义，调用方无法预期；且该路由带 `require_role(DEVELOPER)` 与血缘职责，把实时探库混入会破坏模块边界。另一个被否决方案是回退到「查不到就让用户先去同步元数据」，理由同上（把导航前置条件强加给用户）。

### 决策 3：单个聚合端点 `GET /connections/{conn_key}/catalog`，一次返回 schemas + items

**决策**：响应形如
```json
{
  "ctype": "mysql",
  "default_schema": "ds1",
  "schemas": ["ds1"],
  "items": [
    { "schema": "ds1", "name": "dim_city", "kind": "table",
      "snippet": "SELECT * FROM `dim_city` LIMIT 100;" }
  ],
  "truncated": false
}
```
查询参数：`project`（权限校验，必需）、`schema`（可选，缺省用 `default_schema`）、`kind`（可选 `table|view|all`，默认 `all`）、`q`（可选关键字，服务端不下推也可由前端本地过滤）。

**理由**：一次往返即可点亮整个左列；`schemas` 与 `items` 同响应避免「先拿 schema 再拿表」的串行往返。`kind` 用枚举而非布尔，为后续物化视图/外部表留扩展位。

**被否决的方案**：拆成 `GET /schemas` 与 `GET /tables?schema=` 两个端点。否决理由：前端需串行编排两次请求才能渲染左列首屏，MySQL 单 schema 场景下纯属浪费往返；且 `GET /connections` 已返回连接列表，再引入两个导航端点会让接口面膨胀。

### 决策 4：枚举实现放在查询域（`app/queries/`），路由独立于 metadata

**决策**：新增 `app/queries/catalog.py`（`list_schemas` / `list_objects`，按 `ConnectionType` 分派 MySQL/PG 实现，复用与 `executor.py` 相同的 `meta` + `secret` 取参方式）与 `app/api/catalog.py` 路由，注册到 [app/api/__init__.py](file:///d:/Projects/synetra/services/backend/app/api/__init__.py)。权限校验与审计复用 `query.py` 中的 `_authorized` 形态（校验 `conn.project == project`，`log_event` 记 `action=query`、`detail.op=catalog`）。

**理由**：查询域已经掌握「连接 `meta` 取参 + `store.get_secret` 取密钥 + 驱动异常转可读」这套逻辑；枚举与查询同属「对目标库发起只读访问」，放在一起可共享错误处理与脱敏策略。

**被否决的方案**：塞进 `app/metadata/catalog.py`（该模块已有 `Catalog` 类）。否决理由：`Catalog` 是本地字典/血缘仓储，同名模块再承担「实时探库」会让「catalog 到底指本地还是远端」长期歧义；职责边界优先于文件数量。

### 决策 5：预填 SQL 语句由后端生成并随枚举项返回（`snippet`），前端不做方言拼接

**决策**：后端在枚举时按连接 `ctype` 生成基础语句，两种方言**一律**做 schema 限定（避免「何时可省略前缀」形成第二套判断）：MySQL 用反引号引用（`` SELECT * FROM `db`.`table` LIMIT 100; ``，`db` 取枚举出的 `TABLE_SCHEMA`），PostgreSQL 用双引号引用（`SELECT * FROM "public"."orders" LIMIT 100;`）。前端点击列表项即把 `snippet` 替换写入编辑器。

**理由**：引用符与 schema 限定方式是方言细节，后端最清楚 `ctype`；由后端下发可保证「点表即得到可直接执行的语句」，前端无需重复实现引用规则，也避免大小写 / 保留字 / 特殊字符场景出错。

**被否决的方案**：前端用模板字符串拼接（`SELECT * FROM ${name} LIMIT 100;`）。否决理由：MySQL 需反引号、PG 需双引号，前端虽能从连接 `ctype` 分辨，但库前缀、schema 缺省、特殊字符转义等规则会形成第二套事实来源，与后端漂移风险高。

### 决策 6：查询执行与展示契约完全不变

**决策**：右列执行仍调用 `POST /connections/{conn_key}/query`，请求体 `{project, sql, limit, offset}`、响应 `columns/rows/count/truncated/limit/offset` 一字不改；结果渲染沿用「`Array.isArray(row)` 时按下标 `row[i]`，对象行按 `row[c]`」的既有归一化（[Query.jsx](file:///d:/Projects/synetra/services/frontend/src/pages/Query.jsx#L72-L73)）。

**理由**：`Tasks.jsx` / `ConversationTask.jsx` 等复用同一端点，改动契约会波及任务预览；本次只做界面与导航增强，不应引入回归面。

**被否决的方案**：借重设计之机把 `rows` 改为 `list[dict]`。否决理由：破坏既有契约与测试，收益仅是渲染端少一次类型判断。

## Risks / Trade-offs

- [目标库账号无 `information_schema` 读取权限，枚举失败] → 返回明确的失败原因并在左列列表区以 `Callout` 呈现（而非静默空列表）；错误信息沿用 executor 的「驱动异常转可读」策略，不得回显凭据。
- [超大库（数万对象）枚举慢、响应大] → 只选取名称/类型/schema 等轻量列，设结果上限（如 5000 条）并在超出时置 `truncated=true`，引导用户用关键字或 schema 收窄。
- [PG 多 schema 与 MySQL 单库的差异导致左列交互不一致] → 统一为「`schemas` 长度 > 1 才显示 schema 选择器」，单 schema 时隐藏控件，避免 MySQL 用户看到多余控件。
- [窄屏下两栏挤压] → 左列固定宽度 + 栅格换行，视口不足时退化为上下堆叠（表列表在上、SQL 与结果在下）。
- [枚举与查询各建一次目标库连接，切换连接时可能多次握手] → 枚举只在「切换连接 / 刷新 / schema 变更」时触发，不做轮询；每次执行完连接即关闭（沿用 executor 的 `finally: conn.close()`）。

## Migration Plan

- 无数据迁移：不新增表、不修改 `synetra.db` 结构与 `table_metadata` 内容。
- 无破坏性接口变更：既有端点全部保留原契约；新增端点纯增量，旧前端版本不受影响。
- 回滚策略：前端页面为单文件重写，回滚即还原 `Query.jsx`；后端新增模块与路由可直接移除（无持久化副作用）。

## Open Questions

- 左列列表项是否需要展示附加信息（列数、注释等）——参照图未体现，本次不做，后续可加。
- 枚举结果短时缓存已在 P2 中实现（30s TTL，手动刷新绕过缓存）；后续是否把 TTL 做成可配置项待定。
