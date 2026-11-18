# Design

## Context

见 `proposal.md` 的 Why。设计只描述现状约束与实现路径。

现状约束（均已核实）：

- 身份：`app/auth.py:54-61` 把 `X-Synetra-User` 直接映射为账号，角色来自 `users` 表；`AuthContext` 是唯一身份载体，`require_role()` 已实现角色序（admin 3 / developer 2 / readonly 1）。
- 鉴权覆盖不一致：`DELETE /api/connections/{k}`、`/api/users*`、`/api/llm/*` 有依赖；`POST/PUT /api/connections`、`/tasks`(GET)、`/runs`、`/mdm`(GET) 无依赖。这是「逐路由手挂依赖」的必然结果。
- 前端 `src/api/client.js` 每次请求从 `localStorage["synetra.session"]` 取 `username` 放进请求头；无 401 处理，会话是明文 `{username, role}`。
- 元数据落库：连接 `meta` 序列化为 `meta_json`（TEXT / LONGTEXT），**存储层不限制结构**；限制只存在于 Pydantic 的 `dict[str,str]`。`api/connections.py:153-162` 的样例拉取已按 `meta["params"]`/`meta["headers"]` 读 dict，说明嵌套结构本就是设计意图。
- E2E 依赖：`e2e/fixtures/session.js` 的 `actAs()` 直接写 localStorage 冒充角色；11 个文件、24 处手写 `X-Synetra-User`（含 `setup/seed.setup.js`）。后端测试 15 个文件、43 处显式设身份头，其余大量用例默认匿名调用（依赖「读写路由未鉴权」才通过）。
- 前端无路由：`App.jsx` 用 `useState("overview")` 做状态机，`NAV` 数组已用稳定的 `key`（`connections`/`query`/…）标识页面。

## Goals / Non-Goals

**Goals**

- 身份只来自服务端签发的票据，且**结构上**保证新增路由默认受保护（不再依赖人工逐条挂依赖）。
- 让现有 107 条 E2E 与 304 条后端用例在同一套断言口径下继续可跑（改夹具，不改判定标准）。
- 12 项缺陷逐项可回归验证，`knownIssue` 标注在修复后必然翻转。

**Non-Goals**

- 不引入第三方认证框架（Authlib/Passport）、不引入 Redis 会话存储、不引入 `psutil`。
- 不做多租户组织层级、不做 RBAC 细粒度权限点（沿用三角色）。
- 不做 CSRF 防护（票据走 Authorization 头，非 Cookie 自动携带，无 CSRF 面）。
- 不改元数据库表结构（`service_credential`、`audit_log` 已够用）。

## Decisions

### D1 会话票据：自签 HMAC 票据（vs 服务端 session 表 / Cookie 会话）

格式 `v1.<b64url(payload)>.<b64url(hmac_sha256)>`，payload 为 `{u: username, exp: unix_ts}`——**载荷不含任何口令材料**。

签名密钥按用户派生：`key = HMAC-SHA256(session_secret, password_hash)`，验签时用库中当前 `password_hash` 重新派生。

- 验签用 `hmac.compare_digest` 常量时间比较；过期以服务端时钟判定。
- 派生密钥让「改密/管理员重置密码」自动作废该用户全部旧票据，无需服务端撤销表；因为票据载荷里没有摘要，客户端也无法利用票据做离线口令校验。
- **备选（已否决）：把口令摘要前 N 位放进载荷**——票据 payload 只是 base64，等于把口令哈希片段交给客户端，可用于离线猜测校验；否决。
- **备选一：服务端 session 表**（存 session_id）→ 可即时吊销，但每次请求多一次写/读 + 需要清理过期行，与既有「每请求新建 Store」模式叠加后成本更高；否决。
- **备选二：HttpOnly Cookie** → 抗 XSS，但需要 CSRF token 机制，且 E2E/脚本调用要处理 cookie 罐；否决（记为后续可选演进）。
- **备选三：网关/SSO 注入签名头** → 对部署有前置依赖，本地单机部署无法自证；否决。

密钥：`SYNETRA_SESSION_SECRET` 优先；缺省时在 `data_dir/.session_secret` 生成 32 字节随机值并持久化（文件权限 0600，POSIX），保证重启后票据不失效。多实例部署必须显式共享同一值（写入部署文档）。

TTL：`SYNETRA_SESSION_TTL_MINUTES`，默认 480。

### D2 角色以数据库为准，票据只证明「是谁」

票据只携带 `username`；`current_user()` 验签后回查 `users` 表取 role/enabled。这样停用、改角色即时生效（spec: 账号状态变更即时生效）。代价是每请求一次用户查询——与现状（每次请求都 `store.get_user`）相同，无额外退化。

匿名/无效票据：`raise HTTPException(401)`（原实现是「降级为 readonly」，这正是 D-01 的根因）。`require_role` 保留 403 语义。

### D3 路由级兜底：注册时统一挂依赖（vs 每个路由手写）

`main.py` 注册时对除 `auth_router` 外的 router 统一传 `dependencies=[Depends(require_role(Role.READONLY))]`；`auth_router` 内仅 `/auth/login` 公开，`/monitor/system` 与 `/auth/change-password` 单独挂依赖。

- 效果：任何后续新增的 router 默认受保护；漏写依赖只会「过严」不会「过松」。
- **备选：FastAPI 全局 `Depends` / middleware 白名单** → 全局依赖无法按路径豁免，middleware 会绕过 OpenAPI 文档与 403/401 语义；否决。
- 写操作在此之上叠加 `DeveloperDep`/`AdminDep`，保持既有语义不变（`POST/PUT /api/connections` 补 `DeveloperDep`）。

### D3.1 外部触发通道必须与用户会话隔离

`POST /api/trigger/{task_key}` 现在在 `api/access.py` 内，用 `Authorization: Bearer <服务凭据>` 做**服务凭据**鉴权，与用户票据同头不同语义。若按 D3 给它挂上 router 级 `require_role(READONLY)`，外部系统用凭据触发会被判为「无用户会话」而 401，`scheduling-execution` 的 REST API 异步触发需求直接失效。

做法：把触发端点迁到新模块 `app/api/trigger.py`（自带 `_bearer` + `verify_credential` 逻辑，**不挂**用户会话依赖），`main.py` 单独注册且不加统一兜底。

- 备选：在 `current_user` 中让「不认识的服务凭据」落到匿名并放行 → 等于给所有路由开后门，否决。
- 备选：给统一依赖加路径白名单 → 依赖拿不到请求路径（需改成 middleware），会把鉴权语义从声明式退化到命令式，否决。
- 影响面：`api/access.py` 减少一个端点；`trigger` 的错误码与回执结构不变，既有 `test_access.py` 的触发类用例通过调整 `_client` 仍可用。

### D4 前端票据：`localStorage` + 401 统一登出

`synetra.session` 从 `{username, role}` 扩展为 `{username, role, token, exp}`；`headers()` 发 `Authorization: Bearer <token>`。

401 处理必须显式回调，**不能只 `clearSession()`**：`localStorage` 变更不会触发 React 重渲染，只清存储会出现「会话已失效但界面还停在原页、每个请求继续 401」的中间态。做法：`client.js` 暴露 `onUnauthorized(handler)`，`request()` 在 401 时 `clearSession()` 并触发该回调；`App.jsx` 在挂载时注册 `() => setSession(null)`，回登录页。

- `NEG-03`（会话损坏）仍成立：JSON 解析失败 → `null` → 登录页。
- 备选：内存保存 + refresh token → 刷新丢失登录态，破坏 `AUTH-04` 会话保持用例；否决。

### D5 `meta` 放宽为任意 JSON（vs 扁平化键）

`ConnectionCreate.meta`/`ConnectionOut.meta` 改为 `dict[str, Any]`，加 validator 约束值只能是 JSON 标量或 `dict[str,str]`/`list`（防止把二进制、超深结构塞进库）；`Connections.jsx` 的 `testForm()` 与 `submit()` 共用同一个 `buildMeta()`，试连与保存的参数构成完全一致。

- 备选：把 `params`/`headers` 扁平化成 `params.tenant` 字符串键 → 会破坏 `api/connections.py` 样例拉取与连接详情展示语义（详情已 `Object.entries(meta)` 渲染），否决。
- 兼容性：存量连接的 `meta_json` 全是字符串值，放宽类型不需要数据迁移。

### D6 主机资源：返回「不支持」而非报错

`_cpu_percent()` 捕获 `OSError` 返回 `None`；响应增加 `supported: bool`（`/proc/stat` 与 `os.statvfs` 任一不可用即为 false）；`hostname` 已有 `hasattr(os,"uname")` 兜底，Windows 下改用 `socket.gethostname()`。

- 备选：引入 `psutil` → 新增依赖 + 部署需编译，与「一键部署零编译」约束冲突；否决。
- 前端按 `supported` 渲染：false 时资源区显示「当前平台不支持主机资源采集（仅 Linux 实测）」，数值显示 `-`，其余区域不受影响（满足 MO-01 翻转后仍断言「不牵连其它区域」）。

### D7 运行历史分页：单一参数口径

前端 `PipelineCanvas` 改传 `page_size: 10`（`_normalize_page_size` 的合法档位），并删掉 `r.items ||` 死分支。

- 备选：后端把 `limit` 当 `page_size` 别名 → 两套语义并存，且 `limit` 无档位归一会让「10/20/50」约束失效；否决。

### D8 守卫顺序：self-check 前置

`update_user()` 先判 `username == auth.username and body.enabled is False` → 403，再走 `_guard_last_admin` → 409。`delete_user()` 已是此顺序，无需改。

### D9 审计查询：扩展现有 store 方法 + 同域路由

`GET /api/audit/events?project=&action=&limit=`（`AdminDep`），实现上给 `Store.list_events` 增加 `action` 过滤（现只有 project + limit）。放在 `api/access.py`（与用户/凭据同域），避免为单个查询新建 router。

- 响应字段：`ts/actor/project/action/detail_json`（`detail` 以对象返回，不含明文凭证——写日志处已保证）。
- 前端「审计日志」页签：项目输入框 + 动作下拉（从返回数据聚合）+ 刷新，表格展示。非管理员该页签不渲染查询控件（`SEC-09/11` 语义保持）。

### D10 凭据管理 UI：复用既有接口，不新增后端

`GET /api/projects` 取项目 → 选中项目后 `GET /api/projects/{p}/credentials` 列表；签发弹窗调 `POST` 并把一次性明文 token 展示在带「仅显示一次」提示的 Callout 中；吊销走 `POST .../{id}/revoke`。删除项目导致凭据悬挂不在本次范围（后端按项目隔离，查询即天然隔离）。

### D11 新建调度：选项目 → 选任务 → 录 cron

复用 `PATCH /api/schedules/{task_key}`（后端已支持 `schedule`/`enabled`），任务来源为 `GET /api/tasks?project=`。已配置定时的任务在列表中标记「已有调度」，选中时提示将覆盖。cron 校验复用页面现有 5 段规则。

- 备选：新增 `POST /api/schedules` → 后端需要「无 task 无调度」的语义重构，收益低；否决。

### D12 主数据记录 UI：只加前端

后端 `GET/POST/PUT/DELETE /mdm/models/{entity}/records` 与 `POST /mdm/models/{entity}/sync` 均已存在。前端在实体行展开区增加记录表格（按模型字段动态列）、新增/编辑弹窗（按字段类型渲染输入）、删除确认与「同步分发」按钮，质量校验失败的错误按字段呈现（后端 422 detail 已含字段名）。

### D13 URL 路由：hash 而非 History API

用 `location.hash`（`#/connections`），`NAV.key` 即路由段；初始值来自 hash，无效/越权回退到首个可见页；`hashchange` 监听实现前进后退。

- 备选：`history.pushState` + 服务端 rewrite（Nginx `try_files`）→ 客户端路由需要生产静态服务配合，而本项目的生产前端由后端静态托管/一键部署脚本处理，改动面大于收益；否决。
- 备选：引入 `react-router` → 为 11 个平铺页面引入依赖与整套 API，不划算；否决。
- E2E 影响：`00` 号 spec 之外多数用例用 `openAs` 后点侧边栏，仍然有效；`NEG-04` 需从「刷新回总览」翻转为「刷新停留当前页」。

### D14 测试夹具：两层身份，真实路径单独覆盖

后端有 304 条用例，其中 **15 个文件、43 处**显式设置 `X-Synetra-User`，其余数百次调用是匿名的（今天是靠「读接口未鉴权 / 写接口默认 guest 被拒」侥幸通过）。统一兜底后它们会成片 401，因此分两层处理：

1. **默认层（覆盖绝大多数用例）**：`conftest.py` 提供 autouse fixture，用 FastAPI 的 `app.dependency_overrides[current_user]` 注入一个 admin `AuthContext`，测试结束清理。
   - 为什么不是「注入默认 header」：各测试自己 `TestClient(app)`，且各自临时目录里**未必有 admin 用户行**，构造期签发的票据会因「用户不存在」而 401；`dependency_overrides` 不依赖数据预先存在，也不必 monkeypatch `TestClient.__init__` 这类内部实现。
   - 这只替换「身份解析」一步，`require_role` 的角色判定仍真实执行，所以 403 语义仍被真正覆盖。
2. **真实层（鉴权专项）**：`tests/test_auth_token.py` 与角色门禁用例通过 `real_auth` fixture 清掉 override，改用「建用户 → 调 `/api/auth/login` 取真票据 → 设 `Authorization`」；`_auth(client, user)` 助手在这类文件里改为走真登录。这样验签、过期、停用、改密失效等路径都有真实覆盖。

**不采用**「测试环境关闭鉴权」的开关：那会让 304 条用例全部失去鉴权覆盖，D-01 的回归价值归零。

E2E：`actAs()` 改为 `page.request.post('/api/auth/login', {username, password})` 取 token 后 `addInitScript` 写入含 token 的 session；**11 个文件、24 处** `X-Synetra-User` 替换为统一 `authHeaders(request)` 助手（返回 `{Authorization}`）。`setup/seed.setup.js` 建夹具数据前必须先以 admin 登录取票据（它自己也用了身份头），否则整个套件在数据准备阶段即 401。

## Risks / Trade-offs

- [票据存 localStorage，XSS 可窃取] → 渲染层已有转义（`NEG-02`），且票据短 TTL；后续可切 HttpOnly Cookie（已记录为演进项）。
- [签名为无状态，无法即时吊销单张票据] → 通过「停用账号 / 重置密码」使该用户全部票据失效（派生密钥随 `password_hash` 变化）；如未来需要按设备吊销，再加 `token_version` 列。
- [路由级兜底误伤外部触发通道] → 触发端点迁到独立 router 且不挂会话依赖（D3.1），并在 `test_auth_token.py` 保留「服务凭据触发仍 2xx」用例。
- [路由级兜底导致既有集成脚本 401] → 部署文档与 README 明确登录取票据流程；`doc/test-report.md` 的复现命令同步更新。
- [默认层 override 掩盖真实身份解析缺陷] → 真实路径由 `test_auth_token.py` 与角色门禁用例（`real_auth`）覆盖，task 中列为必须项；override 仅替换身份解析，不替换角色判定。
- [`meta` 放宽后可能写入超大结构] → validator 限制值类型与总序列化长度（如 8KB），超出返回 422。
- [D13 hash 路由改变 `NEG-04` 与部分用例的前置假设] → 同一批次内翻转断言，避免「改了代码没改用例」的中间态。
- [平台差异：`/proc`、`os.statvfs`、文件权限 0600 在 Windows 上不可用] → 资源采集按 `supported` 降级；密钥文件权限在非 POSIX 平台跳过 chmod。

## Migration Plan

1. 后端先做「触发路由迁出」（D3.1）——必须早于统一兜底，否则服务凭据触发先被误伤。
2. 再上「票据签发 + 验签 + 路由兜底」，同时改测试夹具（此步之后所有匿名调用立即 401，属预期）。
3. 前端切换 Bearer 并接 401 回调，E2E 夹具与 `seed.setup.js` 改造，跑通全量。
4. 落 P1 功能项（meta 类型、/proc 兜底、文案、分页、守卫顺序），逐项翻转对应 `knownIssue` 断言。
5. 最后落 P2 界面项（审计/凭据/调度新建/MDM 记录/hash 路由），同步更新 `doc/test-report.md` 缺陷状态与 `reports/e2e/cases.md`。

回滚：每步独立可回退；D1 的兼容性由「票据格式版本号 `v1`」保住，未来换机制可并行验签一段时间。存量数据无需迁移。

## Open Questions

- 会话 TTL 默认值（480 分钟）是否满足内网使用习惯？若需「长期免登录」，后续再加 refresh 机制，不影响本次规格与任务分解。
- 审计日志是否需要保留期/归档策略？当前仅按条数限制查询，属容量治理范畴，另行提出。
- 登录失败限流/锁定不在本次范围：D-01 修复后在线口令爆破仍无速率约束，建议另立变更（不影响本变更的规格与任务分解）。
- `store._password_hash` 使用确定性盐（`sha256(password + 固定串)`），相同口令产生相同摘要。属既有实现问题、不在 12 项缺陷内，建议另立变更处理，本次不改以控制影响面。
