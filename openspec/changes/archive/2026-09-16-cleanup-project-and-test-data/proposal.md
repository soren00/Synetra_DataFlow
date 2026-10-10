## Why

Synetra_DataFlow 经历了多次开发迭代（dataflow-v2 全量实现、登录/监控、用户管理、真实 MySQL ETL 验证等）与多次测试会话。当前仓库积累了大量**仅服务开发与测试、对交付产品无价值**的内容：

- 文档侧：`doc/项目情况与需求概览.md` 已被 openspec 基线明确取代（自标"已部分过时"）；`doc/功能测试与真实ETL验证Spec.md` 是一次性测试会话记录，不是产品需求/架构文档。
- 数据侧：管理库 `synetra.db` 中污染着测试/演示数据——16 条 demo/smoke 连接、demo-etl-orders 演示脚本、测试产生的任务/运行历史/血缘/审计占位、以及 4 个账号（admin/dev/bob/syp）。
- 代码侧：存在未被引用的冗余文件（前端 `src/data/mock.js`、后端 `data/scripts/demo-etl-orders/`）与构建/缓存产物（`synetra_backend.egg-info/`、`.ruff_cache/`、`__pycache__/` 等）。
- 初始账号：`main.py` 的 `_bootstrap_users` 会种子化 developer 演示账号 `dev`，与新交付形态不符。

整理后项目应只保留交付所需文档、代码与数据：**账号仅保留 admin**，测试/演示数据清除，冗余文档与产物清除。

## What Changes

- 账号基线：**BREAKING** 移除 `dev` 开发者演示账号的种子化；应用首次启动仅创建一个 `admin` 管理员账号。受 `dataflow-v2` 7.1"管理员/开发者/只读"角色体系约束的用户管理能力本身不改变。
- 数据清理：从管理的 SQLite 库中清除全部测试/演示数据（demo/smoke 连接及其关联 secret/服务账号、演示脚本、测试生成的任务/运行历史/血缘/审计占位），账号保留且仅保留 `admin`。
- 文档整理：移除已被 openspec 取代的 `doc/项目情况与需求概览.md`；移除一次性测试会话记录 `doc/功能测试与真实ETL验证Spec.md`；修订 `README.md` 使其指向唯一基线来源。
- 代码整理：删除未被引用的 `services/frontend/src/data/mock.js` 与演示脚本目录 `services/backend/data/scripts/demo-etl-orders/`；清理构建产物 `synetra_backend.egg-info/` 与各类缓存目录；完善 `.gitignore`。
- OpenSpec 结构：将清理行动整理为独立 change（本 change），采用 delta 规格，不对既有 8 个能力域规格做破坏性改写。

## Capabilities

### New Capabilities

无全新能力域；本 change 在既有 `access-control` 与 `platform-foundation` 能力域上追加 ADDED delta 需求。

### Modified Capabilities

- `access-control`: 新增"默认账号（仅 admin）"需求——系统 SHALL 在首次启动/安装时仅创建 admin 管理员账号，不创建演示/开发者账号。
- `platform-foundation`: 新增"出厂清洁态"需求——系统 SHALL 的托管数据在清理/重置后不含测试/演示数据，交付仓库不携带演示脚本与未引用 mock 数据。

## Impact

- Web 后端（FastAPI）：`app/main.py` 的启动账号种子化逻辑收敛为仅 admin；登录/用户管理接口行为不变（受影响演示账号不能以 dev 登录）。
- 托管数据库（SQLite）：清空测试/演示数据，仅保留 admin 账号；首次运行的既有测试（pytest）需自建数据，不依赖预置演示数据。
- 文档目录：`doc/` 中两份过时/会话类文档删除，`README.md` 修订。
- 仓库卫生：删除未引用的前端 mock 与后端演示脚本、构建产物与缓存；`.gitignore` 补全，避免污染再次进入版本库。
- 保留项：`.trae/skills`、`.agents/skills` 为工具运行器管理的技能目录，非交付代码，不纳入本次删除。