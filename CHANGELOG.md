# Changelog

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
- 六个 Skill 默认使用简体中文产出人类可读知识，并保留精度敏感的英文技术术语。
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