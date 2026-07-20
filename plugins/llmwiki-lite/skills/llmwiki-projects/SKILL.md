---
name: llmwiki-projects
description: Manage LLM Wiki project registration and storage locations. Use when the user asks to register, list, select, unregister, or move a project, Wiki directory, machine-state directory, or default Wiki root; also use when a Wiki task starts in a project that has not been registered.
---
# Project registry and storage

Use this skill for identity and location management only. Registration is not project understanding.

## Resolve a project

1. Use `llmwiki_project_get` with the current working directory as `current_path` when possible.
2. If no record exists, use `llmwiki_project_register` with the Codex-opened project directory as `source_root`.
3. Do not scan, summarize, or create semantic pages just because a project was registered.
4. Use the returned `source_root`, `state_root`, and `wiki_root` in later tool calls.

## Storage policy

- The current user's configured default may be an external root such as `E:\wiki_obsidian`.
- For a new user with no configured default, the default is `<project-root>/wiki`.
- A configured default is a parent: each project receives `<default-wiki-root>/<project-id>` unless an explicit `wiki_root` is supplied.
- Machine state belongs in `state_root`; human-readable Markdown belongs in `wiki_root`.

Use `llmwiki_project_list`, `llmwiki_project_select`, `llmwiki_project_storage_update`, `llmwiki_settings_update`, and `llmwiki_project_unregister` as needed. Keep `copy_existing=true` unless the user explicitly wants an empty location. Explain that old directories are preserved. Never infer that registration means the project has been understood.
