---
name: llmwiki-projects
description: Manage LLM Wiki project registration and storage locations. Use when the user asks to register, list, select, unregister, or move a project, Wiki directory, machine-state directory, or default Wiki root; also use when a Wiki task starts in a project that has not been registered.
---
# Project registry and storage

Use this skill for identity and location management only. Registration is not project understanding.

## Resolve a project

1. Use `llmwiki_project_get` with the current working directory as `current_path` when possible.
2. If no record exists, use `llmwiki_project_register` with the current project directory as `source_root`.
3. Do not scan, summarize, or create semantic pages just because a project was registered.
4. Use the returned `source_root`, `state_root`, and `wiki_root` in later tool calls.

## Storage policy

- The current user's configured default may be an external root such as `E:\wiki_obsidian`.
- For a new user with no configured default, the default is `<project-root>/wiki`.
- A configured default is a parent: each project receives `<default-wiki-root>/<project-id>` unless an explicit `wiki_root` is supplied.
- Machine state belongs in `state_root`; human-readable Markdown belongs in `wiki_root`.
- Explain these locations in Simplified Chinese by default, using “人类可读 Wiki 目录” and “机器状态目录” in user-facing text.

Use `llmwiki_project_list`, `llmwiki_project_select`, `llmwiki_project_storage_update`, `llmwiki_settings_update`, and `llmwiki_project_unregister` as needed. Keep `copy_existing=true` unless the user explicitly wants an empty location. Explain that old directories are preserved. Never infer that registration means the project has been understood.
网站项目管理支持右键或更多菜单重命名及从列表移除：重命名只更新展示名称，不改变 ID/source_root/wiki_root/state_root；移除只取消注册，不删除任何文件。网站当前项目与助手 current_project_id 分离；进入设置/总览维持当前浏览项目，默认选择只用于新网站会话的初始化。

新注册项目默认加入报告自动整理的参与列表；不想参与时用户可在设置中取消，重复注册或重命名不恢复已取消选择。仅继承已保存的取材宿主开关，不开启原先关闭的对话取材，不回填授权前历史，不创建/连接/恢复定时计划，也不立刻扫描或生成内容。注销同时移出自动整理范围，但保留源码、Wiki、笔记、授权和历史报告文件。
