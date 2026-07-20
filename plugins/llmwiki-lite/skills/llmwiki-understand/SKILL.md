---
name: llmwiki-understand
description: Understand a software project and create a small, useful, source-backed Markdown Wiki with Codex reasoning. Use for requests to understand, document, explain, map, or build a Wiki for a project or codebase.
---
# Understand a project

Codex does semantic work. MCP performs bounded filesystem mechanics.

1. Resolve or register the project with `llmwiki_project_get` and `llmwiki_project_register`.
2. Read existing pages with `llmwiki_wiki_list`; open useful pages only when they exist.
3. Run `llmwiki_status` and `llmwiki_files` for a bounded survey. Do not read every file by default.
4. Choose evidence yourself: README, entry points, configuration, tests, core modules, and files tied to the user's goal.
5. Use `llmwiki_search` to find symbols and `llmwiki_read` in bounded ranges. Read exact source before asserting behavior.
6. Explain what is known, how it works, evidence paths, and what remains unknown.
7. Create only pages with durable value. Prefer a few rich pages over a taxonomy or many stubs.
8. Write semantic Markdown with `llmwiki_wiki_write`, using project-relative `sources` where useful.
9. Run `llmwiki_wiki_check` after adding links or source references.

Do not generate fifteen fixed categories or empty pages. Do not copy source files into Markdown. Separate observed facts from inference and uncertainty. Preserve existing useful content. Stop when the user's question is answered and the smallest useful durable record exists.
