---
name: llmwiki-maintain
description: Maintain an existing LLM Wiki after project changes. Use for incremental updates, stale-source checks, broken links, changed files, contradictions, and Wiki health requests.
---
# Maintain a project Wiki

Use deterministic tools for change detection and Codex for impact analysis.

1. Resolve the project with `llmwiki_project_get`.
2. Run `llmwiki_status` to inspect the last baseline and dirty-path hints.
3. Run `llmwiki_snapshot` with `save=false` first. Save the baseline only after reviewing the change set or when requested.
4. Treat changed paths as candidates, not proof. Search and read changed sources plus the `sources` listed by affected Wiki pages.
5. Decide which pages are stale, contradicted, or unaffected. Do not rewrite every page.
6. Update only affected pages with `llmwiki_wiki_write`, preserving useful user-authored content.
7. Run `llmwiki_wiki_check` for broken links and missing source paths.
8. Report changed files, pages updated, pages left alone, and unresolved questions.

Hook events are hints only. If Hooks are disabled or incomplete, the snapshot is the correctness check. Do not turn a timestamp into a semantic claim or create placeholder pages just to report a gap.
