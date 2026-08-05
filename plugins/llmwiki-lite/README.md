# LLM Wiki Lite 科研助手

LLM Wiki Lite 是可直接安装到 Codex 的轻量 Plugin。它不替代 LLM 推理：Codex 负责理解研究问题、选择证据和写知识；程序只承担重复、确定性的文件与状态操作。

## 组成

```text
llmwiki-lite/
├─ .codex-plugin/plugin.json
├─ .mcp.json
├─ skills/
│  ├─ llmwiki-projects/
│  ├─ llmwiki-understand/
│  ├─ llmwiki-query/
│  ├─ llmwiki-maintain/
│  ├─ llmwiki-literature/
│  ├─ llmwiki-research-record/
│  └─ llmwiki-web/
├─ hooks/
├─ scripts/
└─ tests/
```

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

- **我的研究项目**：项目数、知识页、源文件、待核对变化；
- **项目研究台**：按科研流程动态归类已有 Markdown，并显示最近更新、建议下一步和可复制给 Codex 的维护 Prompt；
- **文献中心**：采用接近 Obsidian 的左侧文献库导航，可按已精读/待精读和文献类型快速筛选，并支持卡片/列表视图；
- **原文与精读**：自动发现项目目录中的 PDF、EPUB、DOCX 和 HTML，直接阅读 PDF 原文，并把原文与简体中文辅助阅读左右对照；
- **科研记录**：在用户明确说“记录刚才的讨论”后，Codex 总结阶段性理解、依据、决策、未解决问题和下一步；网页按时间线查看记录，MCP 只负责追加、列出和读取；
- **科研检索**：跨项目或按项目检索论文、方法、实验、结果、结论和计划；
- **Markdown 阅读**：完整渲染正文，并提供中文导航、目录、分类、更新时间、阅读时长、复制路径和打印/PDF；
- **存储设置**：修改全局默认 Wiki 根目录、项目 `wiki_root` 和高级 `state_root`。

科研分类只是网页浏览视图，不会改变真实目录，也不会强制生成固定类型页面。网站不编辑任意 Markdown 正文；Markdown 文件仍是唯一知识源。

文献辅助阅读推荐写入 `wiki_root`，并在 frontmatter 中使用相对项目根目录的 `paper_file` 或 `sources` 精确关联原文。未显式关联时只做保守的标题/路径候选匹配；置信度不足就保持“待关联”，不会伪造对应关系。系统不自动翻译 PDF，Codex 必须先读原文，再把原文事实、解释和待验证推断分开写。科研过程记录写入 `wiki_root/records/YYYY/MM/YYYY-MM-DD.md`，同一天的多次明确记录追加到同一个日档，每条记录通过 `#entry_key` 区分；只有用户明确触发时才创建，不会自动保存所有对话。

启动方式：

```powershell
python -I -B scripts/web_server.py --host 127.0.0.1 --port 8765
```

也可让 Codex 调用 `llmwiki_web_start`。

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
- Hook 仅提供 dirty-path 提示且始终 fail-open；
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
ruff check scripts tests
python -B tests/smoke_test.py
python C:/Users/lyn/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```