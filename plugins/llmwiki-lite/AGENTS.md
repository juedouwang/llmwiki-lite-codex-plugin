# LLM Wiki vNext Agent Instructions

These instructions apply only inside `plugins/llmwiki-lite/` and replace the legacy repository architecture for this subtree.

## Product boundary

This directory is a standalone Codex Plugin.

- Codex/LLM performs project understanding, semantic selection, reasoning, synthesis, and Wiki writing.
- Six independent Skills cover project registration, understanding, query, maintenance, literature workflows, and web visualization.
- MCP tools perform deterministic filesystem, registry, storage, and local-server mechanics only.
- Hooks record optional dirty-path hints and remain fail-open.
- The website is a loopback-only Markdown viewer and storage-settings UI, not a second reasoning system.
- Do not import or recreate the legacy Research Core architecture from `tools/` or the legacy `llmwiki-research-codex-plugin` repository.

## Keep it small

Do not add fixed knowledge taxonomies, fifteen-page rendering, Claim/Evidence lifecycle state machines, planning protocols, or layered Schema frameworks. Project registration and the website must remain small configuration/visualization features.

## Source layout

```text
.codex-plugin/plugin.json
.mcp.json
skills/llmwiki-projects/
skills/llmwiki-understand/
skills/llmwiki-query/
skills/llmwiki-maintain/
skills/llmwiki-literature/
skills/llmwiki-web/
hooks/hooks.json
scripts/llmwiki_core.py
scripts/llmwiki_registry.py
scripts/markdown_renderer.py
scripts/literature_web.py
scripts/research_web_ui.py
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
7. Hook failure must never fail the host tool call.
8. Storage moves copy by default, reject non-empty merges, and never delete old directories.
9. Update README, affected Skills, and smoke tests when behavior changes.

## Validation

Run from the repository root:

```powershell
$env:PYTHONUTF8='1'
ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/tests
python -B plugins/llmwiki-lite/tests/smoke_test.py
python C:/Users/lyn/.codex/skills/.system/skill-creator/scripts/quick_validate.py <each-skill-directory>
python C:/Users/lyn/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py next/llmwiki-lite-codex-plugin/plugins/llmwiki-lite
```
