# LLM Wiki Lite Codex Plugin

这是与旧 `llmwiki-research` 架构完全分离的轻量版 Codex Plugin。

Codex/LLM 负责项目理解、推理和 Wiki 写作；程序只提供项目注册、文件扫描、搜索、读取、hash、增量检测、安全写入、Hook 提示和本地网站等机械能力。

## 安装

从 GitHub 安装 Marketplace：

```powershell
codex plugin marketplace add https://github.com/juedouwang/llmwiki-lite-codex-plugin
codex plugin add llmwiki-lite@llmwiki-lite
```

Plugin 位于 `plugins/llmwiki-lite/`，完整说明见 `plugins/llmwiki-lite/README.md`。

## 产品组成

- 五个独立 Skill：项目管理、项目理解、查询、维护、网站；
- MCP：注册表、扫描、搜索、读取、hash、增量检测和 Wiki 写入；
- Hook：可选、fail-open 的 dirty-path 提示；
- Web：loopback-only 的简洁 Markdown 可视化与存储位置设置。

程序不生成固定十五类页面，也不实现替代 LLM 的复杂 Research Core。

## 与旧版的区别

轻量版独立仓库：

```text
https://github.com/juedouwang/llmwiki-lite-codex-plugin
```

旧 Research 版仓库：

```text
https://github.com/juedouwang/llmwiki-research-codex-plugin
```

两条发布线使用不同的仓库名、Marketplace 名和 Plugin 名，可以并行维护，不会覆盖彼此。

## 本地开发安装

```powershell
codex plugin marketplace add E:\GitHub\llm-wiki-agent\next\llmwiki-lite-codex-plugin
codex plugin add llmwiki-lite@llmwiki-lite
```

## 从 GitHub 更新

```powershell
codex plugin marketplace upgrade llmwiki-lite
codex plugin add llmwiki-lite@llmwiki-lite
```

发布维护以 `main` 分支和版本标签（例如 `v0.2.0`）为准。版本更新流程见 `RELEASING.md`，变更记录见 `CHANGELOG.md`。

## 验证

从仓库根目录运行：

```powershell
$env:PYTHONUTF8='1'
ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/tests
python -B plugins/llmwiki-lite/tests/smoke_test.py
python C:/Users/lyn/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/llmwiki-lite
```