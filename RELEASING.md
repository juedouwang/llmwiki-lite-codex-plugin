# Release and maintenance

This repository is the standalone lightweight release line for LLM Wiki. It is intentionally separate from `llmwiki-research-codex-plugin`.

## Release checklist

1. Update the version in `plugins/llmwiki-lite/.codex-plugin/plugin.json`.
2. Update `CHANGELOG.md`.
3. Run:
   - `ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/tests`
   - `python -B plugins/llmwiki-lite/tests/smoke_test.py`
   - `quick_validate.py` for all five Skill directories
   - `validate_plugin.py plugins/llmwiki-lite`
4. Commit to `main`.
5. Create an annotated tag such as `v0.2.1` and push it.
6. Create a GitHub Release from the tag.

## User update flow

```powershell
codex plugin marketplace upgrade llmwiki-lite
codex plugin add llmwiki-lite@llmwiki-lite
```

For local development, use the local marketplace path in `README.md` instead of the GitHub URL.