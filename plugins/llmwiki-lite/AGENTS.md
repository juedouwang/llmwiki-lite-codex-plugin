# LLM Wiki vNext Agent Instructions

These instructions apply only inside `plugins/llmwiki-lite/` and replace the legacy repository architecture for this subtree.

## Product boundary

This directory is one standalone plugin that targets three hosts: Codex, Claude Code, and opencode.

- The host AI performs project understanding, semantic selection, reasoning, synthesis, and Wiki writing.
- Seven independent Skills cover project registration, understanding, query, maintenance, literature workflows, explicit research records, and web visualization. The same `skills/` tree serves all three hosts.
- MCP tools perform deterministic filesystem, registry, storage, and local-server mechanics only.
- Hooks record optional dirty-path hints and remain fail-open on every host.
- The website is a loopback-only Markdown viewer, storage-settings UI, explicit manual research notebook editor, and explicit research-task timeline, not a second reasoning system. Manual notebooks are limited to records/manual; task state is limited to .research-progress under the Wiki root. Preserve existing assistant daily records. Do not infer task completion or scientific results from file changes.
- Do not import or recreate the legacy Research Core architecture from `tools/` or the legacy `llmwiki-research-codex-plugin` repository.

## Keep it small

Do not add fixed knowledge taxonomies, fifteen-page rendering, Claim/Evidence lifecycle state machines, planning protocols, or layered Schema frameworks. Project registration and the website must remain small configuration/visualization features.

## Source layout

```text
.codex-plugin/plugin.json         # Codex manifest
.claude-plugin/plugin.json        # Claude Code manifest
.mcp.json                         # shared MCP config (Codex + Claude Code)
hooks/hooks.json                  # shared change hook (Codex + Claude Code)
opencode/llmwiki-hook.js          # opencode plugin hook
opencode/install.py               # opencode config installer
opencode/README.md
skills/llmwiki-projects/
skills/llmwiki-understand/
skills/llmwiki-query/
skills/llmwiki-maintain/
skills/llmwiki-literature/
skills/llmwiki-web/
skills/llmwiki-research-record/
scripts/llmwiki_core.py
scripts/llmwiki_registry.py
scripts/markdown_renderer.py
scripts/literature_web.py
scripts/research_web_ui.py
scripts/research_notebook.py
scripts/research_records.py
scripts/web_server.py
scripts/mcp_server.py
scripts/record_change.py
tests/smoke_test.py
```

## Implementation rules

1. Use Python standard library only unless the user explicitly approves a dependency.
2. Keep MCP results bounded and JSON serializable.
3. Normalize roots and reject path traversal for reads, writes, and website assets.
4. Never perform independent network sends; the website binds only to loopback.
5. Never infer semantic importance or scientific truth in Python code.
6. Preserve user-authored Wiki content outside explicitly generated regions.
7. Hook failure must never fail the host tool call on any host.
8. Storage moves copy by default, reject non-empty merges, and never delete old directories.
9. Update README, affected Skills, and smoke tests when behavior changes.
10. Keep `.codex-plugin/plugin.json` and `.claude-plugin/plugin.json` versions in sync, and keep `.mcp.json` / `hooks/hooks.json` resolvable by both Codex and Claude Code without host-specific JSON files.
11. Skill and script text must stay host-neutral: never address one host by name in Skill instructions or user-facing website strings.

## Validation

Run from the repository root:

```powershell
$env:PYTHONUTF8='1'
ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/opencode plugins/llmwiki-lite/tests
python -B plugins/llmwiki-lite/tests/smoke_test.py
python plugins/llmwiki-lite/opencode/install.py --dry-run
python C:/Users/lyn/.codex/skills/.system/skill-creator/scripts/quick_validate.py <each-skill-directory>
python C:/Users/lyn/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py next/llmwiki-lite-codex-plugin/plugins/llmwiki-lite
```
