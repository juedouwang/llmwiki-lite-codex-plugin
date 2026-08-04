# Release and maintenance

This repository is the standalone lightweight release line for LLM Wiki. It is intentionally separate from `llmwiki-research-codex-plugin`.

## Release checklist

1. Update the version in `plugins/llmwiki-lite/.codex-plugin/plugin.json`.
2. Update `CHANGELOG.md`.
3. Run the local validation commands:
   - `ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/tests`
   - `python -B plugins/llmwiki-lite/tests/smoke_test.py`
   - `quick_validate.py` for every Skill directory
   - `validate_plugin.py plugins/llmwiki-lite`
4. Commit the release preparation to `main` and push it.
5. Create an annotated tag that exactly matches the manifest version, for example `v0.3.1`, and push it.
6. `.github/workflows/validate.yml` checks every `main` push and pull request.
7. `.github/workflows/release.yml` validates the tag and creates the GitHub Release automatically.

The installed Codex client still needs a Marketplace refresh; GitHub Actions validates and publishes the release but does not silently replace an installed local plugin.

## User update flow

```powershell
codex plugin marketplace upgrade llmwiki-lite
codex plugin add llmwiki-lite@llmwiki-lite
```

For local development, use the local marketplace path in `README.md` instead of the GitHub URL.