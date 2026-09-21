# Release and maintenance

This repository is the standalone lightweight release line for LLM Wiki. It is intentionally separate from `llmwiki-research-codex-plugin`. It ships one source tree for Codex, Claude Code, and opencode.

## Release checklist

1. Update the version in `plugins/llmwiki-lite/.codex-plugin/plugin.json` and keep `plugins/llmwiki-lite/.claude-plugin/plugin.json` at the same version.
2. Update `CHANGELOG.md`.
3. Run the local validation commands:
   - `ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/opencode plugins/llmwiki-lite/tests`
   - `python -B plugins/llmwiki-lite/tests/smoke_test.py`
   - `python plugins/llmwiki-lite/opencode/install.py --dry-run`
   - `quick_validate.py` for every Skill directory
   - `validate_plugin.py plugins/llmwiki-lite`
4. Commit the release preparation to `main` and push it.
5. Create an annotated tag that exactly matches the manifest version, for example `v0.3.3`, and push it.
6. `.github/workflows/validate.yml` checks every `main` push and pull request, including the Claude Code manifest and version sync.
7. `.github/workflows/release.yml` validates the tag against both manifests and creates the GitHub Release automatically.

Installed clients still need their own refresh; GitHub Actions validates and publishes the release but does not silently replace an installed plugin.

## User update flow

After pushing a change to GitHub:

```powershell
# Codex
codex plugin marketplace upgrade llmwiki-lite
codex plugin add llmwiki-lite@llmwiki-lite

# Claude Code（在会话内）
/plugin marketplace update llmwiki-lite
/plugin update llmwiki-lite

# opencode（更新仓库后重新运行）
python plugins/llmwiki-lite/opencode/install.py
```

Start a new conversation after the refresh so new Skill and MCP files are loaded. opencode 在启动时读取配置，需要重启应用。
