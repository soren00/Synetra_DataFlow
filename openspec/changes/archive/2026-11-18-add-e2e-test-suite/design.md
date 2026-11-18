# Design

## Context

动机见 `proposal.md - Why`；行为契约见 `specs/quality-assurance/spec.md`。本节只记录影响技术选型的现状约束：

- 前端是**单页无路由**应用：`App.jsx` 用 `useState` 在 11 个页面间切换，没有 URL path，也没有 `data-testid`，可定位锚点只有 `#login-user` / `#login-pass`、`placeholder`、`role="tab"` 与 `title` 属性。
- 身份是**客户端声明式**：会话存在 `localStorage["synetra.session"]`（明文 `{username, role}`），请求头 `X-Synetra-User` 决定服务端身份，无 token、无签名（`app/auth.py:54-61`）。
- 后端有**环境变量级开关**，足以在不改代码的前提下切换运行形态：`SYNETRA_METADATA_DB_DSN`、`SYNETRA_DATA_DIR`、`SYNETRA_LOCAL_SCHEDULER_ENABLED`、`SYNETRA_RETRY_TIMES/INTERVAL_S`。
- 宿主环境为 Windows，存在两条硬约束：Playwright 用系统代码页（GBK）解析子进程输出会因中文日志抛解码异常；宿主的 safe-delete 守卫会拦截「单次 >500 文件」的删除，而 Playwright 启动时会清空 `outputDir`。
- 本机已装 Chrome，未装 ffmpeg（Playwright 录屏依赖）。

## Goals / Non-Goals

**Goals:**

- 一条命令从零到「有结果、有证据、有编号」的完整执行，环境准备不产生人工步骤。
- 断言口径统一：无头与有头、首次执行与重复执行，结果一致可复现。
- 缺口可见：已确认的产品缺陷在每一次回归中都被显式呈现，而不是长期红或静默绿。

**Non-Goals:**

- 不引入 CI 流水线定义（仅在文档给出可移植的执行命令）。
- 不做跨浏览器矩阵、视觉回归（像素比对）、接口契约测试（后端 pytest 已覆盖）。
- 不为测试而修改产品代码（不新增 `data-testid`，不调整路由结构）。

## Decisions

### D1 执行器：Playwright 而非 agent-browser CLI / Cypress

选择 Playwright（`@playwright/test` 1.65 + `channel: chrome`）。

- 理由：内置 `webServer` 编排、`expect.poll` 自动重试、多 project 依赖、结构化 JSON reporter；能复用本机 Chrome 而无需下载内核。
- 否决 `agent-browser CLI`（探索式点击）：不可回归、断言能力弱、证据零散，只适合一次性排查；本轮仅在其思路下保留了「模拟人工操作」的用例组织方式。
- 否决 Cypress：需要额外浏览器打包且对多标签页/请求拦截能力弱于 Playwright，与现有 Node 24 工具链无协同优势。

### D2 环境编排：Playwright `webServer` 托管双服务

- 理由：`webServer` 在用例前拉起、在结束时终止，天然保证「命令即环境」；配合 `url` 健康探针，服务未就绪时不会误跑用例。
- 否决 `docker-compose`：项目定位单机一键部署，本地开发人员手上已有 MySQL，引入容器反而抬高门槛。
- 否决「脚本手动起服务」：无法保证进程回收，残留进程会污染下一次执行。

### D3 数据隔离：独立元数据库 + 独立样例库 + 独立运行时目录

元数据库 `synetra_e2e`、样例源库 `synetra_e2e_src`、目标库 `synetra_e2e_dst`、运行时目录 `services/backend/data-e2e`，全部由 `DB_DSN`/`DATA_DIR` 环境变量指向。

- 理由：应用启动时自带 `ensure_database`，隔离库无需人工建；样例表由 `fixtures/db_setup.py` 幂等重建，保证每次执行起点一致。
- 否决「复用开发库 `synetra`」：存量连接/任务会被破坏性用例删除，且历史运行记录会让分页、统计类断言不稳定。
- 否决「SQLite 回退模式」：无真实 MySQL 源就测不到库表枚举、只读 SQL 守卫、写入落库与行数一致性，而这正是最有价值的一段链路。

### D4 身份注入：`addInitScript` 预置会话，保留一条真实登录路径

角色切换通过 `page.addInitScript` 写 `localStorage["synetra.session"]`，等价于「以该角色登录后刷新」。

- 理由：login 表单每次要走 bcrypt 校验，106 条用例逐条登录会显著拉长时长；而服务端身份只由请求头决定，预置会话与真实登录对被测系统等价。
- 否决「全部走登录表单」：慢且会把登录失败耦合进所有用例的失败原因。
- 折中：保留 `AUTH-01~05` 走真实表单，覆盖登录/错误口令/登出/会话保持，避免「只测注入不测登录」。

### D5 首帧竞态：`expect.poll` + 定向选择器，而非固定 sleep 或改产品加 `data-testid`

页面首帧普遍存在加载态（统计卡为 0、列表显示「加载中…」、连接下拉只有「（无可用连接）」占位）。处理方式：

- 对「值会变」的断言用 `expect.poll`（下拉选项、刷新后的时间列）；
- 对「行会替换」的列表用业务属性定位真实行，例如 `tr[title="点击查看运行详情"]`，避免命中占位行。
- 否决固定 `sleep`：既慢又不稳定。
- 否决给产品加 `data-testid`：属于改动 `src/`，超出本变更「不动业务代码」的边界；若后续反复因选择器抖动返工，另立变更统一补锚点。

### D6 已知缺口：`knownIssue` 注解 + 断言现状，而非 `skip`

对已确认缺口（无认证机制、连接创建无鉴权、API 连接参数 422、非 Linux 资源接口 500 等），用例断言**当前可观察行为**，并在 `test.info().annotations` 写入 `knownIssue`（含描述与位置）。

- 理由：用例保持绿，缺口进入报告；缺口一旦修复，断言会失败并提示更新用例——状态变化不会被忽略。
- 否决 `test.skip`：跳过等于从回归视图中消失，缺口永远没人提。
- 否决「让它红」：长期红灯会让整轮结果失去信号价值，也无法区分新引入的缺陷。

### D7 证据：全页截图而非 video / trace

- 理由：本机无 ffmpeg（`video` 不可用）；成功用例的临时 trace 归 Playwright 清理，会撞上宿主 safe-delete 守卫，产生与产品无关的噪声失败。截图成本低、可直接贴进报告。
- 否决「只留失败截图」：验收报告需要正向证据（每个页面/交互「长什么样」）。
- 取舍：`video: off`、`trace: off`、`screenshot: only-on-failure`，额外在关键断言点主动 `page.screenshot()` 并 `testInfo.attach()`。

### D8 每次运行使用独立时间戳目录

`outputDir` / JSON / HTML 输出到 `reports/e2e/<RUN_ID>/`。

- 理由：Playwright 启动时清空 `outputDir`，固定目录会触发宿主 safe-delete 守卫（>500 文件）导致整轮失败。
- 否决固定目录：已被实测证伪（整轮无法启动）。

### D9 故障注入用例要求零重试

后端 `SYNETRA_RETRY_TIMES=0`、`SYNETRA_RETRY_INTERVAL_S=1`。

- 理由：「源表不存在」这类用例若走默认 2×60s 重试，单条用例就会超过 2 分钟并撞到用例超时。
- 否决在用例侧放宽超时：掩盖问题且拖慢全量执行。

### D10 用例按能力域一个文件、编号前缀对齐报告

`specs/01-auth.spec.js` ~ `15-negative.spec.js`，编号 `AUTH/OV/CN/QR/TK/AI/SC/MO/LN/MDM/SEC/LLM/FLOW/NEG`。

- 理由：与 `doc/test-report.md` 的模块表一一对应，失败时可按编号直接定位文档章节与截图。
- 否决「一个功能一个文件夹」：本项目页面粒度足够粗，过度拆分会让 seed 依赖跨文件传导。

## Risks / Trade-offs

- **[中文字符串选择器脆弱]**（改文案即失败）→ 选择器集中在 fixtures，必要时用作用域限定（Modal 内、表格行内）；文案变更属产品决策，用例失败恰好提示同步更新断言。
- **[无 URL 路由导致刷新即回总览]** → 全部切页走 `gotoPage()` 点击侧边栏；用例不假设深链，`NEG-04` 显式记录该设计现状。
- **[依赖本机 Chrome 版本]** → `channel: chrome` 使结果受浏览器版本影响；换机或上 CI 时需固定 Chrome 版本或改为 `playwright install chromium`。
- **[串行执行约 7 分钟]** → 当前 worker=1 换取时序确定性；若时长成为瓶颈，可将只读页面（OV/MO/LN）拆为独立 project 并行，但需先解决它们对 seed 数据的共享依赖。
- **[隔离库数据随执行增长]**（每轮新建连接/用户/实体）→ 对象带前缀且库可整体重建；不影响同类断言（均按前缀定位）。
- **[defect D-01 使「角色可见性」用例的语义受限]** → 角色差异目前只在前端导航与后端 `AdminDep` 上生效，用例只能断言这两个可观察面；认证机制修复后需补充「伪造身份被拒」的负向用例。

## Migration Plan

1. 落地 `services/frontend/e2e/` 与 `package.json` scripts（不影响现有构建与运行）。
2. `.gitignore` 增补 `reports/`、`services/backend/data-e2e/`、`services/frontend/e2e/.state/`。
3. 首次执行会自动创建隔离库；执行完成后可用 `db_setup.py --drop` 回收。
4. 回滚：删除 `e2e/` 目录、移除 devDependency 与 scripts、还原 `.gitignore`；对产品代码与数据无任何影响。

## Open Questions

- 是否把该套件接入 CI（需 CI 环境提供 MySQL 与 Chrome）——不影响规格与任务分解，可后续单独决策。
- 是否统一给关键控件补 `data-testid`——需改动 `src/`，应另立变更，与本变更解耦。
