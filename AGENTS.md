# LLM Wiki Lite Multi-Platform Plugin

This marketplace contains the new lightweight implementation for Codex, Claude Code, and opencode. Do not copy the legacy Research Core, fixed fifteen-page rendering, lifecycle state machines, or multi-layer Schema machinery into this tree.

The installable Plugin is `plugins/llmwiki-lite/`. Its intentionally small product surface is:

- seven independent workflow Skills shared by every supported host;
- deterministic MCP filesystem and project-registry tools;
- an optional fail-open change Hook for Codex, Claude Code, and opencode;
- a loopback-only Markdown website with storage settings.

Host entry points:

- Codex: `.agents/plugins/marketplace.json` plus `plugins/llmwiki-lite/.codex-plugin/plugin.json`;
- Claude Code: `.claude-plugin/marketplace.json` plus `plugins/llmwiki-lite/.claude-plugin/plugin.json`;
- opencode: `plugins/llmwiki-lite/opencode/install.py` writes the user's `opencode.json`.

Follow `plugins/llmwiki-lite/AGENTS.md` for implementation and validation rules.
