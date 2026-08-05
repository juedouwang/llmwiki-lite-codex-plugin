---
name: llmwiki-literature
description: "Manage the complete literature workflow for an LLM Wiki project: research and recommend papers, wait for the user to select one, download the authorized original into the registered project, create a Simplified Chinese assisted-reading Markdown note, bind it with paper_file, and open the local literature center. Use when the user asks for paper recommendations, paper downloading, literature review, close reading, paper explanation, or adding a paper to the project library."
---

# LLM Wiki Literature

Run one focused literature workflow. Codex performs research judgment, source evaluation, paper reading, and explanation. Use tools only for registration, safe file operations, Wiki writes, and website startup.

## 1. Resolve the project

1. Use `llmwiki_project_get` for the current project; register it with `llmwiki_project_register` only if needed.
2. Treat `source_root` as the research project and `wiki_root` as durable human-readable knowledge.
3. Ask for a research question only when the user's topic and project context do not provide one.

## 2. Recommend before downloading

When the user asks for recommendations:

1. Search current, authoritative paper sources and primary publication pages.
2. Return a short ranked list, normally 5–8 papers, with exact title, year, venue/source, why it matters to this project, reading priority, and an accessible original-paper location when available.
3. Distinguish foundational work, closest competing methods, recent progress, and useful negative or contrasting evidence.
4. Do not download every recommendation. Wait until the user explicitly chooses a paper or explicitly authorizes a batch.
5. Do not bypass paywalls, authentication, robots restrictions, or access controls. If the original cannot be downloaded lawfully with the available access, ask the user to provide the file.

## 3. Download the selected original

After explicit selection or authorization:

1. Default to `<source_root>/references/papers/` unless the user chose another project-relative folder.
2. Use a filesystem-safe filename based on the exact paper title or stable paper identifier. Preserve an existing file; never overwrite silently.
3. Download only from the selected primary or user-approved source using the host's approved web or shell capability. This Plugin does not perform hidden background network requests.
4. Validate that the result is non-empty and actually matches the expected format. For a PDF, check the `%PDF-` signature as well as the response metadata when available.
5. Record the final project-relative path. Never place the original paper in `wiki_root`, and never rewrite or delete it.

## 4. Read and explain

1. Read the original paper, not only its abstract, filename, recommendation page, or an existing LLM summary.
2. If the environment cannot read the paper contents, stop and say so; do not fabricate a close reading.
3. Write the durable assisted-reading version in Simplified Chinese unless the user requests another language.
4. Preserve exact paper title, author names, algorithm/model names, datasets, equations, metric names, table references, and numeric results where precision matters.
5. Clearly separate:
   - 论文原文明确陈述；
   - Codex 的解释或通俗化说明；
   - 与当前课题的联系；
   - 尚未验证的推断或复现问题。

Recommended structure:

- 文献信息
- 一句话结论
- 研究问题
- 方法概览
- 关键公式或机制
- 实验设计
- 主要结果
- 局限
- 与本课题关系
- 复现线索
- 待验证问题

## 5. Bind the note to the paper

Write one useful Markdown note with `llmwiki_wiki_write`. Use a durable path such as `literature/<paper-slug>.md` and exact project-relative binding:

```yaml
---
title: "Exact Paper Title 中文精读"
type: literature-note
language: zh-CN
paper_file: references/papers/exact-paper.pdf
sources:
  - references/papers/exact-paper.pdf
---
```

`paper_file` 是网页文献中心把这条笔记关联到原文的唯一依据，必须填第 3 节记录的下载文件的项目相对路径，并逐字一致（包含大小写、目录层级、扩展名）。漏填或填错时，笔记不会出现在这篇论文的关联列表里，只会进入“待关联”列表（只有不带 paper_file 的旧笔记才会被网页用文件名/标题做模糊匹配）。写完笔记后必须用 `llmwiki_wiki_check` 校验。

Run `llmwiki_wiki_check` after writing. Do not create a fixed taxonomy, duplicate empty pages, or copy the PDF into Markdown.

## 6. Present the result

1. Start or reuse the local website with `llmwiki_web_start`.
2. Tell the user to open the project's 文献中心.
3. Confirm the paper appears as 已精读 and that 原文 + LLM 辅助阅读 opens the side-by-side view.
4. If pairing fails, the cause is almost always a `paper_file` path mismatch: correct the field to the exact project-relative path. Do not loosen matching to accept an unrelated note.

Stop when the selected paper is downloaded, read, explained, bound, validated, and visible in the literature center.
