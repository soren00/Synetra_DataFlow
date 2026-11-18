# Proposal

## Why

`doc/test-report.md` 全量端到端验收后登记了 12 项缺陷（Critical 1 · Major 3 · Minor 5 · Info 3），报告的发布判定因此为「公网/生产：不建议发布，须先修复 D-01、D-02/D-04、D-03」。其中 D-01 身份完全由请求头决定，任何能访问 8000 端口的进程即可获得管理员权限，等同于无权限系统。

报告的处置决策为「本阶段不修复，仅登记留档；修复计划另立变更」。本变更即为该修复计划：把 12 项登记缺口全部收敛为可验证的行为要求并落地实现，使同一把尺子（E2E 套件）在缺口修复后自动翻转断言，缺口状态变化不被静默忽略。

## What Changes

**身份与鉴权（D-01 / D-02 / D-04）**

- **BREAKING** 会话身份改为 HMAC 签名 Bearer 票据：`POST /api/auth/login` 校验口令后签发票据，服务端验签 + 过期校验 + 用户状态复核。**彻底移除** `X-Synetra-User` 请求头信任路径（取代「明文头由网关/SSO 保证」的临时决策）。
- 统一路由鉴权兜底：除 `/health`、`/api/auth/login` 与服务凭据触发路由外，全部路由默认要求至少只读身份；写操作要求开发及以上；用户 / LLM 配置 / 审计日志要求管理员。新增路由漏挂鉴权不再可能静默发生。
- **外部触发接口 `/api/trigger/{task_key}` 保持独立的服务凭据通道**：其 `Authorization: Bearer <服务凭据>` 不参与用户会话校验，迁至独立路由模块以免被统一兜底拦截。
- 匿名请求语义统一为 `401`（未携带或票据无效），角色不足为 `403`。

**功能缺陷（D-03 / D-05 / D-06 / D-07 / D-11）**

- 连接元数据 `meta` 放宽为任意 JSON 值，支持 `params` / `headers` 嵌套结构；表单内「测试连接」同样携带参数与请求头。
- `GET /api/monitor/system` 增加跨平台兜底：非 Linux 主机返回明确的不支持标记与零值，而非 500。
- 登录页文案与实现对齐：移除不存在的 `dev / dev` 演示账号承诺。
- 画布步级指标徽标改用服务端支持的分页参数，取消被静默忽略的 `limit` 传参。
- 管理员自助停用返回 `403`（不能停用自己），不再被「最后一名管理员保护」提前拦截为 `409`。

**产品缺口（D-08 / D-09 / D-10 / D-12）**

- 新增 `GET /api/audit/events`（管理员）审计日志查询接口；「权限凭据」页的「API 凭据」「审计日志」两个 Tab 补齐签发（明文仅一次展示）/ 列表 / 吊销与筛选控件。
- 定时任务页「新建」可直接创建调度（选择项目与任务 + cron），不再仅提示去 ETL 任务页配置。
- 主数据页补齐记录级列表、新增、编辑与同步分发入口。
- Web 界面支持 URL 路由：侧边栏切页写入地址栏，刷新与深链可恢复到对应页面。

**回归资产（全量）**

- 后端测试改为「统一身份夹具 + 鉴权专项真票据用例」两层，E2E 夹具改为真实登录取票据；9 条 `knownIssue` 用例翻转断言，另加 2 条语义翻转（`AUTH-09` 403→401、`NEG-04` 刷新回总览→停留当前页），并为鉴权边界补用例。
- `doc/test-report.md` 与用例清单同步更新缺陷状态（登记 → 已修复 / 待回归）。

## Capabilities

### New Capabilities

无新增能力域。

### Modified Capabilities

- `access-control`: 会话票据认证取代请求头信任（**BREAKING**，取代「明文头由网关/SSO 保证」的旧决策）；接口鉴权兜底与 401/403 错误语义；管理员自助停用返回 403；凭据管理界面行为；新增审计日志查询。
- `monitoring-reconciliation`: 新增主机系统资源采集的跨平台降级契约（非 Linux 返回不支持标记而非错误）。
- `scheduling-execution`: 定时任务管理视图支持直接创建调度（原需求仅覆盖查看与启停）。
- `platform-foundation`: Web 界面支持 URL 路由、刷新与深链恢复（原需求未覆盖导航可寻址）。

### 无 spec 变更的既有需求对齐

以下缺陷属于「已写入需求但实现未对齐」，修复只改实现与用例，不产生 spec delta，落点见 tasks.md：

| 缺陷 | 对齐的既有需求 |
| --- | --- |
| D-03 连接参数无法保存 | `data-connections` · Postman 式 REST/API 连接配置 |
| D-07 运行历史分页参数 | `monitoring-reconciliation` · 运行历史 |
| D-10 主数据记录维护无入口 | `master-data` · 主数据表维护 / 主数据同步分发 |
| D-06 登录页演示账号文案 | `access-control` · 默认账号与账号收敛 |
| 全量缺口翻转断言 | `quality-assurance` · 已知缺口登记 / 缺口修复后可被感知 |

## Impact

**后端**：`app/auth.py`（票据签发/验签、路由兜底依赖）、`app/core/config.py`（会话密钥与 TTL）、`app/main.py`（路由级依赖）、`app/api/auth.py`（登录返回票据、系统资源兜底）、`app/api/access.py`（守卫顺序、审计列表、**外部触发路由迁出**）、`app/api/trigger.py`（新增，服务凭据通道）、`app/api/connections.py`（写操作门禁）、`app/api/monitor.py`（分页参数）、`app/models/connection.py`（meta 类型）、`app/store.py`（审计查询复用）。

**前端**：`src/api/client.js`（Bearer 与 401 登出）、`src/App.jsx`（hash 路由）、`pages/Login.jsx`、`pages/Connections.jsx`、`pages/Schedules.jsx`、`pages/Security.jsx`、`pages/MDM.jsx`、`pages/Monitor.jsx`、`components/PipelineCanvas.jsx`。

**测试**：`services/backend/tests/conftest.py` 统一身份夹具 + 15 个测试文件、43 处身份头改造；`e2e/fixtures/session.js`、`e2e/setup/seed.setup.js` 与 11 个文件、24 处请求头用法；新增鉴权边界用例。

**部署与兼容（BREAKING）**：任何依赖 `X-Synetra-User` 的外部脚本/网关将被 `401` 拒绝，需改为先登录取票据；`SYNETRA_SESSION_SECRET` 未配置时在数据目录自动生成并持久化，多实例部署必须显式共享同一密钥。

**文档**：`doc/test-report.md`、`doc/test-plan.md`、`reports/e2e/cases.md` 的缺陷状态与用例清单。
