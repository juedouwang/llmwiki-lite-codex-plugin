# LLM Wiki Lite

一个轻量、可直接安装到 Codex 的 Plugin。Codex/LLM 负责阅读、理解、推理与知识写作；本地程序只负责项目注册、文件扫描、搜索、读取、hash、增量检测、安全写入和网站展示等机械工作。

新实现与旧的 `llmwiki-research-codex-plugin` 完全分开：

- 本仓库 Plugin：`plugins/llmwiki-lite/`
- 产品契约：`docs/product-contract.md`
- 旧 Research 版：https://github.com/juedouwang/llmwiki-research-codex-plugin

## 架构

```text
Codex / LLM
├─ llmwiki-projects   项目注册、选择和存储位置
├─ llmwiki-understand 项目理解与有用 Wiki 写作
├─ llmwiki-query      基于 Wiki 并回到真实源文件回答
├─ llmwiki-maintain   增量变化与最小必要维护
├─ llmwiki-web        本地网站启动和使用
├─ MCP                机械文件、注册表、存储与网站启动工具
└─ Hook               可选、fail-open 的 dirty-path 提示
```

不再生成固定十五类页面，不建设 Claim/Evidence 状态机，不用无 LLM 的程序假装理解项目。

## 安装

GitHub 安装：

```powershell
codex plugin marketplace add https://github.com/juedouwang/llmwiki-lite-codex-plugin
codex plugin add llmwiki-lite@llmwiki-lite
```

本地开发安装：

```powershell
codex plugin marketplace add E:\GitHub\llm-wiki-agent\next\llmwiki-lite-codex-plugin
codex plugin add llmwiki-lite@llmwiki-lite
```

要求：Codex Plugin 支持、`PATH` 中可执行的 Python 3.10+。不需要额外 Python 包、API key 或独立数据库。

## 项目注册与存储

全局轻量配置默认位于：

- Windows：`%LOCALAPPDATA%\LLMWiki\`
- 其他系统：`~/.local/share/llmwiki/`
- 可用 `LLMWIKI_HOME` 覆盖。

```text
<LLMWIKI_HOME>/
├─ registry.json
├─ settings.json
└─ projects/<project_id>/state/
```

每个项目保存三个明确位置：

```text
source_root  只读理解对象/源项目
state_root   manifest、config、events 等机器状态
wiki_root    人和 Agent 阅读的 Markdown
```

注册不会扫描或总结项目。项目 ID 由项目名称 slug 和规范化源路径 hash 稳定生成。

Wiki 位置选择顺序：

1. 注册时显式传入 `wiki_root`；
2. 全局配置了 `default_wiki_root` 时，使用 `<default_wiki_root>/<project_id>`；
3. 其他新用户默认使用 `<project-root>/wiki`。

当前开发机器可配置为 `E:\wiki_obsidian`，但该路径不是写死的跨用户默认值。

移动 Wiki 或状态目录时默认复制现有内容、拒绝向非空目标合并、切换注册表并保留旧目录；取消注册也不会删除任何文件。

## MCP 工具

项目与设置：

- `llmwiki_project_register`
- `llmwiki_project_list`
- `llmwiki_project_get`
- `llmwiki_project_select`
- `llmwiki_project_storage_update`
- `llmwiki_project_unregister`
- `llmwiki_settings_get`
- `llmwiki_settings_update`
- `llmwiki_web_start`

项目机械操作：

- `llmwiki_init`
- `llmwiki_status`
- `llmwiki_snapshot`
- `llmwiki_files`
- `llmwiki_search`
- `llmwiki_read`
- `llmwiki_wiki_write`
- `llmwiki_wiki_list`
- `llmwiki_wiki_check`

MCP 不做页面分类、重要性判断、项目总结或科学结论判断。

## 本地网站

通过 Codex 调用 `llmwiki_web_start`，或直接运行：

```powershell
python -I -B scripts/web_server.py --open
```

默认地址：`http://127.0.0.1:8765/`。网站只允许绑定 loopback，不访问外部网络，不使用数据库。

网站包括：

- 已注册项目列表、源目录、Wiki 目录、状态目录；
- 项目页、页面列表、快照状态、最近变化提示；
- 跨项目和项目内 Wiki 搜索；
- Markdown frontmatter、标题、段落、列表、任务列表、引用、Obsidian callout、表格、代码块、行内代码、链接、图片、分隔线和 `[[wikilinks]]`；
- 设置页：修改全局默认 Wiki 根目录，注册项目，修改项目 `wiki_root` / `state_root`，取消注册。

网站当前不编辑 Markdown 正文。Markdown 文件仍是唯一知识真相源，由 Codex 和用户在文件层维护。

## 使用示例

```text
注册当前项目，把人类可读 Wiki 放到 E:\wiki_obsidian。
```

```text
理解这个项目，只建立真正有内容的 Wiki 页面。
```

```text
这个项目的数据如何进入核心模型？请核验真实代码后回答。
```

```text
检查最近变化，只更新受影响的 Wiki。
```

```text
打开 LLM Wiki 网站。
```

## 安全边界

- 所有读取和写入都做根目录约束与路径穿越检查；
- Wiki 写入只发生在配置的 `wiki_root`；
- 网站附件只从对应项目的 `wiki_root` 提供；
- 网站只绑定 `127.0.0.1` / loopback；
- Hook 失败不会阻断 Codex；
- Hook 只是提示，准确变化以 snapshot 为准；
- 程序不进行独立外部网络发送。

## 验证

```powershell
$env:PYTHONUTF8='1'
ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/tests
python -B plugins/llmwiki-lite/tests/smoke_test.py
python C:/Users/lyn/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/llmwiki-lite
```
