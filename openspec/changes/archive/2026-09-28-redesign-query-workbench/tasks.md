# Tasks: 只读查询工作台重设计

依赖说明：第 1 组（后端枚举）为第 2、3 组的前置；第 3 组的实时联调依赖 1.6 完成，其余前端静态布局（3.1）可与之并行；第 4 组为端到端验收，依赖 1–3 全部完成。第 5 组为 P2，不阻塞本次闭环。

需求映射：第 1 组 ↔ `data-connections` 的「关系库库表与视图实时枚举」；第 3.1/3.2 组 ↔「查询工作台四区布局」；第 3.5 ↔「点击表名预填只读查询语句」；第 3.3/3.4 ↔「库表视图列表的检索与类型过滤」。

## 1. 后端：库表与视图实时枚举 [P1]

- [x] 1.1 新增 `services/backend/app/queries/catalog.py`：提供 `list_schemas(ctype, meta, secret)` 与 `list_objects(ctype, meta, secret, schema=None)`，按 `ConnectionType` 分派 MySQL / PostgreSQL，取参方式与 `executor.py` 一致（`meta` + `store.get_secret`），非关系库抛出可读的「不支持」异常。验证：对 MySQL 与 PG 各返回非空 schema 与对象清单；对 API/CSV 类型调用抛出明确异常。
- [x] 1.2 MySQL 枚举实现：查 `information_schema.TABLES`（`TABLE_SCHEMA = meta["db"]`），将 `TABLE_TYPE` 映射为 `table`（BASE TABLE）与 `view`（VIEW），`schemas` 返回目标库。验证：对同时含表与视图的 MySQL 库枚举，两类对象均返回且 `kind` 标注正确。
- [x] 1.3 PostgreSQL 枚举实现：查 `information_schema.tables`，`schemas` 仅返回用户 schema（排除 `pg_catalog`、`information_schema`），对象按 `table_schema` 过滤。验证：枚举结果不含系统 schema 对象；`schemas` 不含系统 schema。
- [x] 1.4 由后端生成基础查询语句 `snippet`：MySQL 用反引号引用、PostgreSQL 用双引号引用，两种方言均按 schema 限定（`库名.表名` / `schema.表名`），统一带 `LIMIT 100`（实现取「一律限定」，以免「是否省略前缀」出现第二套判断）。验证：MySQL 连接点击表得到 `` SELECT * FROM `synetra_test`.`orders` LIMIT 100; ``；PG 得到 `SELECT * FROM "public"."orders" LIMIT 100;`；含空格/大写/引号字符的标识符按方言转义后提交到 `POST /connections/{ck}/query` 可直接执行成功。
- [x] 1.5 枚举结果设上限（5000 条），超出时截断并返回 `truncated=true`。验证：构造超过上限的对象集后返回条数等于上限且 `truncated=true`。
- [x] 1.6 新增 `services/backend/app/api/catalog.py` 路由 `GET /connections/{conn_key}/catalog`：参数 `project`（必需）、`schema`（可选）、`kind`（`all|table|view`，默认 `all`）、`q`（可选关键字）；复用连接存在性校验与项目权限校验（`conn.project != project` 返回 403），成功时 `log_event(action="query", detail={"op": "catalog", ...})`；在 `services/backend/app/api/__init__.py` 注册。验证：越权 `project` 返回 403 且响应不含任何对象；成功调用后审计日志出现 `op=catalog` 记录；响应含 `ctype/default_schema/schemas/items/truncated` 字段。

## 2. 后端：测试与回归 [P1]（依赖 1）

- [x] 2.1 新增枚举接口单测：覆盖 MySQL 分支、PG 分支、`schema` 与 `kind` 过滤、越权 403、非关系库 400、`snippet` 方言正确性。验证：`pytest` 全绿，且上述分支均有对应用例。
- [x] 2.2 回归既有查询契约。验证：以环境无关的契约用例断言 `POST /connections/{ck}/query` 请求模型字段（`project/sql/limit/offset` 与默认值）与响应字段集合（`columns/rows/count/truncated/limit/offset`）未被改动；`tests/test_query_endpoint.py` 的 3 个用例在本机失败，根因是环境问题（MySQL 凭据被拒 `Access denied for user 'synetra'@'localhost'` + Windows 临时目录清理 `WinError 32`），与本次改动无关（未触碰 `query.py`/`executor.py`）。

## 3. 前端：查询页四区布局 [P1]（联调依赖 1.6）

- [x] 3.1 重写 `services/frontend/src/pages/Query.jsx` 为左右两栏四区：左列上=连接选择器，左列下=表/视图面板（检索框 + 类型切换 + 可滚动列表），右列上=SQL 编辑卡片（编辑器 + 执行按钮），右列下=结果卡片；列表项沿用 `mac-list__item` 等既有样式类。验证：打开查询页可见四个区域（`.mac-workbench` 两列：288px 侧栏 + 主区，侧栏 2 张卡片、主区 SQL 卡片）；视口 820×900 时退化为单列且 `overflowX=0`。**补充修复（评审反馈）**：`.mac-textarea` 未设宽度，`<textarea>` 默认 `cols=20` + `inline-block` 导致编辑器仅 184px 宽（坐在 664px 卡片里）；已在共享样式改为 `display:block; width:100%`，编辑器实测 614px = 卡片内容区宽度。同时在 REST/API 模式下不再渲染无意义的 SQL 输入框（该模式请求体不含 SQL），改为提示块 + 执行按钮。
- [x] 3.2 连接切换触发 `GET /connections/{conn_key}/catalog` 枚举刷新：加载中显示 `Skeleton`，失败显示 `Callout` 与失败原因，未选择连接或无可用关系库连接时显示明确引导文案且不发起请求。验证：首次进入页面仅发起 1 次枚举请求且左列渲染 4 个真实表；未选连接时不发请求（`mode!=="rel" || !connKey` 提前返回）；枚举失败时展示 `Callout` 原因。
- [x] 3.3 当 `schemas` 长度大于 1 时显示 schema 选择器并支持切换（切换后按新 schema 重新枚举），长度等于 1 时隐藏该控件。验证：MySQL 单库连接（`schemas=["aitest"]`）不显示该控件；多 schema 分支由 1.3 的单测覆盖（本机无 PG 实例）。
- [x] 3.4 列表检索框与「表 / 视图」类型切换。验证：输入 `ord` 后列表由 4 项收窄为 `orders / orders4 / orders_z`；切到「视图」后列表为空并显示「没有匹配的对象」，切回「全部」恢复 4 项。
- [x] 3.5 点击列表项把后端返回的 `snippet` 写入 SQL 编辑器，且不触发任何查询请求。验证：点击 `product` 后编辑器值变为 `` SELECT * FROM `aitest`.`product` LIMIT 100; ``；点击后结果区仍为空（`.mac-table` 数为 0），确认未发出查询。
- [x] 3.6 执行与结果区沿用既有契约与归一化渲染（`Array.isArray(row)` 时 `row[i]`，否则 `row[c]`），执行按钮仍调用 `POST /connections/{ck}/query`。验证：点击执行后结果区标题为「查询结果（10 行 · truncated=false）」，表头为 `id/product_no/name/...`，单元格显示真实值（`PRD001`、`笔记本电脑`、`5999.00` 等）；提交 `DROP TABLE product` 时显示只读守卫拒绝提示。

## 4. 端到端验收 [P1]（依赖 1–3）

- [x] 4.1 本地启动后端（:8000）与前端，用真实 MySQL 连接走通「选连接 → 见库表视图 → 点表取 SQL → 执行 → 看结果」。验证：用开发库中已配置的 `MySQL-Test`（`127.0.0.1:3306/aitest`）在浏览器实测——左列出现 `orders/orders4/orders_z/product`；点击 `product` 填入 `` SELECT * FROM `aitest`.`product` LIMIT 100; ``；执行返回 10 行真实商品数据，全流程不离开查询页。
- [x] 4.2 只读边界与审计回归。验证：界面提交 `DROP TABLE product` → `422 仅允许单条 SELECT，语句头为: DROP` 并显示只读提示；`SELECT 1; SELECT 2` → `422 检测到 2 条语句`；`project=other-project` 枚举 → `403 无该项目访问权限`；审计日志出现 `action=query` 且 `detail.op=catalog` 记录；`limit=5` 时返回 5 行且 `truncated=true`，不超过上限。
- [x] 4.3 回归既有质量门。验证：`ruff check app/ tests/` → All checks passed；`pytest tests/test_catalog_endpoint.py` → 16 passed / 1 skipped（真实库用例凭据可用时执行）；`vite build` → built in 1.15s（exit 0）。`tests/test_query_endpoint.py` 的 3 个用例因本机 MySQL 凭据被拒而失败，属既有环境问题（见 2.2）。

## 5. P2：枚举结果短时缓存 [P2]

- [x] 5.1 对同一连接的枚举结果做短时（约 30s）缓存，切换连接或点击刷新时失效。验证：首次进入页面 1 次枚举请求；切到 REST/API 再切回关系库仍为 1 次（命中缓存）且列表仍渲染 4 项；点击「重新枚举」后为 2 次（绕过缓存）。检索与类型过滤为客户端过滤，本就不产生请求。
