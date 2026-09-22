## Why

用户需要把科研目标变成每日可执行安排，并在统一工作台查看、交付和验收。沿用已认可的现有页面，避免重新设计和重复任务副本。

## What Changes

- 仅增加每日待办和侧栏“我的工作 / 项目”分组，日报周报使用叠页图标。
- 新增目标拆解 Skill，通过稳定 MCP/CLI 写入父目标及按日期安排的子任务。
- 每日待办与科研进度使用同一任务 ID、同一文件，不复制同步。
- 网页和助手共用服务；助手交付待验收，用户确认才完成，自动保存科研记录。
- 保留 Git、笔记、报告、知识库、文献内页及其原交互；不新增后台模型、定时器或外部依赖。

## Capabilities

### New Capabilities
- `daily-task-planning`: 跨项目每日待办、目标拆解、共享任务入口及交付验收。

### Modified Capabilities

无（当前主 specs 为空；旧 change 的独立未完成任务不在本次范围内）。

## Impact

扩展 research_progress.py 现有任务存储，增加 daily_tasks 共享服务、每日页面和 Skill/CLI，接入现有 web_server/MCP。兼容旧任务，不迁移科研资料、不改真实项目数据、不提交或推送代码。
