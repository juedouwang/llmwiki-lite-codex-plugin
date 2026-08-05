# LLM Wiki Lite Codex Plugin

这是与旧 `llmwiki-research` 架构完全分离的轻量版科研与项目知识助手。

- **Codex / LLM** 负责理解、选择证据、推理和 Markdown 写作；
- **Skill** 负责给 Codex 清晰、可独立执行的工作指引；
- **MCP** 只提供注册、扫描、检索、读取、快照、状态和安全写入等重复机械劳动；
- **Hook** 只记录可能变化的路径提示；
- **Web** 是简体中文优先的本地科研知识工作台。

程序不会生成固定十五类空页面，也不会重新引入复杂 Research Core。

## 安装

从 GitHub 安装 Marketplace：

```powershell
codex plugin marketplace add https://github.com/juedouwang/llmwiki-lite-codex-plugin
codex plugin add llmwiki-lite@llmwiki-lite
```

Plugin 位于 `plugins/llmwiki-lite/`，完整说明见 `plugins/llmwiki-lite/README.md`。

## 中文科研工作台

网页端面向中国研究生的日常科研使用：

- 首页查看研究项目、知识页、源文件和待核对变化；
- 项目研究台按研究总览、文献、方法、数据、实验、结果、结论、计划等流程动态归类现有 Markdown；
- 文献中心采用 Obsidian 风格的左侧文献库，可按阅读状态和文献类型快速筛选，并支持卡片/列表切换；
- 自动发现项目目录中的 PDF、EPUB、DOCX 和 HTML，并区分论文、补充材料、专利与报告；
- PDF 可直接在网页内阅读，也可与 Codex 生成的简体中文精读 Markdown 左右双栏对照；
- 独立 `llmwiki-literature` Skill 负责“调研推荐 → 用户选择 → 下载原文 → 中文精读 → 网页入库”的完整流程；
- 检索论文、方法、实验、指标、结论和待办；
- 阅读页提供项目导航、本页目录、更新时间、预计阅读时长、复制路径和打印/PDF；
- 在网页修改人类可读 Wiki 目录与高级机器状态目录。

网页只监听 loopback，不修改源项目，也不提供任意 Markdown 正文编辑器。文献原文始终只读；系统不会自动翻译 PDF，也不会把不确定的阅读记录强行关联到论文。

## 默认语言

人类可读 Wiki 和报告默认使用简体中文。代码、路径、命令、API/MCP 名、算法名、模型名、论文标题、数据集名和需要保持精度的技术术语保留英文。

## 存储

- 新用户未配置时：`<project-root>/wiki`
- 当前用户推荐的人类可读根目录：`E:\wiki_obsidian`
- 机器状态与人类 Markdown 分开保存
- 网页可修改默认位置和项目位置
- 移动时默认复制内容，旧目录永不自动删除

## 与旧版的区别

轻量版独立仓库：

```text
https://github.com/juedouwang/llmwiki-lite-codex-plugin
```

旧 Research 版仓库：

```text
https://github.com/juedouwang/llmwiki-research-codex-plugin
```

两条发布线使用不同仓库名、Marketplace 名和 Plugin 名，可以并行维护。

## GitHub Marketplace

本机使用 GitHub Marketplace，不再使用本地 Marketplace：

```powershell
codex plugin marketplace add https://github.com/juedouwang/llmwiki-lite-codex-plugin
codex plugin add llmwiki-lite@llmwiki-lite
```

修改并推送到 GitHub 后，刷新 Marketplace 并重新安装插件：

```powershell
codex plugin marketplace upgrade llmwiki-lite
codex plugin add llmwiki-lite@llmwiki-lite
```

重新安装后请新开 Codex 对话，让新的 Skill 和 MCP 配置生效。插件的界面元数据未改动，因此继续使用当前图标。

发布维护以 `main` 分支和版本标签（例如 `v0.3.1`）为准。每次推送 `main` 或提交版本标签都会由 GitHub Actions 自动执行验证；版本标签通过验证后自动创建 GitHub Release。

## 验证

```powershell
$env:PYTHONUTF8='1'
ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/tests
python -B plugins/llmwiki-lite/tests/smoke_test.py
python C:/Users/lyn/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/llmwiki-lite
```
