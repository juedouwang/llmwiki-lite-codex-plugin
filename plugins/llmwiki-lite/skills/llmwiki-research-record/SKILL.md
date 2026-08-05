---
name: llmwiki-research-record
description: "Record an explicitly requested research discussion as a durable, concise Markdown process record: stage understanding, evidence, decisions, open questions, and next steps. Use when the user says 记录刚才的讨论、保存阶段性理解、记录科研过程、记录科研决策、生成今天的研究记录, or asks to review prior research records. Do not automatically save every conversation."
---

# LLM Wiki Research Record

This Skill adds a small, explicit research-process journal to the current LLM Wiki project. Codex performs the interpretation and writing; MCP only validates fields and appends/reads Markdown files.

## When to trigger

Use this Skill when the user explicitly asks to:

- 记录刚才的讨论；
- 保存这次阶段性理解；
- 记录一个科研决策；
- 记录当前研究进展；
- 查看、检索或回顾之前的科研记录。

Do **not** save every conversation automatically. A record must be created only after an explicit user request.

## Resolve the project

1. Call `llmwiki_project_get` to resolve the current registered project.
2. If the current project is not registered, ask whether to register it, or use `llmwiki_project_register` when the user has already clearly authorized registration.
3. Keep the research source project (`source_root`) separate from the human-readable Wiki root (`wiki_root`). Records are stored below the configured Wiki root, normally in `records/YYYY/MM/YYYY-MM-DD.md`.

## Create a record

Before calling `llmwiki_record_write`, review the relevant conversation in the current context and produce a concise synthesis. Do not create a verbatim chat transcript. Separate:

- what the source, experiment, or user explicitly established；
- Codex's interpretation or working hypothesis；
- decisions that were actually made；
- questions that remain unverified。

Call `llmwiki_record_write` with:

- `project_root`: the resolved project's `source_root`；
- `project_id`: the resolved project ID；
- `title`: a specific Chinese title, such as `低纹理配准实验的阶段性理解`；
- `discussion_context`: why this discussion happened and what question it addressed；
- `understanding`: the current stage understanding, required and written in Chinese by default；
- `evidence`: related paper paths, source files, experiment outputs, or other evidence references; do not invent paths；
- `conclusion`: the current conclusion, if one exists；
- `decisions`: decisions actually made in this discussion, not suggestions disguised as decisions；
- `open_questions`: unresolved or unverified questions；
- `next_steps`: concrete follow-up actions；
- `related_files`: project-relative source or paper paths when known；
- `related_pages`: Wiki-relative Markdown paths when known；
- `tags`: a few useful tags such as `阶段性理解`, `实验`, `文献`, or `决策`。

The tool appends one new entry to the day file `records/YYYY/MM/YYYY-MM-DD.md`; it never overwrites an existing entry. Each entry has a stable `#entry_key` fragment, so a multi-entry day must be read with the full ID such as `records/2026/08/2026-08-04.md#123000-topic`. If the user asks to revisit an old record, read it first with `llmwiki_record_read`, then create a new follow-up entry unless the user explicitly asks to edit a specific Markdown page through another supported workflow.

## Read records

- Use `llmwiki_record_list` for a chronological list or keyword search。
- Use `llmwiki_record_read` for the complete record。
- Do not treat a record as scientific truth merely because it exists. Use its evidence references and return to the source project or paper when the user asks for verification。

## Present the result

After creating a record:

1. Tell the user the exact Wiki-relative path returned by the tool。
2. Summarize what was recorded in 2–4 bullets。
3. Offer or start the local website with `llmwiki_web_start` so the user can open the project's `科研记录` page。

The website is a viewer. It must not silently rewrite or reinterpret the record。