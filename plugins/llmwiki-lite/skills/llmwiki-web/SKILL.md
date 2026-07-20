---
name: llmwiki-web
description: Start and use the simple local LLM Wiki website. Use when the user asks to visualize Wiki Markdown, browse registered projects, search pages in a browser, or change Wiki storage locations from the web interface.
---
# Local Wiki website

The website is a simple loopback visualization layer, not a second knowledge engine.

1. Call `llmwiki_web_start` with optional `home` or `port` when the user asks to open the site.
2. Return the URL from the tool. It binds to `127.0.0.1` only.
3. If a shell launch is needed, run `python -I -B <plugin>/scripts/web_server.py`; keep the host loopback.

The site shows registered projects, source/Wiki/state locations, status, Markdown page lists, search, headings, paragraphs, lists, task lists, quotes, callouts, tables, code, links, images, frontmatter, and `[[wikilinks]]`. It renders the full UTF-8 Markdown body.

Use Settings to edit the global default Wiki root or an individual project's `wiki_root` and `state_root`. Existing content is copied by default, non-empty destination merges are refused, registry state is updated, and old directories are never deleted. The web form does not edit Markdown bodies.
