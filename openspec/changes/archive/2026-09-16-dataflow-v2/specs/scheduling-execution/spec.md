## Purpose

定义任务的调度与执行能力，包括 DolphinScheduler 定时调度、平台对外 REST API 异步触发同步（含凭据鉴权与错误语义）、版本回滚、重试与失败告警。

## ADDED Requirements

### Requirement: DolphinScheduler 定时调度
系统 SHALL 通过 DolphinScheduler 按用户配置的时间与间隔定时执行 ETL 任务。

#### Scenario: 定时触发
- **WHEN** 任务配置了定时时间与间隔
- **THEN** DolphinScheduler 按计划触发 worker 执行对应 Python 脚本

### Requirement: REST API 异步触发同步
系统 SHALL 对外暴露经过鉴权的 REST API，供外部系统触发数据同步；触发为异步语义，校验通过后立即返回触发回执（触发ID、任务ID、项目与触发时间），不同步等待执行完成，执行结果通过监控能力查询。

#### Scenario: 鉴权并触发成功
- **WHEN** 外部系统携带有效 API 凭据调用触发接口且任务属于凭据授权项目
- **THEN** 系统调用调度引擎触发任务并返回触发回执，调用事件写入审计日志

#### Scenario: 凭据无效或缺失
- **WHEN** 请求未携带凭据或凭据无效/已吊销
- **THEN** 系统拒绝请求并返回鉴权失败错误，不触发任何任务

#### Scenario: 任务不存在或越权
- **WHEN** 凭据有效但目标任务不存在或不属于凭据授权项目
- **THEN** 系统返回明确的错误响应且不触发任务，尝试事件写入审计日志

### Requirement: 版本管理与回滚
系统 SHALL 保留任务配置与脚本的历史版本，并支持回滚到指定版本。

#### Scenario: 回滚到历史版本
- **WHEN** 用户选择历史版本执行回滚
- **THEN** 系统恢复该版本对应的配置与脚本为当前生效版本

### Requirement: 重试机制
系统 SHALL 支持用户配置失败重试次数与间隔；不支持断点续传与幂等保证（API 目标的幂等限制另行明示）。

#### Scenario: 失败重试
- **WHEN** 任务执行失败且配置了重试
- **THEN** 系统按配置次数与间隔重试，并记录最终失败结果

### Requirement: 失败邮件告警
系统 SHALL 在任务失败时发送邮件告警。

#### Scenario: 失败通知
- **WHEN** 任务最终失败
- **THEN** 系统向配置的接收人发送邮件告警
