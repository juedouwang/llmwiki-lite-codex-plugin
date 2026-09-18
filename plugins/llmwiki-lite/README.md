# LLM Wiki Lite 科研助手

LLM Wiki Lite 是同一份源码、面向多个 AI 编码助手的轻量科研与项目知识插件：

- **Codex**：作为 Codex Plugin 安装（Marketplace 位于本仓库 `.agents/plugins/marketplace.json`）；
- **Claude Code**：作为 Claude Code Plugin 安装（Marketplace 位于本仓库 `.claude-plugin/marketplace.json`）；
- **opencode**：运行 `plugins/llmwiki-lite/opencode/install.py` 写入 `opencode.json` 配置。

三个平台共用同一套 Skill、MCP 工具、变更 Hook 和网页端。AI 助手负责理解研究问题、选择证据和写知识；程序只承担重复、确定性的文件与状态操作。

## 安装

### Codex

见仓库根目录 `README.md`：

```powershell
codex plugin marketplace add https://github.com/juedouwang/llmwiki-lite-codex-plugin
codex plugin add llmwiki-lite@llmwiki-lite
```

### Claude Code

```text
/plugin marketplace add juedouwang/llmwiki-lite-codex-plugin
/plugin install llmwiki-lite@llmwiki-lite
```

Claude Code 会自动发现本插件根目录下的 `skills/`、`hooks/hooks.json` 与 `.mcp.json`。

### opencode

```powershell
python plugins/llmwiki-lite/opencode/install.py
```

脚本把 Skill 路径、`mcp.llmwiki` 和变更 Hook 合并进 `~/.config/opencode/opencode.json`（可用 `--scope project`、`--config`、`--dry-run`、`--uninstall`）。细节见 `opencode/README.md`。

### 运行环境

三个平台都要求 `PATH` 中存在可用的 Python 3（默认命令为 `python`，只使用标准库，无需安装依赖）。Linux/macOS 若只有 `python3`，请为 `python` 建立符号链接，或把 MCP 配置改成 `python3`。

## 组成

```text
llmwiki-lite/
├─ .codex-plugin/plugin.json     # Codex 清单
├─ .claude-plugin/plugin.json    # Claude Code 清单
├─ .mcp.json                     # Codex 与 Claude Code 共用的 MCP 配置
├─ hooks/                        # 变更提示 Hook（Codex 与 Claude Code 共用）
├─ opencode/                     # opencode Hook 插件与安装脚本
├─ skills/
│  ├─ llmwiki-projects/
│  ├─ llmwiki-understand/
│  ├─ llmwiki-query/
│  ├─ llmwiki-maintain/
│  ├─ llmwiki-literature/
│  ├─ llmwiki-research-record/
│  └─ llmwiki-web/
├─ scripts/
└─ tests/
```

`.mcp.json` 与 `hooks/hooks.json` 使用同一段跨平台启动代码：Codex 通过插件根目录 / `${PLUGIN_ROOT}` 解析脚本，Claude Code 通过 `${CLAUDE_PLUGIN_ROOT}` 解析，opencode 由安装脚本写入绝对路径。

七个 Skill 可以独立触发：

1. `llmwiki-projects`：注册项目和管理存储位置；
2. `llmwiki-understand`：理解研究或软件项目并建立少量有用 Wiki；
3. `llmwiki-query`：从 Wiki 定位，再回到真实项目核验；
4. `llmwiki-maintain`：根据变化增量维护受影响页面；
5. `llmwiki-literature`：调研推荐论文，在用户选定后下载原文、生成中文精读并进入文献中心；
6. `llmwiki-research-record`：在用户明确要求后，把讨论整理为阶段性科研记录；
7. `llmwiki-web`：启动中文科研知识工作台。

## 中文输出约定

人类可读 Wiki、维护报告和查询回答默认使用简体中文，写作结构贴近中国研究生科研习惯：研究问题、方法、实验条件、证据、结果、结论、局限和下一步。

以下内容在需要精度时保留英文：代码、路径、命令、API/MCP 名、Schema 字段、算法名、模型名、论文标题、数据集名、指标名和不宜机械翻译的技术术语。

## 中文科研知识工作台

网页端是主要可视化和浏览入口，保持简洁、无前端构建链：

- **统一布局**：浅灰侧栏、白色内容区、中性色按钮与统一细线 SVG 图标（本地内置，不依赖图标字体）；一个导航入口，不再嵌套第二侧栏。
- **项目与知识库**：单列表与搜索筛选，移除仪表盘统计、重复分类和常驻说明；维护指令按需展开。
- **文献**：搜索、阅读状态、类型和收藏筛选；原文阅读及精读对照；辅助阅读与添加文献说明默认折叠。
- **科研记录**：按日期分组；AI 明确记录与手动块式笔记并存，保留截图、文字批注、自动保存、草稿、历史和冲突保护。
- **阅读**：正文优先，目录/记录信息收起，复制路径与打印放进“更多”，时间不写进笔记正文。
- **设置**：存储与项目配置按需展开，高级路径默认收起；移动端抽屉导航支持键盘与焦点管理。

界面重构不迁移数据、不改真实 Markdown 路径、不开放任意文件编辑。手动笔记仍是明确授权的编辑入口。网站仍只监听 `127.0.0.1`；公网访问、账号权限和多设备同步尚未实施。


文献辅助阅读推荐写入 `wiki_root`，并在 frontmatter 中使用相对项目根目录的 `paper_file` 或 `sources` 精确关联原文。未显式关联时只做保守的标题/路径候选匹配；置信度不足就保持“待关联”，不会伪造对应关系。系统不自动翻译 PDF，AI 助手必须先读原文，再把原文事实、解释和待验证推断分开写。科研过程记录写入 `wiki_root/records/YYYY/MM/YYYY-MM-DD.md`，同一天的多次明确记录追加到同一个日档，每条记录通过 `#entry_key` 区分；只有用户明确触发时才创建，不会自动保存所有对话。

启动方式：

```powershell
python -I -B scripts/web_server.py --host 127.0.0.1 --port 8765
```

也可让 AI 助手调用 `llmwiki_web_start`。

## 存储模型

每个注册项目有三个位置：

- `source_root`：真实研究/代码项目，插件只读理解；
- `wiki_root`：人类可读 Markdown；
- `state_root`：快照、哈希、配置和变化提示。
- `wiki_root/records/YYYY/MM/YYYY-MM-DD.md`：用户明确触发后生成的科研过程日档；同一天追加多条记录，不覆盖历史。

未配置全局默认时，新用户使用 `<project-root>/wiki`。配置例如 `E:\wiki_obsidian` 后，新项目默认使用其独立子目录。网页修改位置时默认复制原内容，并永不自动删除旧目录。

## 安全边界

- 网站只绑定 `127.0.0.1` / loopback；
- 所有文件读写做根目录和路径穿越检查；
- Wiki 写入仅发生在配置的 `wiki_root`；
- 文献原文只从已注册项目的 `source_root` 读取，只允许 PDF、EPUB、DOCX、HTML/HTM；路径穿越和符号链接会被拒绝；
- PDF 使用 inline 与 Range 流式响应供浏览器阅读，非 PDF 作为附件打开，源项目 HTML 不在站内执行；
- 网站永不修改、移动或删除文献原文；
- Hook 仅提供 dirty-path 提示且始终 fail-open（三个平台相同：脚本缺失或输入异常都不会影响宿主工具调用）；
- 不进行独立外部网络发送；
- 不引入 React/Vue、Node 构建链、数据库或外部 CDN；
- 不生成固定十五类页面，不批量制造空模板。

## 使用示例

```text
注册当前项目，把人类可读 Wiki 放到 E:\wiki_obsidian。
```

```text
理解这个研究项目，用简体中文建立少量真正有内容的 Wiki 页面。
```

```text
核验低纹理 RGB-D 配准方案的实验依据，并指出还缺什么实验。
```

```text
精读 references/ANoCo.pdf，生成简体中文辅助阅读并写入 Wiki，然后打开文献中心进行原文对照。
```

```text
检查最近变化，只更新受影响的 Wiki，然后打开中文科研工作台。
```

```text
记录刚才的讨论，保存这次阶段性理解、依据和下一步。
```

## 验证

```powershell
$env:PYTHONUTF8='1'
ruff check plugins/llmwiki-lite/scripts plugins/llmwiki-lite/opencode plugins/llmwiki-lite/tests
python -B plugins/llmwiki-lite/tests/smoke_test.py
python plugins/llmwiki-lite/opencode/install.py --dry-run
python C:/Users/lyn/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/llmwiki-lite
```

## 手动科研笔记（开发中 / 未发布）

- 时间戳仅作为笔记元数据保存，不写入正文或逐块展示；点击“更多 → 笔记信息”查看创建和修改时间（北京时间，到秒），导出放在 frontmatter。兼容旧笔记，隐藏此前自动生成的时间行，不删除用户手写内容。

在项目 **科研记录 → ＋ 手动记录** 中直接书写，不再必须通过 AI 对话。

- 在任意两个块之间点击 **＋**：插入文本、标题、图片、代码、提示或分隔线。文本支持 Markdown 预览；代码块只保存、不执行。
- 截图后 **Ctrl+V** 粘贴，或选择图片、拖入多张图片。支持 PNG/JPEG/GIF/WebP，单张不超过 10 MB；图片按内容去重，点击可放大。
- 每个块支持多条文字批注、上移/下移、复制、删除；“撤销块操作”恢复最近 40 次结构操作，文本输入仍支持浏览器原生撤销。
- 停止输入约 0.9 秒后自动保存；**Ctrl+S** 立即保存，**Ctrl+Enter** 在当前块后插入文本。
- 浏览器保留未保存草稿，刷新后可选择恢复；服务停止或上传失败会明确提示。浏览器清理站点数据后草稿可能丢失，重要内容以“已保存”为准。
- 多窗口编辑使用版本校验，冲突时暂停覆盖，提供服务器内容对比、保存为新笔记、导出草稿；不会静默覆盖另一个窗口或外部工具的修改。
- 每次更新会保留上一版本，界面展示最近 50 个版本，支持预览与恢复；支持专注模式和 Markdown 导出。

### 存储与兼容

- 手动笔记：`<wiki_root>/records/manual/<id>.md`。正文可直接阅读，末尾隐藏注释保留块结构、批注和一致性校验数据，无额外数据库。
- 截图：`<wiki_root>/records/assets/<内容哈希>.<扩展名>`。删除块不会立即删除图片，避免历史版本失去附件。
- 保存历史：`<wiki_root>/.notebook-history/<id>/*.snapshot`，不进入记录时间线。历史不自动清理；长期大量编辑时需要留意存储占用。
- AI 日档仍只追加，不被块编辑器改写；手动笔记与日档统一参与记录检索、标签筛选和时间线。
- 外部直接修改手动笔记的正文后，块编辑器会拒绝覆盖；可先从“查看原 Markdown”阅读原文，再人工合并或另建笔记。
- 导出 Markdown 包含相对图片路径，不内嵌图片；迁移时一并保留 `records/manual` 与 `records/assets` 的相对结构。
- 当前批注是**块级文字批注**，不是图片涂鸦/箭头标注，也不包含多人实时协作或 Jupyter 代码执行。

### 验证

`python -B tests/smoke_test.py` 同时运行笔记存储、冲突、上传、安全与兼容回归。
也可单独运行 `python -B tests/test_notebook.py`。

可选浏览器回归：准备已有 Node 与 Playwright，设置 `LLMWIKI_PLAYWRIGHT` 为模块路径，运行 `python -B tests/run_notebook_browser.py`。
可设置 `LLMWIKI_BROWSER_CHANNEL=chrome` 使用已安装 Chrome；`LLMWIKI_SCREENSHOT` 指定截图文件。测试不安装依赖，不使用真实项目数据，也不修改浏览器个人资料。

## 跨天科研进度与低打扰记录（未发布）

- “科研进度”替代旧待办展示：两周/四周时间轴、未排期任务、继续上次、停止点与下一步、状态即改即存。旧待办选择导入，原研究记录不变。
- 任务保存到 Wiki 根目录 `.research-progress/tasks.json`，最多 500 项；每任务最多保留 100 次显式变更快照。多窗口冲突保留当前输入并提示比较，不静默覆盖。
- 笔记直接粘贴或拖放图片，空块复用；新建图片块不强制打开文件管理器；“继续记录…”直接写，复制/移动/删除位于块菜单。截图不能覆盖已有图片，除非显式选择替换。
- **自动日报/周报尚未启用**：Hook 当前只有文件变化提示，不包含全部对话或科研验证结论。后续需要项目级对话授权、可溯源活动账本及无窗口定时运行端。方案与交互审查见仓库 `docs/research-continuity.md`。

验证：`python -B plugins/llmwiki-lite/tests/smoke_test.py` 包含笔记与科研进度回归；可选浏览器测试覆盖截图粘贴、多图/空块/拖放、旧待办导入、跨天上下文、并发冲突及移动端。
