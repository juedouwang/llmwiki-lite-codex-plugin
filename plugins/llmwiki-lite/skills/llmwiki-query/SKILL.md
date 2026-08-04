---
name: llmwiki-query
description: Answer questions about a registered research or software project using its Wiki plus direct source verification. Use for research questions, implementation behavior, methods, experiments, results, architecture, and evidence checks.
---
# Query a project

Use the Wiki as an index, not as unquestionable truth.

1. Resolve the project with `llmwiki_project_get` using the current path.
2. List the Wiki with `llmwiki_wiki_list` and identify relevant pages.
3. Read relevant pages and search the real project with `llmwiki_search`.
4. Re-open exact source ranges with `llmwiki_read` for code behavior, paths, configuration, numbers, experimental conditions, conflicts, or quotations.
5. Answer directly with project-relative evidence paths. Mark inference and uncertainty explicitly.
6. Update the Wiki only when the answer has durable value or the user asks for it, using `llmwiki_wiki_write`.
7. Run `llmwiki_wiki_check` if links or source paths changed.

## Default answer style

- Answer in Simplified Chinese unless the user requests another language.
- Organize research answers around question, evidence, analysis, conclusion, limitations, and next action when useful.
- Keep exact code, paths, commands, API/MCP names, algorithm and model names, dataset names, paper titles, metrics, and schema fields in English where precision matters.
- Distinguish source-backed facts, calculations, interpretation, and unresolved uncertainty.

## Literature questions

For a paper-specific question, use any `literature-note` as a reading index, then verify exact claims, equations, tables, metrics, and quotations against the bound `paper_file` or another primary source available in the project. State clearly when the PDF could not be inspected. Do not turn a broad literature map or an LLM interpretation into a paper claim.
Do not trust old summaries, hashes, or Hook hints as semantic evidence. Do not invent missing facts. Do not scan the entire project when targeted search and bounded reads can answer the question.