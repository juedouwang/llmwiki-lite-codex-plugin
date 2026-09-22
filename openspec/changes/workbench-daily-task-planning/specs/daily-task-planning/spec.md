## Purpose

让科研人员在现有工作台中直接查看跨项目每日安排，用对话将科研目标拆为可执行子任务，并保证任务数据唯一、交付和人工验收清楚分离，继续使用现有笔记和报告功能。

## ADDED Requirements

### Requirement: Incremental workbench navigation
系统 MUST 仅新增每日待办和我的工作/项目分组，保持旧内页与交互，日报周报采用叠页图标。

#### Scenario: Retain existing features
- **WHEN** 从每日待办进入科研记录、报告、代码或其他旧栏目
- **THEN** 原页面组件和原操作继续可用，项目选择不把全局栏目变为项目过滤页

### Requirement: Shared dated tasks
系统 MUST 让科研进度子任务与每日待办同用任务 ID 和原任务存储，并支持无项目临时待办。

#### Scenario: Edit through either view
- **WHEN** 在任一入口修改子任务名称、日期或完成状态
- **THEN** 另一视图重新读取相同 ID 后显示一致结果，没有同步副本

#### Scenario: Previous unfinished tasks
- **WHEN** 切换到未来或过去日期
- **THEN** 按该日期展示安排与之前未完成；仅显式安排到这天才修改原日期

### Requirement: Goal planning skill and reusable API
系统 MUST 提供宿主推理的任务拆解 Skill，网页/MCP/CLI 共用确定性服务，创建父目标和每日子任务必须原子且可重试。

#### Scenario: Arrange a weekly goal
- **WHEN** 用户授权把某科研目标安排到本周，助手提交合法日期的每日产出
- **THEN** 科研进度显示目标和子任务排期，每日待办按日聚合，不要求临时编写脚本

#### Scenario: Repeated plan call
- **WHEN** 同一 request_id、相同内容重试或含无效子任务的计划被提交
- **THEN** 重试不产生重复任务，无效计划不部分写入

### Requirement: Delivery is not acceptance
系统 MUST 区分助手交付和用户完成验收；交付与完成可追溯到科研记录，不从文件变化推断成果。

#### Scenario: Assistant delivery
- **WHEN** 助手提交实际完成摘要
- **THEN** 任务保持未 done 并显示待你验收，生成关联交付记录；助手调用完成/验收或改写受保护状态被拒绝

#### Scenario: User acceptance
- **WHEN** 用户在每日待办或原科研进度确认完成
- **THEN** 同一任务进入 done，完成留痕可查看；重复提交不重复创建同一阶段记录
