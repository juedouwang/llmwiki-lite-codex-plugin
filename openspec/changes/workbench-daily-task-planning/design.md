## Context

真实服务已有 research_progress.py/.research-progress/tasks.json、科研进度任务排期、Markdown 笔记、独立报告页与局部导航。认可原型为 research-workbench-incremental-preview.html，旧内页不重做。

## Goals / Non-Goals

**Goals:** 一次落实用户确认的五项：增量前端、拆解 Skill、单一任务数据、共享程序接口、完成留痕和验收分离。

**Non-Goals:** 不做新数据库、后台推理进程、自动认定完成、自动改期、Git/笔记/报告内页重构或发布推送。

## Decisions

- 现有 tasks.json 是唯一任务存储。新增 parent_id、scheduled_date、可选 estimated_minutes（用于认可预览的行尾用时显示）和交付验收元数据；缺省兼容旧任务；更新采用 PATCH 和原有 revision/文件锁。
- 未关联项目的临时任务仅在既有 workspace 上下文使用同样的 tasks.json，不伪造注册项目。
- /daily 为全局页面；跨项目只读聚合，不改变当前项目。之前未完成可显式安排到选定日期，不偷偷挪期。
- GET /api/daily-tasks?date=YYYY-MM-DD；POST 同地址调用 daily_tasks.mutate。MCP llmwiki_daily_tasks_get、llmwiki_task_write 与 task_cli.py 调相同服务。助手入口固定 actor=agent，不允许验收/写 done。
- plan 使用 request_id 和单次存储写入创建目标与每日子任务。相同请求重试不重复创建，不同内容复用 key 拒绝。父目标不因子任务已完成被自动勾选。
- submit 保存实际交付摘要，状态待验收；用户 accept 才置 done。完成记录和关联 ID 需要幂等；旧进度页面完成操作走同一语义。
- 侧栏我的工作含每日待办、日报周报；项目区域包含选择器及全部原栏目。保留 125% 大小、主题、未保存保护和笔记 Ctrl+V。

## Risks / Trade-offs

- 文件存储沿用现有上限和锁，不引入第二套同步；多窗口旧 revision 返回冲突而不是覆盖。
- 全局读取可能遇到缺失项目目录，必须明确报错/告警，不静默改数据。
- 前端和接口验证全部使用临时注册项目，不向真实科研内容添加测试任务。
- Skill 需要宿主刷新后发现；源码 CLI 可直接调用，不声称安装缓存已经同步。
