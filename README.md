# Synetra_DataFlow

> Web 化 ETL 数据同步与任务调度平台 —— 面向懂 ETL / 数仓的数据工程师。

Synetra_DataFlow 把数据同步从「写脚本」升级为「可用、可管、可追溯」的 Web 工作台：
在浏览器里完成连接管理、只读查询、任务编排、定时调度、运行监控、血缘治理与主数据管理。
平台提供两条转换路径 —— **LLM + Python**（脚手架生成 + 静态校验 + 人工确认）与**手写 Python**（低权限安全沙箱），
以自研 `etl_sdk` 作为抽取/加载执行内核，契约冻结、可插拔扩展。

---

## 核心特性

- **连接管理**：统一纳管关系库（MySQL / PostgreSQL / Oracle / SQLServer）、CSV、REST/API 等数据源与目标；支持连通性测试、结构嗅探（sniff）与数据抽样（sample）。
- **只读查询工作台**：面向数据工程师的 SQL 查询与调试体验，内置语句校验与结果浏览。
- **ETL 项目 / 任务编排**：字段映射、增量水位、主键 upsert，可视向导（wizard）预填。
- **两条转换路径**：
  - LLM + Python：自然语言描述 → 生成脚本 → dry-run → 静态校验 → 人工确认落地。
  - 手写 Python：低权限身份、最小凭证、网络白名单、资源配额四重安全控制。
- **定时调度与触发**：自研 `LocalScheduler` 轮询 cron 触发任务，支持失败重试（次数/间隔可配）与外部 `Bearer` 凭据触发。
- **运行监控与对账**：运行记录、日志、指标、对账（reconcile）与告警（邮件 SMTP，未配置时降级记日志）。
- **元数据与血缘**：字段级血缘推断与图谱，按转换路径分级展示。
- **主数据管理（MDM）**：主数据模型维护。
- **权限与凭据**：基于角色的访问控制（admin / developer / readonly），连接凭证 Fernet 加密入库，API 凭据可签发/吊销。
- **AI 管理**：LLM 多供应商抽象（默认 DeepSeek），调用前强制脱敏，RAG 注入 `etl_sdk` 文档与技能包。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.10+ · FastAPI · Uvicorn · Pydantic Settings |
| 执行内核 | 自研 `etl_sdk`（抽取/转换/加载，`SyncConfig` 为契约冻结点） |
| 调度 | 自研 `LocalScheduler`（轮询 + cron 匹配 + 失败重试） |
| 存储 | MySQL 8（元数据/日志/审计/血缘，默认 `mysql://root:***@127.0.0.1:3306/synetra`）· DSN 留空时回退 SQLite |
| 加密 | `cryptography`（Fernet，Vault 密钥） |
| LLM | 供应商无关接入层，默认 DeepSeek，RAG + 脱敏 |
| 前端 | React 18 · Vite 5 · 自研 macOS 设计语言组件（无第三方 UI 库） |
| 部署 | Linux 一键脚本（`deploy/install.sh`）+ systemd |

## 目录结构

```
synetra/
├── services/
│   ├── backend/            FastAPI 后端
│   │   ├── app/
│   │   │   ├── api/         REST 路由（auth / connections / catalog / query / tasks /
│   │   │   │               scripts / schedule / monitor / metadata / llm / mdm /
│   │   │   │               projects / access）
│   │   │   ├── core/       配置（Settings）
│   │   │   ├── db.py       数据库抽象层（MySQL/SQLite 双后端与方言适配）
│   │   │   ├── etl_sdk/    抽取/加载运行时（契约冻结点）
│   │   │   ├── scripting/  dry-run 执行通道（etl_runtime）
│   │   │   ├── schedule/   自研 LocalScheduler
│   │   │   ├── llm/        LLM 接入层 + RAG + 技能包
│   │   │   ├── metadata/   元数据/血缘
│   │   │   ├── monitor/    运行监控/对账
│   │   │   ├── alerting/   告警
│   │   │   ├── queries/    只读查询
│   │   │   ├── sources/    数据源连接器
│   │   │   ├── mdm/        主数据
│   │   │   ├── models/     领域模型
│   │   │   ├── security/   权限/凭据/沙箱
│   │   │   ├── store.py    元数据存储层（MySQL 默认 / SQLite 回退）
│   │   │   ├── auth.py     认证/授权
│   │   │   ├── vault.py    Fernet 密钥管理
│   │   │   └── main.py     FastAPI 入口 + /health
│   │   ├── tests/          pytest 测试
│   │   ├── scripts/        运维脚本（SQLite → MySQL 元数据迁移）
│   │   ├── pyproject.toml
│   │   └── .env.example
│   └── frontend/           React 前端
│       ├── src/
│       │   ├── pages/      页面（总览/连接/查询/项目/AI任务/定时/血缘/监控/MDM/权限/AI管理/登录）
│       │   ├── components/ 自研 UI 组件
│       │   ├── api/        API 客户端与会话
│       │   └── theme/      设计 token 与主题
│       └── vite.config.js  dev 5173，代理 /api、/health → 127.0.0.1:8000
├── deploy/                 一键部署脚本（install.sh）
├── openspec/               OpenSpec 规划（specs 能力域 + changes/archive 归档变更）
├── data/                   运行时数据（vault.key、脚本产物；SQLite 回退时的 synetra.db）
└── doc/                    文档目录
```

> `openspec/` 是需求/设计/规格的唯一权威来源：`openspec/specs/` 为能力域规格，
> `openspec/changes/archive/` 保存已归档的变更（含作为当前基线的 `dataflow-v2`）。

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
# {"status":"ok","service":"synetra-backend","version":"0.1.0"}
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
| `SYNETRA_DATA_DIR` | `data` | 运行时数据目录（Vault 密钥、脚本产物、SQLite 回退库） |
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
| 脚本 | `/api/scripts/generate`、`/handwritten`、`/{key}/dry-run`、`/llm-confirm`、`/sandbox`、`/current`、`/versions`、`/rollback` |
| 调度 | `POST /api/tasks/{key}/run`、`/schedule`、`/api/schedules`、`PATCH /api/schedules/{key}` |
| 监控 | `/api/runs`、`/api/runs/{id}/logs`、`/metrics`、`/reconcile`、`/api/alerts` |
| 元数据/血缘 | `/api/metadata/infer`、`/api/metadata/tasks/{key}/lineage/*`、`/api/metadata/lineage` |
| LLM | `/api/llm/config`、`/api/llm/skills`、`/api/llm/tasks/draft`、`/api/llm/generations`、`/api/llm/expression` |
| 主数据 | `/api/mdm/models` |
| 项目 | `/api/projects` |
| 权限 | `/api/users`、`/api/projects/{project}/credentials`、`POST /api/trigger/{task_key}` |

**认证方式**

- **交互访问**：请求头 `X-Synetra-User` 携带用户名，服务端据此解析角色（`admin` / `developer` / `readonly`）。
- **外部触发**：`Authorization: Bearer <token>`，凭据以 HMAC-SHA256 哈希入库，明文仅签发时返回一次，可吊销。

---

## 测试与代码质量

```bash
cd services/backend
pytest                 # 运行 tests/
ruff check .           # 代码风格（line-length 100, target py310）
```

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
- `openspec/specs/` —— 能力域规格：`platform-foundation`、`data-connections`、`etl-authoring`、`scheduling-execution`、`monitoring-reconciliation`、`metadata-lineage`、`access-control`、`master-data`、`etl-projects`、`ai-admin`。
- `openspec/changes/archive/` —— 已归档变更，其中 `dataflow-v2` 为当前基线（proposal / design / tasks 均已闭环）。

新增需求请遵循流程：先产出 change（proposal → specs → design → tasks），
保证 proposal 每条能力可追溯到 specs 的 Requirement 与 tasks 的任务（无断链），实现完成后再 archive。

## 运行时数据与安全

- 元数据/日志/审计/血缘统一落在 MySQL（默认库 `synetra`，表结构在启动时幂等创建）。
- Fernet 密钥 `vault.key` 位于 `SYNETRA_DATA_DIR`（默认 `data/`）；仅当 `SYNETRA_METADATA_DB_DSN` 留空时才生成 SQLite 库 `synetra.db`。
- 相关文件及 `services/backend/data/` 已被 `.gitignore` 忽略（`*.db`、`*.db-shm/-wal`、`*.key`、`.env`），**请勿提交入库**。
- 连接凭证以 Fernet 加密存储，明文不落库；请妥善保管 `vault.key`，一旦丢失则历史密文不可解密。
- 首次登录后请立即修改默认管理员密码。

---

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
