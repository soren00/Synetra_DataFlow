# Tasks

> 分期标注：[P1] = 端到端闭环（放开发布阻塞项）；[P2] = 界面与连接器扩展项。
> 依赖关系：组 2/3 依赖组 1；组 4 依赖组 1 与组 3；组 5/6/7/8 依赖组 1（角色门禁）；组 9 依赖全部。

## 1. 会话票据与鉴权兜底（后端）[P1]

- [x] 1.1 在 `app/core/config.py` 增加 `session_secret`（默认空）与 `session_ttl_minutes`（默认 480）；未配置密钥时于 `data_dir/.session_secret` 生成 32 字节随机值并持久化（POSIX 下 chmod 0600）。验证：连续启动两次读取同一密钥；`tests` 下新增用例断言密钥文件内容长度与稳定性。
- [x] 1.2 在 `app/auth.py` 实现 `issue_session_token(username)` / `verify_session_token(token) -> str | None`（格式 `v1.<payload>.<sig>`，载荷仅 `u`/`exp`；签名密钥按用户派生 `HMAC(session_secret, password_hash)`，用 `hmac.compare_digest` 比较）。验证：单测覆盖正确签名、篡改载荷、错误签名、过期、非 v1 前缀五种情况。
- [x] 1.3 重写 `current_user()`：仅从 `Authorization: Bearer` 解析，验签 → 回查 `users` 表校验 enabled → 未知/失效返回 401（删除 `X-Synetra-User` 分支，参数签名中移除该 Header）。验证：单测断言「仅带 `X-Synetra-User: admin` 的请求返回 401」「带有效票据返回 admin 身份」「账号停用后原票据返回 401」。
- [x] 1.4 `POST /api/auth/login` 返回 `{username, role, token, expires_at}`；`POST /api/auth/change-password` 成功后旧票据因派生密钥变化自动失效。验证：登录响应含 token；改密后用旧 token 请求 `/api/users` 返回 401。
- [x] 1.5 把 `POST /api/trigger/{task_key}` 从 `app/api/access.py` 迁到新模块 `app/api/trigger.py`（自带 `_bearer` 与 `verify_credential`，不挂用户会话依赖），`main.py` 单独注册且不加统一兜底；必须先于 1.6 完成，否则统一兜底会让服务凭据触发失效。验证：服务凭据触发返回 202 且产生 run 记录；无凭据返回 401，且失败原因不是「缺少用户会话」。
- [x] 1.6 在 `app/main.py` 为除 `auth_router` 与**触发 router**外的所有 router 注册 `dependencies=[Depends(require_role(Role.READONLY))]`；`app/api/auth.py` 的 `/monitor/system` 单独挂 `AuthorizedDep`，`/auth/login` 保持公开。验证：匿名请求 `/api/connections`、`/api/tasks`、`/api/runs`、`/api/mdm/models` 均返回 401，`/health`、`/api/auth/login`、服务凭据触发不需要用户票据。
- [x] 1.7 补齐写操作角色门禁：`POST/PUT /api/connections`、`POST /api/connections/test`、`/connections/{k}/sniff`、`/connections/{k}/sample` 挂 `DeveloperDep`。验证：readonly 票据调用这些接口返回 403，developer 票据返回 2xx。

## 2. 后端测试夹具改造与鉴权回归 [P1]

- [x] 2.1 在 `tests/conftest.py` 增加 autouse fixture：用 `app.dependency_overrides[current_user]` 注入 admin `AuthContext`（仅替换身份解析，保留 `require_role` 角色判定）；新增 `tests/authhelpers.py` 的 `act_as()` / `clear_identity()` 供用例切换身份或回到匿名（会移除该覆盖）；`tests/test_auth_token.py` 用模块级 autouse fixture 强制走真实验签。验证：`pytest -q` 全绿且用例数不少于改造前（304）。
- [x] 2.2 改造 15 个显式设置 `X-Synetra-User` 的测试文件（共 43 处，`test_access.py`、`test_connection_delete.py`、`test_projects.py`、`test_mdm.py`、`test_metadata.py`、`test_monitor_schedule.py`、`test_pipeline_api.py`、`test_llm_*.py` 等）：统一改为 `act_as(client, user)`（有口令时走真实登录，否则直接签发该账号票据），角色门禁类断言仍走真实 `require_role`。验证：`grep X-Synetra-User tests/` 无残留，上述文件 `pytest -q` 全绿，403 断言仍成立。
- [x] 2.3 新增 `tests/test_auth_token.py`：覆盖 1.2/1.3/1.5/1.6 的场景（伪造票据、篡改载荷、过期、仅声明身份头、停用账号、改密后旧票据失效、匿名读写各业务接口、readonly 写、非 admin 管理接口、服务凭据触发仍 202）。验证：15 条用例全部通过（`pytest tests/test_auth_token.py -q`）。

## 3. 前端票据接入与 E2E 夹具改造 [P1]

- [x] 3.1 `src/api/client.js`：会话结构扩展为 `{username, role, token, exp}`，`headers()` 发 `Authorization: Bearer`；`request()` 在 401 时 `clearSession()`、触发 `onUnauthorized` 回调并抛出可识别错误；删除 `X-Synetra-User`。验证：手工登录后 DevTools 中请求头为 Bearer，篡改 token 后下一次请求即回到登录页。
- [x] 3.2 `src/App.jsx` 注册 `onUnauthorized(() => setSession(null))` 使 401 后立即回登录页（不能只清 localStorage，React 不会因此重渲染）；`Login.jsx` 提交成功后保存含 token 的会话。验证：`AUTH-03/04/05` 三条用例通过，且手工把 token 改坏后界面自动退出。
- [x] 3.3 `e2e/fixtures/session.js`：`actAs()` 改为先 `POST /api/auth/login` 取 token 再写 localStorage；新增 `authHeaders(request)` 助手返回 `{Authorization}`。验证：`npx playwright test --config=e2e/playwright.config.js specs/01-auth.spec.js` 通过。
- [x] 3.4 替换 11 个文件、24 处 `X-Synetra-User`（specs + `setup/seed.setup.js` + `fixtures/session.js`）为 `authHeaders(...)`；`seed.setup.js` 在准备数据前先以 admin 登录取票据。验证：全量 E2E 无 401 类失败，且 `grep X-Synetra-User services/frontend` 无结果（实测 0 处）。
- [x] 3.5 翻转缺口断言：`NEG-06`（匿名读与写均 401）、`CN-11`（readonly 建连接 403）、`AUTH-10`（登录页不再承诺 dev/dev）、`AUTH-09`（伪造/缺失身份头一律 401，不再降级 readonly），并移除对应 `knownIssue` 标注。验证：这 4 条用例在新断言下通过。

## 4. P1 功能缺陷收敛（D-03 / D-05 / D-06 / D-07 / D-11）[P1]

- [x] 4.1 `app/models/connection.py`：`meta` 改为 `dict[str, Any]`（`ConnectionCreate` 与 `ConnectionOut` 同步），加 validator 限制值为 JSON 标量/`dict[str,str]`/`list` 且序列化长度 ≤ 8KB。验证：`test_meta_supports_nested_params_and_headers`、`test_meta_rejects_oversized_or_unsupported_values` 通过。
- [x] 4.2 `pages/Connections.jsx`：抽出 `buildMeta()` 供 `submit()` 与 `testForm()` 共用，使试连与保存的参数结构一致。验证：`CN-09b` 翻转为「带 Params/Headers 可保存并回读」。
- [x] 4.3 `app/api/auth.py` 资源采集：`_cpu_percent()` 捕获 `OSError` 返回 `None`，`_disk()` 捕获 `AttributeError`（Windows 无 `statvfs`），响应加 `supported: bool`，hostname 用 `socket.gethostname()` 兜底。验证：`test_system_resource_degrades_without_proc` 与 `MO-01` 通过。
- [x] 4.4 `pages/Login.jsx` 文案改为仅 `admin / admin`，并提示管理员可自行创建账号。验证：`AUTH-10` 翻转后通过。
- [x] 4.5 `components/PipelineCanvas.jsx` 改用 `page_size: 10` 并删除 `r.items ||` 死分支。验证：`TK-18` 翻转后通过。
- [x] 4.6 `app/api/access.py`：`update_user()` 中 self-check（403）提到 `_guard_last_admin`（409）之前。验证：`SEC-06` 断言 403 + 文案；`SEC-07` 降级场景仍 409。

## 5. 审计查询与凭据/审计界面（D-09）[P2]

- [x] 5.1 `Store.list_events` 增加 `action` 过滤；`api/access.py` 新增 `GET /api/audit/events?project=&action=&limit=`（`AdminDep`），返回 `ts/actor/project/action/detail`。验证：`test_audit_events_admin_only_and_filtered` 覆盖 dev 403、管理员按 action/项目过滤生效。
- [x] 5.2 `pages/Security.jsx`「API 凭据」页签：项目选择（`GET /api/projects`）→ 凭据列表（`GET /api/projects/{p}/credentials`）→ 签发（明文一次性展示）/吊销。验证：`SEC-08` 覆盖签发后列表出现、吊销后状态变为已吊销。
- [x] 5.3 `pages/Security.jsx`「审计日志」页签：项目输入 + 动作输入 + 查询，表格展示五列；非管理员不渲染查询控件（仅提示）。验证：`SEC-08` 翻转通过；`SEC-09/11` 仍断言非管理员无管理入口。

## 6. 定时任务页新建调度（D-08）[P2]

- [x] 6.1 `pages/Schedules.jsx`：新增「新建调度」弹窗（选项目 → 选任务 → cron 预设/自定义 → 提交 `PATCH /api/schedules/{task_key}`），已有调度的任务标注「保存将覆盖」，cron 非法拦截提交，无任务时给出空态引导。验证：`SC-05`、`SC-05b` 通过。
- [x] 6.2 翻转 `SC-05` 断言为「新建可直接创建调度」并移除 `knownIssue`。验证：`specs/08-schedules.spec.js` 全量通过。

## 7. 主数据记录维护界面（D-10）[P2]

- [x] 7.1 `pages/MDM.jsx`：实体展开区增加记录表格（按模型字段动态列，主键取 `_id`）、新增/编辑弹窗（按字段类型转数值）、删除确认与「同步分发」按钮。验证：`MDM-04` 覆盖新增/删除/同步结果。
- [x] 7.2 翻转 `MDM-04` 断言为「记录级 CRUD 与同步具备 UI 入口」并移除 `knownIssue`。验证：`specs/11-mdm.spec.js` 全量通过。

## 8. 界面导航可寻址（D-12）[P2]

- [x] 8.1 `src/App.jsx`：页面切换写入 `location.hash`（`#/<nav key>`），初始化按 hash 恢复，监听 `hashchange` 支持前进/后退；无效或越权 key 回退到首个可见页。验证：`NEG-04` 覆盖切页写地址、刷新保持、无效标识回退。
- [x] 8.2 翻转 `NEG-04` 断言为「刷新停留在当前页」并移除 `knownIssue`。验证：`specs/15-negative.spec.js` 全量通过。

## 9. 全量回归与文档状态更新

- [x] 9.1 后端全量回归：`services/backend/.venv/Scripts/python.exe -m pytest -q` → **323 passed**（1 warning），退出码 0。
- [x] 9.2 E2E 全量回归：`npx playwright test --config=e2e/playwright.config.js` → **108 passed / 0 failed**（9.7 分钟）；`grep -c knownIssue e2e/specs` 为 **0**。
- [x] 9.3 更新 `doc/test-report.md`：新增 §3.1「修复结果」逐项标注状态与复验用例，处置决策改为「已全部修复」，发布判定翻转为「可发布」。
- [x] 9.4 更新 `doc/test-plan.md`（hash 路由与票据鉴权前提）与 `reports/e2e/cases.md`（补状态说明、变更后的用例名与 108 条口径）；两份文档 `grep X-Synetra-User` 无残留。
- [x] 9.5 `README.md` 认证章节改写为「登录取会话票据 + `SYNETRA_SESSION_SECRET`（多实例须共享）+ 服务凭据通道 + 角色门禁」；已按文档完成一次登录与业务调用验证（E2E `AUTH-03/04`、`FLOW-01`）。

## Workflow follow-up

- 变更实施完成并通过全量回归后，按项目评审要求归档该变更。
- 归档后核对 `openspec/specs/` 中 4 个能力域的合入结果。
