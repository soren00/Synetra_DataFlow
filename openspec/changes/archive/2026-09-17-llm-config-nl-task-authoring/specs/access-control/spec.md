# Access Control（增量能力）

## Purpose

本 delta 定义 LLM 配置管理的权限边界：仅管理员可读写 LLM 配置（供应商/模型/API Key），开发者与只读角色不可查看或修改。

## Requirements

### Requirement: LLM 配置管理员专属
系统 SHALL 仅允许管理员角色读写 LLM 配置；非管理员访问配置读写接口返回 403。对话式建任务仍沿用开发者角色权限（与任务保存一致）。

#### Scenario: 管理员读写配置
- **WHEN** 管理员访问 LLM 配置的查询、保存、测试或吊销接口
- **THEN** 操作正常执行并返回对应结果

#### Scenario: 非管理员被拒
- **WHEN** 开发者或只读角色访问 LLM 配置读写接口
- **THEN** 系统返回 403，配置内容与明文凭据不可见

## (本 delta 不修改其他未涉及需求)
