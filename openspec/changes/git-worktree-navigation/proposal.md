## Why
已有多个 Git 工作树，但代码页只能切换当前目录的分支。用户需要在同一科研项目里进入已有工作树，而不是重复注册项目或强制切换被占用分支。

## What Changes
- 多工作树项目增加“选择工作树”，显示目录、分支和当前位置。
- 被其他工作树占用的分支保留禁用检出，旁边增加“进入所在工作树”。
- 选中目录的读写、预览、合并状态相互隔离，保留共享仓库锁和确认流程。
- 不创建工作树、不自动合并 main、不修改项目注册位置。

## Capabilities
### New Capabilities
- `git-worktree-navigation`: 同项目已有工作树导航与操作上下文。
### Modified Capabilities
无。

## Impact
Git HTTP 适配器、代码页与导航、README、网页 Skill、临时仓库回归测试。无新增依赖，无数据迁移。
