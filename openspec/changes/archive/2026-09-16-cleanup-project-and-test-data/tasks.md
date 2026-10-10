> 分期：全部为 [P1] 一次性交付整理。先做账号与数据收敛（T1–T3），再清理文档与代码（T4–T6），最后回归验证（T7）。

## 1. 账号收敛为仅 admin [P1]

- [x] **1.1 修改启动账号种子化（app/main.py）**；验证：`_bootstrap_users` 仅当 `admin` 缺失或密码为空时创建 admin；不再引用 `dev`；代码语义与 `dataflow-v2` 7.1 的"管理员/开发者/只读"角色体系兼容（仅默认账号集合变化，角色枚举不变）
- [x] **1.2 清空存量测试账号**；验证：对 `synetra.db` 执行清理后 `user_account` 仅剩角色为 admin 的一个账号（保留 admin）；登录 admin 成功、其余旧账号登录返回 401

## 2. 测试/演示数据清除 [P1]

- [x] **2.1 清除托管库测试/演示数据**；验证：清理后 connection/task/run_record/run_log/run_metric/reconciliation/table_metadata/lineage/mdm_model/mdm_record/mdm_change/audit_log/service_credential/secret 中无项目=demo 或测试产生的记录（计数为 0 或仅保留真实交付数据）
- [x] **2.2 删除演示脚本目录**；验证：`services/backend/data/scripts/demo-etl-orders/` 目录被删除，脚本存储路径中不再含 demo 脚本

## 3. 首次启动清洁态验证 [P1]

- [x] **3.1 空数据目录首次启动仅含 admin**；验证：清空 `data/synetra.db*` 与 `data/vault.key` 后启动应用，`user_account` 仅含 admin；connection/task/run 等表为空；对应 access-control 与 platform-foundation 的 Scenario 全部满足

## 4. 文档整理 [P1]

- [x] **4.1 删除过时/会话类文档**；验证：`doc/项目情况与需求概览.md` 与 `doc/功能测试与真实ETL验证Spec.md` 被删除，`doc/` 剩余项均为有效交付/基线文档
- [x] **4.2 修订 README.md**；验证：README 的基线来源指向 `openspec/changes/dataflow-v2/`，目录结构与删除后的实际结构一致，不含对已删文档的引用

## 5. 冗余代码与产物清除 [P1]

- [x] **5.1 删除未引用与构建产物**；验证：`services/frontend/src/data/mock.js`、`services/backend/synetra_backend.egg-info/`、`.ruff_cache/`、`__pycache__/`、`.pytest_cache/` 被清理；前端项目在删除 mock.js 后仍可正常构建，后端可正常导入运行
- [x] **5.2 完善 .gitignore**；验证：`.gitignore` 覆盖数据文件（`data/*.db*`）、密钥（`data/*.key`）与缓存/产物目录，避免后续污染入库

## 6. 仓库卫生核查 [P1]

- [x] **6.1 全库冗余复查**；验证：在 services/backend 与 services/frontend 中运行一遍 grep，确认不存在对已删 mock/演示/文档路径的引用；残留未引用文件被清理

## 7. 回归验证 [P1]

- [x] **7.1 后端与前端回归**；验证：`pytest` 全量通过（既有测试自建数据，不依赖被删演示/预置账号）；`ruff` 检查干净；前端 `vite build` 成功且产物可用