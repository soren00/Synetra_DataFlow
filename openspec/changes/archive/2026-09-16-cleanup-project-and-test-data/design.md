## 目标

将项目收敛为干净交付形态：账号仅保留 admin、测试/演示数据清除、冗余文档/代码/产物清除，并让 `_bootstrap_users` 的默认行为与新基线一致。

## 范围与保留边界

- **删除**：`doc/项目情况与需求概览.md`、`doc/功能测试与真实ETL验证Spec.md`、`services/frontend/src/data/mock.js`、`services/backend/data/scripts/demo-etl-orders/`、`services/backend/synetra_backend.egg-info/` 及各类缓存（`.ruff_cache/`、`__pycache__/`、`.pytest_cache/`）。
- **保留**：`.trae/skills/` 与 `.agents/skills/` 为工具运行器托管，非交付代码；`doc/` 其他项与 openspec 主规格为交付基线。
- **修改**：`app/main.py`（种子化仅 admin）、`README.md`、`.gitignore`。

## 决策

### D1：账号收敛方式 —— 应用侧截断 + 启动种子化收敛为仅 admin
在 `app/main.py::_bootstrap_users` 中，将种子集合从 `{"admin": "admin", "dev": "dev"}` 收敛为仅创建 `admin`（角色 admin）。对当前 `synetra.db` 内的存量账号（admin/dev/bob/syp）执行一次数据清理，仅保留 `admin`。

- **理由**：交付形态应只含 `admin`；同时关闭"每次启动都可能补种 `dev`"的回归源。
- **被否决的备选**：仅清理数据而不改 `_bootstrap_users`。否决原因：下一次启动会重新种子化 `dev`，无法收敛为"仅 admin"，违反 target 状态。

### D2：测试/演示数据清理 —— 重置托管库至清洁态
采用清理动作删除全部测试/演示数据：清空 connection/secret/service_credential/task/task_history/run_record/run_log/run_metric/reconciliation/table_metadata/lineage/mdm_model/mdm_record/mdm_change 等表中与测试/演示相关的记录，并将 user_account 收敛为仅 admin；同时删除演示脚本目录。

- **理由**：一次性清理比逐个甄别更彻底、可审计；仓库本源即干净，交付即清洁态。
- **被否决的备选**：保留表结构但仅删除 `project=demo` 相关行。否决原因：`data/scripts/demo-etl-orders/`、大批测试占位（run/audit/mdm）与"测试账号"并非都以 demo 标记，逐个甄别易漏、成本高于收益；目标状态是干净交付，而非部分保留。

### D3：文档清理
删除被 openspec 明确取代的 `项目情况与需求概览.md` 与一次性测试会话记录 `功能测试与真实ETL验证Spec.md`；修订 `README.md` 指向 `openspec/changes/dataflow-v2/` 为唯一基线来源。

- **理由**：过期/会话类文档会造成信息歧义；README 保持为唯一入口索引。
- **被否决的备选**：仅更新过时声明继续保留。否决原因：文档内容已被基线取代且无交付价值，保留只会持续增加维护与歧义成本。

### D4：冗余代码/产物清理与忽略规则
删除未引用的 `services/frontend/src/data/mock.js`、构建产物 `synetra_backend.egg-info/`、缓存目录；在 `.gitignore` 追加对数据文件、密钥与缓存的保护，避免再次入库。

- **理由**：仓库来自 `mock.js` 未被任何模块引用，删除不影响构建；egg-info/缓存为可再生产物，删除并忽略减少噪声与安全风险（如 `vault.key` 不应入库）。
- **被否决的备选**：原样保留并仅 gitignore。否决原因：`mock.js`、`demo-etl-orders` 为未引用/演示内容，本应属于不可见实现细节，保留无益。

## 数据清理目标态（验收锚点）

- `user_account`：仅 `admin`（1 行）。
- `connection`、`task`、`run_record`、`run_log`、`run_metric`、`lineage`、`table_metadata`、`mdm_model/mdm_record/mdm_change`、`audit_log`、`service_credential`、`secret`：测试/演示记录清零。
- 脚本存储目录：不含 `demo-etl-orders`。

## Open Questions

- 无阻塞项。一次性清理与后续"首次启动仅含 admin"为单一交接点；测试能否通过取决于测试是否自建数据而非依赖预置演示数据（已确认 test_access 等自建用户，预期不受影响）。