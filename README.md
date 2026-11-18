# Synetra_DataFlow

![release](https://img.shields.io/badge/release-v1.0.0-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![node](https://img.shields.io/badge/node-18%2B-339933)

> Web 化 ETL 数据同步与任务调度平台 —— 面向懂 ETL / 数仓的数据工程师。

Synetra_DataFlow 把数据同步从「写脚本」升级为「可用、可管、可追溯」的 Web 工作台：
在浏览器里完成连接管理、只读查询、任务编排、定时调度、运行监控、血缘治理与主数据管理。
平台提供两条转换路径 —— **LLM + Python**（脚手架生成 + 静态校验 + 人工确认）与**手写 Python**（低权限安全沙箱），
以自研 `etl_sdk` 作为抽取/加载执行内核，契约冻结、可插拔扩展。

<p align="center">
  <img src="docs/images/05-pipeline-canvas.png" alt="多步清洗流水线画布" width="880">
  <br>
  <sub>多步清洗流水线画布：拖拽编排、连线即时校验、批次读取 / 提交参数与节点配置入口</sub>
</p>

---

## 界面预览

> 截图为 E2E 用例执行时自动留证的界面状态（Playwright 全页截图）。

<table>
  <tr>
    <td width="50%"><img src="docs/images/01-login.png" alt="登录"><br><sub>登录：用户名口令换会话票据</sub></td>
    <td width="50%"><img src="docs/images/02-overview.png" alt="总览"><br><sub>总览：健康状态、运行记录与统计卡</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/03-connections.png" alt="连接管理"><br><sub>连接管理：关系库 / CSV / REST 统一纳管</sub></td>
    <td width="50%"><img src="docs/images/04-query.png" alt="只读查询"><br><sub>只读查询：库表树、语句校验与结果浏览</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/06-node-config.png" alt="节点配置"><br><sub>节点配置：行级步骤参数、表达式校验与节点预览入口</sub></td>
    <td width="50%"><img src="docs/images/07-node-preview.png" alt="节点预览"><br><sub>节点预览：试运行输出样本与各步行数统计</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/08-run-detail.png" alt="运行详情"><br><sub>运行详情：触发方式、任务上下文与实时日志</sub></td>
    <td width="50%"><img src="docs/images/09-lineage.png" alt="血缘"><br><sub>字段级血缘：按流水线节点逐跳推导</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/10-mdm.png" alt="主数据"><br><sub>主数据：模型维护、记录增删改与同步分发</sub></td>
    <td width="50%"><img src="docs/images/11-audit.png" alt="审计日志"><br><sub>权限凭据：签发 / 吊销与审计日志查询</sub></td>
  </tr>
</table>

## 系统架构

```mermaid
flowchart TB
  subgraph FE["浏览器 · React 18 + Vite"]
    UI["页面与自研 macOS 组件"]
    CANVAS["流水线画布<br/>@xyflow/react"]
    CLIENT["API 客户端<br/>Bearer 会话票据"]
  end

  subgraph BE["FastAPI · /api"]
    AUTH["auth<br/>登录签发 / 验签"]
    ROUTES["业务路由<br/>连接 · 查询 · 任务 · 调度 · 监控 · 血缘 · MDM · 权限"]
    PIPE["pipeline<br/>步骤清单 · 节点预览"]
    TRIGGER["trigger<br/>服务凭据触发"]
  end

  subgraph CORE["执行层"]
    SCHED["LocalScheduler<br/>cron 轮询 + 失败重试"]
    RUNNER["run_task<br/>真实执行通道"]
    ENGINE["etl_sdk.pipeline<br/>解释执行 · 分批流式 · 溢出归并"]
    SANDBOX["scripting<br/>dry-run · 沙箱 · 版本"]
    LLM["llm<br/>脱敏 · RAG · 技能包"]
  end

  DB[("元数据库<br/>MySQL 8 / SQLite 回退")]
  SRC[("数据源<br/>MySQL · PG · Oracle · SQLServer · CSV · REST")]
  DST[("目标<br/>关系库 · CSV")]

  UI --> CLIENT
  CANVAS --> PIPE
  CLIENT --> AUTH
  CLIENT --> ROUTES
  ROUTES --> SCHED
  ROUTES --> RUNNER
  ROUTES --> PIPE
  ROUTES --> LLM
  ROUTES --> SANDBOX
  SCHED --> RUNNER
  TRIGGER --> RUNNER
  RUNNER --> ENGINE
  PIPE --> ENGINE
  ENGINE --> SRC
  ENGINE --> DST
  CORE --> DB
```

- **交互链路**：浏览器只持会话票据，所有身份由服务端验签；业务路由默认要求至少只读身份。
- **执行链路**：手动触发 / 定时触发 / 外部凭据触发统一走 `run_task` 真实执行通道，再交由 `etl_sdk.pipeline` 分批流式执行。
- **配置即事实源**：任务保存时只生成薄入口脚本，多步逻辑由引擎按 `pipeline` 配置解释执行。

---

## 核心特性

- **连接管理**：统一纳管关系库（MySQL / PostgreSQL / Oracle / SQLServer）、CSV、REST/API 等数据源与目标；支持连通性测试、结构嗅探（sniff）与数据抽样（sample）。
- **只读查询工作台**：面向数据工程师的 SQL 查询与调试体验，内置语句校验与结果浏览。
- **ETL 项目 / 任务编排**：拖拽画布编排多步清洗（源 / 多源合并 / 行级处理 / 整批处理 / 目标），节点内复用字段映射五种规则，支持节点级预览与步级指标。
- **多步清洗流水线**：一个任务内可完成「映射 → 过滤 → 去重 → 排序 → 分组聚合 → 写入」，支持多源 `union` 按列名合并；分批流式执行、整批步骤溢出到本地临时盘、按批提交，不落中间表。
- **两条转换路径**：
  - LLM + Python：自然语言描述 → 生成脚本 → dry-run → 静态校验 → 人工确认落地。
  - 手写 Python：低权限身份、最小凭证、网络白名单、资源配额四重安全控制。
- **定时调度与触发**：自研 `LocalScheduler` 轮询 cron 触发任务，支持失败重试（次数/间隔可配）与外部 `Bearer` 服务凭据触发；定时与手动触发共用同一条真实执行通道。
- **运行监控与对账**：运行记录（含执行通道与批次参数）、实时日志、步级指标（每步输入/输出/拒绝行数与耗时）、对账（reconcile）与告警（邮件 SMTP，未配置时降级记日志）。
- **失败可诊断**：步骤失败返回失败节点标识，并自动把该步样本快照落到 `data_dir/runs/<run_id>/`（不写目标库、不建 staging 表）。
- **元数据与血缘**：按流水线节点逐跳推导字段级血缘与图谱，合并节点记录全部上游来源；存量配置按等价编译结果回退。
- **主数据管理（MDM）**：主数据模型维护、记录级增删改与同步分发。
- **认证与权限**：登录签发 HMAC 会话票据（`Authorization: Bearer`），服务端验签 + 过期 + 用户状态复核；角色门禁（admin / developer / readonly）统一兜底，匿名 `401`、越权 `403`。
- **凭据与审计**：连接凭证 Fernet 加密入库；项目级 API 凭据可签发/吊销（明文仅展示一次），审计日志可查询。
- **AI 管理**：LLM 多供应商抽象（默认 DeepSeek），调用前强制脱敏，RAG 注入 `etl_sdk` 文档与技能包。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.10+ · FastAPI · Uvicorn · Pydantic Settings |
| 执行内核 | 自研 `etl_sdk`（抽取/转换/加载 + `run_pipeline` 流水线解释器，配置为唯一事实源） |
| 调度 | 自研 `LocalScheduler`（轮询 + cron 匹配 + 失败重试） |
| 存储 | MySQL 8（元数据/日志/审计/血缘，默认 `mysql://root:***@127.0.0.1:3306/synetra`）· DSN 留空时回退 SQLite |
| 加密 | `cryptography`（Fernet，Vault 密钥） |
| LLM | 供应商无关接入层，默认 DeepSeek，RAG + 脱敏 |
| 前端 | React 18 · Vite 5 · 自研 macOS 设计语言组件；画布内核 `@xyflow/react`（节点/连线用设计 token 自绘） |
| 部署 | Linux 一键脚本（`deploy/install.sh`）+ systemd |

## 目录结构

```
synetra/
├── services/
│   ├── backend/            FastAPI 后端
│   │   ├── app/
│   │   │   ├── api/         REST 路由（auth / connections / catalog / query / tasks /
│   │   │   │               pipeline / scripts / schedule / monitor / metadata / llm /
│   │   │   │               mdm / projects / access / trigger）
│   │   │   ├── core/       配置（Settings）
│   │   │   ├── db.py       数据库抽象层（MySQL/SQLite 双后端与方言适配）
│   │   │   ├── etl_sdk/    抽取/加载运行时（契约冻结点）
│   │   │   │   └── pipeline/  schema / registry / validate / compile / engine
│   │   │   ├── scripting/  执行通道（run_task 真实执行、dry-run、沙箱、脚本版本）
│   │   │   ├── schedule/   自研 LocalScheduler
│   │   │   ├── llm/        LLM 接入层 + RAG + 技能包
│   │   │   ├── metadata/   元数据/血缘（按流水线节点推导）
│   │   │   ├── monitor/    运行监控/对账
│   │   │   ├── alerting/   告警
│   │   │   ├── queries/    只读查询
│   │   │   ├── sources/    数据源连接器
│   │   │   ├── mdm/        主数据
│   │   │   ├── models/     领域模型（task_config / pipeline / connection / access …）
│   │   │   ├── security/   权限/凭据/沙箱
│   │   │   ├── store.py    元数据存储层（MySQL 默认 / SQLite 回退）
│   │   │   ├── auth.py     会话票据签发/验签与角色门禁
│   │   │   ├── vault.py    Fernet 密钥管理
│   │   │   └── main.py     FastAPI 入口 + /health
│   │   ├── tests/          pytest 测试（含流水线引擎 / API / 真实 MySQL 验收）
│   │   ├── scripts/        运维脚本（SQLite → MySQL 元数据迁移）
│   │   ├── pyproject.toml
│   │   └── .env.example
│   └── frontend/           React 前端
│       ├── src/
│       │   ├── pages/      页面（总览/连接/查询/项目/AI任务/定时/血缘/监控/MDM/权限/AI管理/登录）
│       │   ├── components/ 自研 UI 组件 + 流水线画布 / 字段规则编辑器
│       │   ├── api/        API 客户端与会话（Bearer 票据、401 回登录）
│       │   └── theme/      设计 token 与主题
│       ├── e2e/            Playwright 端到端用例（15 个 spec + 夹具 + 种子）
│       └── vite.config.js  dev 5173，代理 /api、/health → 127.0.0.1:8000
├── deploy/                 一键部署脚本（install.sh）与测试库种子（test-db-seed.sql）
├── openspec/               OpenSpec 规划（specs 能力域 + changes/archive 归档变更）
├── docs/                   文档与界面截图（pipeline-steps.md、images/）
├── doc/                    测试计划与测试报告
└── data/                   运行时数据（vault.key、会话密钥、脚本产物、spill/runs）
```

> `openspec/` 是需求/设计/规格的唯一权威来源：`openspec/specs/` 为能力域规格，
> `openspec/changes/archive/` 保存已归档的变更（当前基线为 `dataflow-v2`，其上依次叠加
> `etl-multistep-pipeline`、`add-e2e-test-suite`、`fix-test-report-defects` 三次变更）。

---

## 快速开始

### 前置要求

- Python **3.10+**
- Node.js **18+**（前端构建）
- MySQL **8.0+**（元数据库；默认连接 `127.0.0.1:3306`，账号 `root / 12345`，库名 `synetra`）
  - 首次启动会自动 `CREATE DATABASE IF NOT EXISTS synetra` 并建表；连接账号需具备建库/建表权限。
  - 若本地没有 MySQL，可把 `SYNETRA_METADATA_DB_DSN` 置空，回退为 `data_dir/synetra.db`。

### 1. 启动后端

```bash
cd services/backend
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows (PowerShell)
# .\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

首次启动会**幂等创建唯一管理员账号 `admin / admin`**（不预置演示账号），并按需生成数据目录与 Vault 密钥。

校验：

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","service":"synetra-backend","version":"1.0.0"}
```

> 若系统级 pip 受限（无 sudo），可改用用户级安装后再执行：
> ```bash
> export PYTHONUSERBASE="$HOME/.local/share/pysynetra"
> export PATH="$PYTHONUSERBASE/bin:$PATH"
> pip install -e "services/backend[dev]"
> ```

### 2. 启动前端

```bash
cd services/frontend
npm install
npm run dev
```

浏览器打开 **http://localhost:5173**，使用 `admin / admin` 登录。
开发服务器已将 `/api` 与 `/health` 代理至后端 `http://127.0.0.1:8000`，无需额外跨域配置。

### 3. 生产构建

```bash
cd services/frontend && npm run build   # 产物在 dist/
```

---

## 配置

后端通过环境变量配置，前缀 **`SYNETRA_`**，优先级：环境变量 > `.env` 文件 > 默认值。
复制 `services/backend/.env.example` 为 `services/backend/.env` 后按需填写。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SYNETRA_HOST` / `SYNETRA_PORT` | `0.0.0.0` / `8000` | 服务监听地址与端口 |
| `SYNETRA_DATA_DIR` | `data` | 运行时数据目录（Vault 密钥、会话密钥、脚本产物、spill/runs、SQLite 回退库） |
| `SYNETRA_METADATA_DB_DSN` | `mysql://root:12345@127.0.0.1:3306/synetra` | 元数据库 DSN；格式 `mysql://user:password@host:port/dbname?charset=utf8mb4`，留空回退 SQLite |
| `SYNETRA_SCRIPTS_ROOT` | 空（默认取 `data_dir`） | 脚本 current/versions 产物根目录 |
| `SYNETRA_VAULT_KEY` | 空 | Fernet 密钥（未设置则读写 `data_dir/vault.key`，自动生成并 `chmod 600`） |
| `SYNETRA_LLM_PROVIDER` | `deepseek` | LLM 供应商 |
| `SYNETRA_DEEPSEEK_API_KEY` | 空 | DeepSeek API Key |
| `SYNETRA_DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | DeepSeek 接口地址 |
| `SYNETRA_DEEPSEEK_MODEL` | `deepseek-chat` | 模型名 |
| `SYNETRA_LOCAL_SCHEDULER_ENABLED` | `true` | 是否启用本地轮询调度 |
| `SYNETRA_LOCAL_SCHEDULER_INTERVAL_S` | `20` | 调度轮询间隔（秒） |
| `SYNETRA_RETRY_TIMES` / `SYNETRA_RETRY_INTERVAL_S` | `2` / `60` | 失败重试次数与间隔（秒） |
| `SYNETRA_SESSION_SECRET` | 空 | 会话票据签名密钥；留空则在 `data_dir/.session_secret` 自动生成并持久化（多实例须显式配置同一值） |
| `SYNETRA_SESSION_TTL_MINUTES` | `480` | 会话票据有效期（分钟） |
| `SYNETRA_PIPELINE_BATCH_SIZE` | `5000` | 流水线分批读取大小（行/批） |
| `SYNETRA_PIPELINE_COMMIT_SIZE` | `5000` | 目标写入提交批次（行/批） |
| `SYNETRA_PIPELINE_SPILL_THRESHOLD_MB` | `256` | 整批步骤（排序/去重/聚合）内存物化阈值，超出后溢出到 `data_dir/spill` |
| `SYNETRA_RUN_SNAPSHOT_KEEP` | `20` | 失败/手动样本快照保留份数（`data_dir/runs`） |
| `SYNETRA_SMTP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_FROM` | — | 邮件告警 SMTP，未配置时告警降级为日志 |
| `SYNETRA_ALERT_EMAIL_TO` | 空 | 告警收件人 |

> `.env` 固定从 `services/backend/.env` 读取（与进程工作目录无关），便于从任意目录启动服务。

### 从 SQLite 迁移存量数据

早期版本用 `data_dir/synetra.db` 存元数据；切到 MySQL 后可一次性搬运存量数据（可重复执行，冲突行以源库为准）：

```bash
cd services/backend
python scripts/migrate_sqlite_to_mysql.py --dry-run   # 先看统计
python scripts/migrate_sqlite_to_mysql.py             # 再执行迁移
```

- `secret` / `api_key_cipher` 是 Fernet 密文，解密依赖 `data_dir/vault.key`：迁移后仍使用同一
  `SYNETRA_DATA_DIR`（或同一 `SYNETRA_VAULT_KEY`）即可继续解密，**请勿丢失 vault.key**。
- 迁移只读源库，不会改动/删除 `synetra.db`；如不再需要可自行归档。

---

## API 概览

所有业务接口挂载于 **`/api`** 前缀；另提供 `GET /health` 存活探针。

| 模块 | 主要端点 |
| --- | --- |
| 认证 | `POST /api/auth/login`、`POST /api/auth/change-password`、`GET /api/monitor/system` |
| 连接 | `/api/connections`（CRUD、`/test`、`/sniff`、`/sample`） |
| 目录 | `GET /api/connections/{ck}/catalog` |
| 只读查询 | `POST /api/connections/{ck}/query`、`/api/connections/{ck}/debug` |
| 任务 | `/api/tasks`、`/api/tasks/{key}/history`、`/rollback`、`/preview`、`/api/wizard/prefill` |
| 流水线 | `GET /api/pipeline/steps`、`GET /api/tasks/{key}/pipeline`、`POST /api/tasks/{key}/nodes/{node_id}/preview`、`/snapshot` |
| 脚本 | `/api/scripts/generate`、`/handwritten`、`/{key}/dry-run`、`/llm-confirm`、`/sandbox`、`/current`、`/versions`、`/rollback` |
| 调度 | `POST /api/tasks/{key}/run`、`/schedule`、`/api/schedules`、`PATCH /api/schedules/{key}` |
| 监控 | `/api/runs`、`/api/runs/{id}/logs`、`/api/runs/{id}/steps`、`/metrics`、`/reconcile`、`/api/alerts` |
| 元数据/血缘 | `/api/metadata/infer`、`/api/metadata/tasks/{key}/lineage/*`、`/api/metadata/lineage` |
| LLM | `/api/llm/config`、`/api/llm/skills`、`/api/llm/tasks/draft`、`/api/llm/generations`、`/api/llm/expression` |
| 主数据 | `/api/mdm/models` |
| 项目 | `/api/projects` |
| 权限 | `/api/users`、`/api/projects/{project}/credentials`、`POST /api/trigger/{task_key}` |

**认证方式**

- **交互访问**：先 `POST /api/auth/login` 用用户名口令换**会话票据**，后续请求带 `Authorization: Bearer <票据>`。
  票据为 HMAC 签名的 `v1.<payload>.<sig>`，签名密钥按用户派生（改密即令旧票据失效），默认有效期 8 小时。
  服务端**不再信任任何客户端自称的身份**（`X-Synetra-User` 已移除），未携带/伪造/过期票据一律 `401`。
  签名密钥取 `SYNETRA_SESSION_SECRET`；未配置时在 `data_dir/.session_secret` 自动生成并持久化，
  **多实例部署必须显式配置同一密钥**，否则各实例签发的票据互不认可。
- **外部触发**：`Authorization: Bearer <服务凭据>`，凭据以 HMAC-SHA256 哈希入库，明文仅签发时返回一次，可吊销；
  该通道独立于用户会话，凭据仅能触发所属项目的任务。
- 角色门禁：所有业务接口默认要求至少只读身份，写操作要求开发者及以上，用户/LLM 配置/审计日志要求管理员。

---

## 多步清洗流水线

任务配置中的 `pipeline` 用「节点 + 连线」的有向无环图表达处理过程；存量的 `fields` 配置在读取时
等价编译为 `source → transform → target` 三节点流水线，**无需迁移**。

| 类别 | 步骤 |
| --- | --- |
| 源 | 关系库表、只读 SQL、CSV 文件、REST/API |
| 合并 | `union`（按列名对齐、缺失列补空、同名类型提升；不做跨源 join） |
| 行级 | 字段选择与映射（五种规则）、行过滤、类型转换、受限表达式、码值映射、空值填充、字符串处理、常量、序列号 |
| 整批 | 排序、去重、分组聚合（sum/count/min/max/avg/first/last） |
| 目标 | 关系库追加 / 主键 upsert、CSV 文件 |

典型编排（两源合并 → 映射 → 过滤 → 去重 → 聚合 → upsert）：

```mermaid
flowchart LR
  S1["source_table<br/>销售库.orders"] --> U["union<br/>按列名对齐"]
  S2["source_csv<br/>历史订单.csv"] --> U
  U --> SEL["select<br/>字段映射五规则"]
  SEL --> F["filter<br/>金额 > 0"]
  F --> D["unique<br/>主键去重"]
  D --> AGG["aggregate<br/>按客户汇总"]
  AGG --> T["target<br/>主键 upsert"]
```

步骤配置项、语义（空值/类型/溢出）与可粘贴示例见 **[docs/pipeline-steps.md](docs/pipeline-steps.md)**。

执行语义：

- **解释执行**：保存时只生成薄入口脚本（调用 `etl_sdk.run_pipeline`），多步逻辑由引擎按配置执行；
  `current/versions`、dry-run、人工确认语义不变，历史脚本与手写脚本继续可用。
- **分批流式**：源按 `batch_size` 分批读取，行级步骤逐批处理；排序/去重/聚合等整批步骤在内存分块排序，
  超阈值把有序块溢出到 `data_dir/spill` 再做多路归并，结果与纯内存路径一致。
- **可观测**：每次运行按步骤记录输入/输出/拒绝行数与耗时（运行详情 + 画布节点徽标）；失败自动把
  该步骤样本写到 `data_dir/runs/<run_id>/`（不写目标库、不建 staging 表），按份数清理。
- **增量与错误**：执行前注入已保存水位、成功后推进、失败保持原水位；步骤失败返回失败节点标识，
  已提交批次不回滚（upsert 模式下重跑幂等）。

> **升级注意**：本版本起**定时触发与手动触发共用真实执行通道**（此前定时触发实际走 dry-run，
> 会出现「成功但零写入」）；增量水位也会真实注入与推进。升级后建议先在日常库验证一次定时任务。
> 另：含 `pipeline` 的配置对旧版本不可读，回滚前请先导出任务配置。

## 测试与代码质量

**后端（pytest，全量 323 passed）**

```bash
cd services/backend
pytest                 # 运行 tests/
ruff check .           # 代码风格（line-length 100, target py310）

# 流水线的 MySQL 真实环境验收（表名前缀 it_epipe_，用例结束自动 DROP；无 MySQL 时自动跳过）
pytest tests/test_pipeline_mysql_integration.py -v
# 可用 SYNETRA_TEST_MYSQL_HOST/PORT/USER/PASSWORD/DB 覆盖连接
```

**前端端到端（Playwright，108 条用例全通过）**

```bash
cd services/frontend
npm run test:e2e          # headless，自动托管后端 :8000 与前端 :5173
npm run test:e2e:headed   # 可视化执行
```

- 覆盖 11 个页面 + 运行详情 / 流水线画布 / 数据预览三个模态，共 15 个 spec；用例强制指向隔离元数据库 `synetra_e2e`。
- 执行时自动留证整页截图（`reports/e2e/screenshots/`，已在 `.gitignore` 忽略），本文「界面预览」即取自此集合。
- 测试计划与执行报告见 [`doc/test-plan.md`](doc/test-plan.md) 与 [`doc/test-report.md`](doc/test-report.md)。

> 首次运行带 `needs_mysql` 标记的用例（连通性/只读查询/目标连接）前，执行一次
> `mysql -uroot -p < deploy/test-db-seed.sql`：创建 `synetra_test` 库、`synetra/synetra_pass`
> 账号与 25 行 `orders` 样例数据（仅授予该测试库权限，可重复执行）。

---

## 部署（Linux 一键）

`deploy/install.sh` 为**幂等可重跑**脚本，覆盖：系统用户创建 → 依赖检查 → 后端 venv 安装 → 前端构建 → 元数据库（MySQL，可选）→ 写入 `.env` → systemd 装配 → `/health` 自检。

```bash
sudo bash deploy/install.sh [--app-user <user>] [--web-user <user>] [--skip-frontend]
```

- 安装基目录：`/opt/synetra`（可通过 `INSTALL_BASE` 覆盖）。
- 装配 systemd 服务 `synetra-web.service`，`ExecStart` 运行 Uvicorn，`EnvironmentFile` 读取 `backend/.env`。
- 可选 MySQL 元库：设置 `MYSQL_ADMIN_USER` / `MYSQL_META_DB`（及 `MYSQL_HOST`、`MYSQL_META_DB_USER` / `_PASS`）后脚本会幂等建库；未提供则跳过、不阻塞部署。
- 非 root 运行时自动跳过用户创建与 systemd 装配，仅完成文件级部署（便于审阅/沙箱）。

启动与验证：

```bash
sudo systemctl start synetra-web
curl http://<host>:8000/health
```

---

## OpenSpec 规划文档

本仓库使用 [OpenSpec](https://github.com/) 的 spec-driven 工作流，中文书写、契约先行：

- `openspec/config.yaml` —— 项目上下文与 proposal/specs/design/tasks 规则。
- `openspec/specs/` —— 能力域规格：`platform-foundation`、`data-connections`、`etl-authoring`、`etl-pipeline`、`scheduling-execution`、`monitoring-reconciliation`、`metadata-lineage`、`access-control`、`master-data`、`etl-projects`、`ai-admin`、`quality-assurance`。
- `openspec/changes/archive/` —— 已归档变更，其中 `dataflow-v2` 为当前基线（proposal / design / tasks 均已闭环）；
  其上的 `etl-multistep-pipeline`（多步流水线）、`add-e2e-test-suite`（E2E 套件）、`fix-test-report-defects`（缺陷收敛）已全部归档。

新增需求请遵循流程：先产出 change（proposal → specs → design → tasks），
保证 proposal 每条能力可追溯到 specs 的 Requirement 与 tasks 的任务（无断链），实现完成后再 archive。

## 运行时数据与安全

- 元数据/日志/审计/血缘统一落在 MySQL（默认库 `synetra`，表结构在启动时幂等创建）。
- Fernet 密钥 `vault.key` 位于 `SYNETRA_DATA_DIR`（默认 `data/`）；仅当 `SYNETRA_METADATA_DB_DSN` 留空时才生成 SQLite 库 `synetra.db`。
- 会话票据签名密钥 `SYNETRA_SESSION_SECRET`，未配置时自动生成 `data_dir/.session_secret`；多实例部署必须共享同一密钥。
- 运行期临时数据同样落在 `data_dir`：整批步骤溢出目录 `spill/`、失败/手动样本快照 `runs/<run_id>/`（按 `SYNETRA_RUN_SNAPSHOT_KEEP` 清理）。
- 相关文件及 `services/backend/data/` 已被 `.gitignore` 忽略（`*.db`、`*.db-shm/-wal`、`*.key`、`.env`），**请勿提交入库**。
- 连接凭证以 Fernet 加密存储，明文不落库；请妥善保管 `vault.key`，一旦丢失则历史密文不可解密。
- 首次登录后请立即修改默认管理员密码。

---

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
