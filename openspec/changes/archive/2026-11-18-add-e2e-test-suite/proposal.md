# Proposal

## Why

系统当前只有后端 `pytest`（304 条，覆盖 API 契约、引擎语义、pipeline 解释器）这一层验证，浏览器层完全空缺：11 个页面的交互完整性、三角色（admin/developer/readonly）可见性差异、跨页业务链路从未被自动化验证，每次改动只能靠人工点击回归，不可重复也无证据留存。

本轮首次把浏览器层验收补齐时，一次全量执行即暴露 12 项问题，其中 4 项是静态检查与单元测试无法发现、只能由真实操作触发的：

- 服务端仅信任 `X-Synetra-User` 请求头，任意客户端声明 `admin` 即获得管理员权限（`app/auth.py:54-61`）；
- 创建/编辑连接路由未挂鉴权依赖，匿名即可写入（`app/api/connections.py:34-37`）；
- API 类型连接只要填了 Params/Headers 必 422（前端嵌套对象 vs 后端 `meta: dict[str,str]`）；
- 非 Linux 环境 `GET /api/monitor/system` 必 500（直读 `/proc/stat` 无兜底）。

这些缺陷说明「能跑起来的单元测试」不等于「用户能走通的产品」。本变更把浏览器层验收固化为可重复执行的资产，并把已确认的实现缺口显式登记，避免下次回归时被静默跳过或被误判为测试失败。

## What Changes

- 新增浏览器层端到端验收套件（Playwright 1.65 + 本机 Chrome channel），106 条用例覆盖 11 个页面、3 个模态（运行详情 / 流水线画布 / 数据预览）、4 条跨页主链路与异常边界。
- 一行命令自举环境：Playwright 托管后端（8000）与前端（5173），并把元数据库强制指向隔离库 `synetra_e2e`、数据目录指向 `data-e2e`、关闭本地调度器与失败重试，**绝不触碰开发库 `synetra` 与既有 `aitest`/`synetra_test`**。
- 数据契约：基线数据通过后端 HTTP API 造数（与前端同通道），E2E 对象统一 `E2E-`/`e2e-` 前缀，破坏性用例只作用于自建对象，避免污染存量数据。
- 证据链：每用例关键步骤全页截图 + 机器可读结果 JSON + 用例明细表；失败用例保留 error-context 快照。
- 已知缺口登记机制：对已确认的实现缺口，用例断言「当前现状」并以 `test.info().annotations` 标注 `knownIssue`，使缺口在回归中持续可见，而不是被跳过或长期红。
- 交付测试方案与测试报告两份文档，报告含覆盖矩阵、缺陷清单与发布建议。

明确不在本变更范围：

- 修复上述 12 项缺陷（另立变更）；
- 性能/压力测试、跨浏览器矩阵（仅 Chrome）、移动端响应式验收；
- 需要外部凭据的路径（LLM 真实生成、SMTP 真实发信）与需要 Linux 的指标路径，仅验证降级行为。

## Capabilities

### New Capabilities

- `quality-assurance`: 端到端验收测试的执行契约 —— 隔离环境自举、基线数据夹具与清理、验收覆盖边界、证据与结果留存、已知缺口登记、回归入口与通过判定。

### Modified Capabilities

（无。本变更不改变任何产品行为，不修改既有能力域的需求。）

## Impact

- **新增代码**：`services/frontend/e2e/`（`playwright.config.js`、`fixtures/`、`setup/seed.setup.js`、15 个 spec 文件、`fixtures/db_setup.py`）。
- **前端工程**：`services/frontend/package.json` 新增 devDependency `@playwright/test` 与 2 条 scripts（`test:e2e`、`test:e2e:headed`）；**不改动任何 `src/` 业务代码**。
- **仓库治理**：`.gitignore` 新增 `reports/`、`services/backend/data-e2e/`、`services/frontend/e2e/.state/`，避免 19MB 级测试产物与运行态入库。
- **后端**：零代码改动；仅通过环境变量（`SYNETRA_METADATA_DB_DSN`、`SYNETRA_DATA_DIR`、`SYNETRA_LOCAL_SCHEDULER_ENABLED`、`SYNETRA_RETRY_TIMES`）切换运行形态。
- **依赖**：本机 Chrome（Playwright `channel: chrome`，不下载额外浏览器内核）；`db_setup.py` 复用后端 venv 内的 `pymysql`。
- **文档**：`doc/test-plan.md`（方案）、`doc/test-report.md`（执行报告与缺陷清单）。
- **已知边界**：套件默认 headless 串行执行，全量约 7 分钟；有头观察需显式置 `E2E_HEADED=1`。
