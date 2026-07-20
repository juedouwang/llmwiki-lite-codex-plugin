---
name: llmwiki-query
description: Answer questions about a registered software project using its Wiki plus direct source verification. Use for project queries, implementation questions, code behavior, architecture explanations, and evidence checks.
---
# Query a project

Use the Wiki as an index, not as unquestionable truth.

1. Resolve the project with `llmwiki_project_get` using the current path.
2. List the Wiki with `llmwiki_wiki_list` and identify relevant pages.
3. Read relevant pages and search the real project with `llmwiki_search`.
4. Re-open exact source ranges with `llmwiki_read` for code behavior, paths, configuration, numbers, conflicts, or quotations.
5. Answer directly with project-relative evidence paths. Mark inference and uncertainty explicitly.
6. Update the Wiki only when the answer has durable value or the user asks for it, using `llmwiki_wiki_write`.
7. Run `llmwiki_wiki_check` if links or source paths changed.

Do not trust old summaries, hashes, or Hook hints as semantic evidence. Do not invent missing facts. Do not scan the entire project when targeted search and bounded reads can answer the question.
