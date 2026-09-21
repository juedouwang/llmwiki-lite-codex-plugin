# Changelog

## Unreleased — 2026-09-18

- 待办升级为两周/四周科研时间轴：持久化任务、未排期、继续上次、停止点/下一步、状态快捷保存、显式导入旧待办及多窗口冲突保护。
- 截图粘贴支持页面空白焦点、空块复用与多图顺序插入；图片块不再强制弹文件选择器；拖放按落点插入。修复新增文字块焦点，次要块操作收进菜单，标签选择即时筛选。
- 增加任务存储/安全/迁移回归与全站交互测试；新增科研连续性方案。自动日报/周报未启用，不新增后台 agent/定时进程。

- 统一导航、项目、收藏和展开/关闭图标为简约细线样式，移除字体符号混用；使用本地 SVG，无新增依赖。

- 网站重构为统一浅灰侧栏与单内容区：移除大横幅、仪表盘、双侧栏及重复分类，项目/记录/文献/知识页改为简洁列表。
- 笔记改为无装饰内容区，时间、历史、导出与专注放入“更多”；保留加号插块、截图、批注、自动保存和冲突保护。
- 抽离网页样式与交互脚本，增加移动端抽屉导航、键盘焦点约束及全站浏览器回归。只改本地开发版，不部署公网、不迁移研究数据。

- 插件通用化为 Codex、Claude Code、opencode 三个平台：新增 `.claude-plugin/plugin.json` 和 `.claude-plugin/marketplace.json`，Claude Code 可直接从同一仓库安装。
- `.mcp.json` 与 `hooks/hooks.json` 改为同一段跨平台启动代码，Codex 用插件根目录 / `${PLUGIN_ROOT}`，Claude Code 用 `${CLAUDE_PLUGIN_ROOT}`，不再维护平台分叉配置。
- 新增 opencode 集成：`opencode/llmwiki-hook.js` 提供同语义的 `tool.execute.after` 变更提示，`opencode/install.py` 合并写入 `opencode.json`（支持 `--scope`、`--config`、`--dry-run`、`--uninstall`，写入前自动备份）。
- 七个 Skill、MCP 描述与网页文案改为宿主中立表述（AI 助手），不再写死 Codex。
- 冒烟测试新增跨平台元数据、启动脚本与 opencode 安装器回归；CI 同步校验 Claude Code 清单与版本一致性。

- 时间戳仅作为笔记元数据保存，不写入正文或逐块展示；点击“笔记信息”查看创建和修改时间（北京时间，到秒），导出放在 frontmatter。兼容旧笔记，隐藏此前自动生成的时间行，不删除用户手写内容。

- 科研记录新增手动块式笔记、截图上传/粘贴/拖入、块级文字批注、顺序调整与撤销。
- 新增自动保存、独立浏览器草稿、版本历史恢复、并发编辑冲突保护和 Markdown 导出。
- 保持已有 Codex 日档不变，手动笔记统一进入时间线与检索。
- 新增受同源保护的本地写入接口、图片限制、原子文件保存与跨进程保存锁。
- 添加笔记回归及可选浏览器端到端测试；尚未发布，不改变已安装插件版本。

## 0.3.3 — 2026-08-14

- 科研记录页将“依据与关联材料”中的实验图片内联为小缩略图网格，点击放大、再次点击或按 Esc 关闭，替换原先的大图预览。
- 新增 `/source-asset/` 图片路由与 `IMAGE_MIME_TYPES`、`source_asset_url` 支持，图片按需懒加载、不再整页 base64 内嵌。
- 文本类关联文件改为等宽代码样式，避免路径中的下划线被误渲染为斜体。
- 保留非图片关联文件与 Wiki 页面在“关联材料”面板中的展示。

## 0.3.2 — 2026-08-05

- 新增独立 `llmwiki-research-record` Skill：只有用户明确触发时，才把 Codex 讨论整理为简洁的阶段性科研记录。
- 新增 `llmwiki_record_write`、`llmwiki_record_list` 和 `llmwiki_record_read` MCP 工具；记录按 `records/YYYY/MM/YYYY-MM-DD.md` 一日一档保存，同一天追加条目并通过 `#entry_key` 单独读取。
- 网页新增“科研记录”侧边栏、时间线列表、关键词检索和单条记录阅读页，保持中文控制台风格。
- 补充记录存储、路径穿越、网页路由和 MCP 冒烟测试。

## 0.3.1 — 2026-08-04

- 将科研工作台统一为更接近云厂商控制台的导航与信息架构，减少重复面包屑和冗余卡片。
- 优化项目切换、全局搜索、文献筛选、列表视图和移动端侧栏，突出研究项目与文献阅读主流程。
- 保留 PDF 原文、LLM 辅助阅读和原文/辅助阅读双栏对照，并补充回归测试覆盖。

## 0.3.0 — 2026-08-03

- 将网页端升级为简体中文优先的研究生科研知识工作台。
- 新增“我的研究项目”首页统计、项目研究台、科研流程动态分类和建议下一步。
- 新增中文科研检索、项目筛选和研究类别标签。
- 新增 Obsidian 风格文献中心：左侧文献库按阅读状态和类型筛选，支持搜索、结果计数及卡片/列表切换。
- 新增独立 `llmwiki-literature` Skill，固化“调研推荐 → 用户选择 → 下载原文 → 中文精读 → 网页入库”流程。
- 自动发现项目内 PDF、EPUB、DOCX 和 HTML，提供 PDF 站内阅读与原文/简体中文辅助阅读双栏对照。
- 新增保守的文献—笔记关联：优先使用 `paper_file`/`sources`，无法可靠判断时保持待关联，并提供可复制的中文精读 Prompt。
- 增强 Markdown 阅读页：中文导航、本页目录、更新时间、阅读时长、复制路径和打印/PDF。
- 重构存储设置，把“人类可读 Wiki 目录”和“机器状态目录”分开展示，机器状态进入高级设置。
- 七个 Skill 默认使用简体中文产出人类可读知识，并保留精度敏感的英文技术术语。
- 修复 Plugin 清单和旧文档中的中文乱码。
- 继续保持 Lite 边界：无前端构建链、无数据库、无外部 CDN、无任意 Markdown 网页编辑器。

## 0.2.0 — 2026-07-20

- Rebuilt as the lightweight `LLM Wiki Lite` Codex Plugin.
- Split workflows into five independent Skills.
- Added central project registration and selectable Wiki/state storage roots.
- Added deterministic MCP filesystem and registry tools.
- Added fail-open dirty-path Hook support.
- Added loopback-only Markdown website with project registration and storage settings.
- Removed the legacy fixed-page and complex Research Core architecture from this release line.
