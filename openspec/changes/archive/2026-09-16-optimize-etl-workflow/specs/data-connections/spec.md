# Data Connections （增量能力）

## Purpose

本 delta 定义连接相关的能力增强：双向（source/target/both）连接方向、只读查询真实数据展示、Postman 式 REST/API 连接配置录入。

## Requirements

### Requirement: 双向连接方向
系统 SHALL 支持连接方向枚举 `source | target | both`；方向为 `both` 的连接在新建 ETL 任务时既可作为数据源亦可作为数据目标被选择。

#### Scenario: 双向连接可作源或目标
- **WHEN** 用户在连接管理中把某连接方向设为「双方（both）」
- **THEN** 该连接同时出现在「数据源」与「数据目标」的可选项中，可在同一任务中或跨任务被任一角色选用

#### Scenario: 单向连接保持原语义
- **WHEN** 用户保留连接的 source 或 target 方向
- **THEN** 该连接仅在其对应方向的任务下拉中可见，存取行为与既有版本一致

### Requirement: 只读查询展示真实数据
系统 SHALL 在只读查询页与 ETL 任务数据预览中以「行按列顺序对应取值」的方式渲染关系库查询结果，单元格显示实际数据值而非统一占位符。

#### Scenario: 关系库查询结果展示
- **WHEN** 用户对关系库连接执行 SELECT 并查看结果表格
- **THEN** 每个单元格显示该行该列的真实值（数值/字符串/空值按语义展示），不再统一显示横线占位符

#### Scenario: API 样例结果展示
- **WHEN** 用户查看 REST/API 连接的样例数据
- **THEN** 系统按对象键值渲染字段值，保持既有展示语义

### Requirement: Postman 式 REST/API 连接配置
系统 SHALL 在 REST/API 连接的配置表单中提供结构化录入：HTTP 方法以下拉选择、认证方式以下拉选择，并支持 Query Params 与 Headers 的键值对列表编辑，而非仅自由文本输入。

#### Scenario: 下拉选择方法与认证
- **WHEN** 用户配置 REST/API 连接
- **THEN** HTTP 方法在 GET/POST/PUT/DELETE 等选项中选择，认证方式在 none/bearer/basic 中选择，不依赖手输

#### Scenario: 键值对结构化录入
- **WHEN** 用户为 REST/API 连接补充请求
- **THEN** 系统允许以键值对形式维护 Query Params 与 Headers，保存后用于样例拉取与只读调试

## (本 delta 不修改其他未涉及需求)