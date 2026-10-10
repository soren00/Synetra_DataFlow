## Purpose

定义数据源与数据目标的连接管理能力，覆盖主流关系库、CSV、REST/API 数据源，以及关系库、对象存储、Hive、ClickHouse、API 数据目标；并提供面向任务配置的 Web 只读查询/接口调试能力与 API 目标推送语义。

## ADDED Requirements

### Requirement: 支持的数据源类型
系统 SHALL 支持 MySQL、Oracle、SQL Server、PostgreSQL、CSV 文件、REST/API 作为数据源。

#### Scenario: 配置关系库数据源
- **WHEN** 用户新增数据源并选择 MySQL、Oracle、SQL Server、PostgreSQL 之一
- **THEN** 系统保存连接配置并可测试连通性

#### Scenario: 配置 API 数据源
- **WHEN** 用户新增数据源并选择 REST/API 类型
- **THEN** 系统保存请求地址、认证与参数配置并可用于后续拉取数据

### Requirement: 支持的数据目标类型
系统 SHALL 支持关系库、对象存储、Hive、ClickHouse 与 API 作为数据目标。

#### Scenario: 配置 API 数据目标
- **WHEN** 用户新增数据目标并选择 API 类型
- **THEN** 系统保存推送端点与认证配置，用于将同步结果推送到 API

### Requirement: 连接凭证安全
系统 SHALL 通过 conn_key 指向安全存储管理连接凭证，不将明文凭证内联进任务配置或写入版本库。

#### Scenario: 凭证脱敏存储
- **WHEN** 用户保存包含口令或密钥的连接配置
- **THEN** 系统仅保存 conn_key 引用，明文凭证不进入配置快照、脚本或版本记录

### Requirement: 连接测试
系统 SHALL 支持对数据源与数据目标执行连接测试，并返回成功或失败原因。

#### Scenario: 连接测试成功
- **WHEN** 用户对已配置连接执行测试
- **THEN** 系统返回连接成功或明确的失败原因

### Requirement: 关系库只读查询
系统 SHALL 支持用户在 Web 界面对已授权的关系库连接发起只读 SELECT 查询并分页预览结果，查询强制行数上限与执行超时。

#### Scenario: 只读查询返回结果
- **WHEN** 用户对已配置关系库提交 SELECT 查询
- **THEN** 系统在权限校验通过后执行查询，分页返回不超过行数上限的结果集

#### Scenario: 拒绝写操作与多语句
- **WHEN** 用户提交的语句包含非 SELECT、DDL/DML 或多条语句
- **THEN** 系统在执行前拒绝该语句并提示仅允许只读单语句查询

### Requirement: REST/API 请求调试
系统 SHALL 支持用户对已配置的 REST/API 数据源发起请求调试，查看返回样例，并可将样例字段送入字段选择向导。

#### Scenario: 调试 API 数据源
- **WHEN** 用户基于连接配置发起一次 API 请求（可临时覆盖方法、头与参数）
- **THEN** 系统返回响应样例与状态，并可从样例字段直接发起字段选择向导

### Requirement: 查询审计与边界
系统 SHALL 对所有只读查询与 API 调试操作执行项目权限校验并记录审计日志；查询结果首期不持久化、不提供导出。

#### Scenario: 查询操作留痕
- **WHEN** 用户执行关系库查询或 API 请求调试
- **THEN** 系统校验其项目访问权限并记录操作者、时间、连接与查询/请求摘要

### Requirement: API 目标推送语义
系统 SHALL 按配置的目标端点、认证、超时与重试向 API 目标推送同步结果；推送幂等性由目标端点约定，平台不保证幂等，并需在配置中显式告知。

#### Scenario: 推送到 API 目标
- **WHEN** 同步任务以 API 为目标执行加载
- **THEN** 系统按配置将结果推送到目标端点，超时或失败时按重试策略处理

#### Scenario: 幂等限制告知
- **WHEN** 用户配置 API 数据目标
- **THEN** 系统明确提示平台不保证推送幂等，需由目标端点自行保证
