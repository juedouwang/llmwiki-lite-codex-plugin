# Release and maintenance

This repository is the standalone lightweight release line for LLM Wiki. It is intentionally separate from `llmwiki-research-codex-plugin`. One source tree ships the plugin for Codex, Claude Code, and opencode, and the Windows desktop app. All of them share one version number.

## Versioning

- The version lives in `plugins/llmwiki-lite/.codex-plugin/plugin.json` and `plugins/llmwiki-lite/.claude-plugin/plugin.json`; keep them identical and always a plain `X.Y.Z` ([Semantic Versioning](https://semver.org/)). CI rejects anything else.
- The desktop app reads its version from the bundled `plugin.json`, and its update check compares that with the newest GitHub Release tag.
- `CHANGELOG.md` keeps an `## Unreleased` section at the top; a release renames it to `## X.Y.Z — YYYY-MM-DD`. That section becomes the Release notes.

## Release checklist

1. Move the `Unreleased` entries in `CHANGELOG.md` under a new `## X.Y.Z — YYYY-MM-DD` heading and leave an empty `## Unreleased` above it.
2. Set the same `X.Y.Z` in both `plugin.json` files.
3. Run the local checks:
   - `ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/opencode plugins/llmwiki-lite/tests`
   - `python -B plugins/llmwiki-lite/tests/smoke_test.py`
   - `python plugins/llmwiki-lite/opencode/install.py --dry-run`
   - `claude plugin validate plugins/llmwiki-lite`
   - Optional, Windows: `desktop/build.ps1`, then `desktop/package.ps1`, and try the zip.
4. Merge to `main` and push. `validate.yml` runs the checks and test-builds the desktop zip (kept for 7 days as an Actions artifact).
5. Tag and push: `git tag -a vX.Y.Z -m "vX.Y.Z"` then `git push origin vX.Y.Z`.
6. `release.yml` then, without further input:
   - checks that the tag equals both manifest versions, runs ruff and the smoke test;
   - creates the GitHub Release with the `CHANGELOG.md` section as notes;
   - builds the desktop app on `windows-latest`, runs its tests, and attaches `WildResearchWorkbench-X.Y.Z-windows-x64.zip` plus its `.sha256`.

Installed copies are never replaced silently: plugin users run the update commands below, and desktop users see an in-app notice that links to the new Release.

## User update flow

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

Start a new conversation after the refresh so new Skill and MCP files are loaded. opencode 在启动时读取配置，需要重启应用。Desktop users download the new zip, close the app, and run `install.cmd` again; their data in `%LOCALAPPDATA%\LLMWiki` is untouched.

## Local development

- **Plugin:** with both hosts' `llmwiki-lite` marketplace pointing at this checkout, run `tools/refresh-local-plugin.ps1`. Hosts only reinstall on a version change, so the script installs a temporary `X.Y.(Z+1)-dev.<UTC time>` version and restores both manifests byte for byte afterwards. Do not hand-edit the version for local testing.
- **Desktop:** run `desktop/run-dev.ps1` to start the app straight from this checkout; code changes apply on the next start. Close an installed copy that uses the same data first (one data directory opens one window), or pass `-DataHome <scratch dir>`. `desktop/build.ps1` + `desktop/install.ps1` is only needed to test the packaged app itself.
