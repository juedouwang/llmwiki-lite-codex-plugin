---
name: llmwiki-web
description: Start and use the Chinese-first local LLM Wiki research cockpit. Use when the user asks to visualize Wiki Markdown, browse research projects, search papers/methods/experiments/results, or change Wiki storage locations from the web interface.
---
# 中文科研知识工作台

The website is a simple loopback visualization and interaction layer, not a second knowledge engine.

1. Call `llmwiki_web_start` with optional `home` or `port` when the user asks to open the site.
2. Return the URL from the tool. It binds to `127.0.0.1` only.
3. If a shell launch is needed, run `python -I -B <plugin>/scripts/web_server.py`; keep the host loopback.

The Chinese-first site provides:

- 一个共享侧栏：项目、搜索、当前项目的科研记录/文献/知识库/科研进度，以及设置；
- 项目首页与知识页采用单列列表，不重复展示仪表盘、统计卡和维护建议；
- 文献使用单列表与搜索/类型/阅读状态/收藏筛选，配对笔记及添加指令按需展开；
- 原文阅读与中文精读对照阅读，小屏自动改为单列；
- 科研记录按日期分组；手动笔记保留加号插块、图片、批注和自动保存；
- Markdown 阅读页只有正文与折叠目录，页面操作在“更多”；
- 存储设置按需展开，高级路径默认收起，添加项目链接可直接展开对应表单。

这是本地网页，不提供公网部署或远程同步。不要为了远程访问将监听地址改为公网地址。

Paper files are served read-only from the registered `source_root` through an extension allowlist and path/symlink checks. PDFs use browser-inline streaming with byte ranges; non-PDF formats are downloaded/opened without executing source HTML. Assistant-reading Markdown from the source project is read-only, while durable notes should be stored in `wiki_root` with an exact `paper_file` binding.
The interface does not create fixed template pages or change real Markdown paths. Existing English paper titles, algorithm names, code, paths, commands, API names, and other precision-sensitive technical terms remain unchanged.

Storage changes copy existing content by default, refuse unsafe non-empty destination merges, update the registry, and never delete old directories. The website does not edit arbitrary Markdown bodies; Markdown remains the knowledge source maintained by the AI assistant and the user.

## 手动科研笔记

在“科研记录”点击“＋ 手动记录”进入块编辑器。支持文本、标题、图片、代码、提示、分隔线；粘贴/拖入截图；块级文字批注；自动保存、草稿恢复、版本历史与 Markdown 导出。时间戳由服务端保存为元数据，只在“更多 → 笔记信息”中查看笔记创建/修改时间（北京时间，到秒），不在正文或各块旁展示；导出放在 frontmatter，旧块缺失时间不补造。代码块不执行。笔记与截图仅写入 Wiki 根目录，不改科研源项目或既有 AI 日档。

多窗口或外部编辑冲突时停止覆盖并让用户选择；不要用 MCP 写入覆盖网页笔记来规避冲突。图片批注当前是块级文字，未提供画笔/箭头标注。

## 科研进度与顺手记录

原待办页面现为两周/四周科研时间轴。任务回车添加、列表直接切换状态并保存；点击任务记录“上次做到哪、下一步”和日期，未排期单列，进行中/卡住任务显示“继续上次”。旧记录中的待办须选择导入，不自动生成日期或推断完成。任务与修改历史存于 Wiki 根目录 `.research-progress/tasks.json`，并发冲突须处理后保存。

手动笔记直接粘贴/拖入截图即可；不要指导用户必须先点加号、打开文件选择器。空图片块不自动弹选择器，已有图只有“替换图片”才覆盖。连续文字使用底部“继续记录…”或 Ctrl/Cmd+Enter；复制、移动、删除位于块菜单。不要把服务端时间写进正文。

自动日报/周报尚未接入调度与完整授权对话来源。不能声称 Hook 已掌握所有聊天、文件改动等于成果，或关闭运行端也必定生成报告。参见仓库 `docs/research-continuity.md` 的实施边界。
