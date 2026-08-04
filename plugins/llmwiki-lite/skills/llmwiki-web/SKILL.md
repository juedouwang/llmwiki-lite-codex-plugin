---
name: llmwiki-web
description: Start and use the Chinese-first local LLM Wiki research cockpit. Use when the user asks to visualize Wiki Markdown, browse research projects, search papers/methods/experiments/results, or change Wiki storage locations from the web interface.
---
# 中文科研知识工作台

The website is a simple loopback visualization and interaction layer, not a second knowledge engine.

1. Call `llmwiki_web_start` with optional `home` or `port` when the user asks to open the site.
2. Return the URL from the tool. It binds to `127.0.0.1` only.
3. If a shell launch is needed, run `python -I -B <plugin>/scripts/web_server.py`; keep the host loopback.

The Chinese-first site provides:

- “我的研究项目”首页，展示知识页、源文件、待核对变化和最近扫描；
- 项目“文献中心”，自动发现 PDF/EPUB/DOCX/HTML，显示原文、LLM 辅助阅读及配对状态；
- PDF 原文阅读页，以及左侧原文、右侧中文精读 Markdown 的双栏对照阅读；
- 项目研究台，按研究总览、文献、方法、数据、实验、结果、结论、计划等科研流程动态归类已有 Markdown；
- 中文科研检索，可按项目查找论文、方法、实验、结果和结论；
- Markdown 阅读页，包含中文面包屑、项目侧栏、本页目录、更新时间、预计阅读时长、复制路径和打印/PDF；
- 存储设置，可修改人类可读 Wiki 目录和高级机器状态目录。

Paper files are served read-only from the registered `source_root` through an extension allowlist and path/symlink checks. PDFs use browser-inline streaming with byte ranges; non-PDF formats are downloaded/opened without executing source HTML. Assistant-reading Markdown from the source project is read-only, while durable notes should be stored in `wiki_root` with an exact `paper_file` binding.
The categories are a browser view only. They do not create fixed template pages or change real Markdown paths. Existing English paper titles, algorithm names, code, paths, commands, API names, and other precision-sensitive technical terms remain unchanged.

Storage changes copy existing content by default, refuse unsafe non-empty destination merges, update the registry, and never delete old directories. The website does not edit arbitrary Markdown bodies; Markdown remains the knowledge source maintained by Codex and the user.