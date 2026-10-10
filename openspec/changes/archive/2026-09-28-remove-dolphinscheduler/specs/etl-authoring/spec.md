## MODIFIED Requirements

### Requirement: 手写路径低权限执行身份
系统 SHALL 让手写 Python 任务以非 root 的低权限用户身份、在受资源配额限制的隔离进程内运行，手写任务不得以 root 或平台服务账号运行；该隔离由平台自身提供，不依赖外部调度引擎或 worker group。

#### Scenario: 低权限运行
- **WHEN** 手写 Python 任务被执行
- **THEN** 进程以非 root 的低权限用户身份、在受资源配额限制的隔离环境中运行