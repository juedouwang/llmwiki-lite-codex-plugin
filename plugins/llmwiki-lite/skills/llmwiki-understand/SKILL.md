---
name: llmwiki-understand
description: Understand a research or software project and create a small, useful, source-backed Markdown Wiki with Codex reasoning. Use for requests to understand, document, explain, map, or build a Wiki for a project, codebase, experiment, or research topic.
---
# Understand a project

Codex does semantic work. MCP performs bounded filesystem mechanics.

1. Resolve or register the project with `llmwiki_project_get` and `llmwiki_project_register`.
2. Read existing pages with `llmwiki_wiki_list`; open useful pages only when they exist.
3. Run `llmwiki_status` and `llmwiki_files` for a bounded survey. Do not read every file by default.
4. Choose evidence yourself: research goals, README, papers and notes, entry points, configuration, tests, core modules, datasets, experiment records, and files tied to the user's goal.
5. Use `llmwiki_search` to find exact terms and `llmwiki_read` in bounded ranges. Read exact sources before asserting behavior, numbers, experimental conditions, or conclusions.
6. Explain what is known, how it works, evidence paths, and what remains unknown.
7. Create only pages with durable value. Prefer a few rich pages over a taxonomy or many stubs.
8. Write semantic Markdown with `llmwiki_wiki_write`, using project-relative `sources` where useful.
9. Run `llmwiki_wiki_check` after adding links or source references.

## Default language and writing style

- Human-readable Wiki pages and user-facing reports default to Simplified Chinese unless the user explicitly requests another language.
- Write for Chinese graduate students: state the research question, method, experimental conditions, evidence, conclusion, limitations, and next step when relevant.
- Keep code, paths, commands, API/MCP names, schema fields, model or algorithm names, paper titles, dataset names, and technical terms in English when translation would reduce precision.
- Do not mechanically translate established terminology, and do not hide uncertainty behind polished prose.

## Literature reading contract

When the project contains downloaded papers or the user asks Codex to read a paper:

- Keep the original paper inside the registered project, normally under `references/`, `papers/`, or another user-chosen source directory. Never rewrite or delete it.
- Read the original before summarizing. Write the durable assistant-reading version as Simplified Chinese Markdown in `wiki_root`.
- Use frontmatter that binds the note to the exact project-relative file:

```yaml
---
title: "Exact Paper Title 中文精读"
type: literature-note
language: zh-CN
paper_file: references/path/to/paper.pdf
sources:
  - references/path/to/paper.pdf
---
```

- Prefer the sections: 文献信息、一句话结论、研究问题、方法概览、关键公式或机制、实验设计、主要结果、局限、与本课题关系、复现线索、待验证问题。
- Preserve the exact paper title, algorithm/model names, dataset names, equations, metrics, and numeric results. Separate paper claims, your interpretation, and unverified inference.
- The local website pairs `paper_file` with the original and can show the paper and assistant-reading Markdown side by side.
Do not generate fifteen fixed categories or empty pages. Do not copy source files into Markdown. Separate observed facts from inference and uncertainty. Preserve existing useful content. Stop when the user's question is answered and the smallest useful durable record exists.