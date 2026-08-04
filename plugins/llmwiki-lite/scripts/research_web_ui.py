"""Chinese-first research cockpit pages for the loopback LLM Wiki website."""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from llmwiki_core import LLMWikiError, status, wiki_list
from llmwiki_registry import get_project, list_projects, load_settings
from markdown_renderer import render_markdown

STYLE = """
:root{--bg:#f4f6f8;--panel:#fff;--text:#18202a;--muted:#677281;--line:#dde3ea;--accent:#245b91;--accent-dark:#19446e;--soft:#eef5fb;--warm:#fff8e8;--success:#eaf6ee;--danger:#a43434;--shadow:0 1px 3px #17212b10}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}header{position:sticky;top:0;z-index:20;background:#fffffffa;border-bottom:1px solid var(--line)}nav{max-width:1280px;margin:auto;padding:11px 24px;display:flex;align-items:center;gap:19px}.brand{font-weight:750;color:var(--text);letter-spacing:.02em}.brand small{display:block;color:var(--muted);font-size:10px;font-weight:500;line-height:1.1}.spacer{flex:1}main{max-width:1280px;margin:26px auto;padding:0 24px 54px}footer{max-width:1280px;margin:auto;padding:20px 24px 32px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}.hero{display:flex;justify-content:space-between;gap:24px;align-items:end;margin:0 0 22px}.hero h1{font-size:30px;margin:0 0 4px;line-height:1.25}.hero p{margin:0;color:var(--muted)}.eyebrow{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.12em}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(285px,1fr));gap:16px}.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px;box-shadow:var(--shadow)}.card h2,.panel h2,.panel h3{margin-top:0}.project-card{display:flex;flex-direction:column;min-height:245px}.project-card .actions{margin-top:auto}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0 0 22px}.stat{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:14px}.stat strong{display:block;font-size:25px;line-height:1.2}.stat span{color:var(--muted);font-size:12px}.meta,.muted{color:var(--muted);font-size:13px}.path{font-family:Consolas,"SFMono-Regular",monospace;overflow-wrap:anywhere}.badge,.category-tag{display:inline-block;padding:2px 8px;background:var(--soft);border:1px solid #dbe8f4;border-radius:99px;font-size:12px;margin:0 5px 4px 0}.badge.warn{background:var(--warm);border-color:#eadbad}.actions{display:flex;flex-wrap:wrap;gap:9px;margin-top:14px}.button,button{display:inline-block;border:1px solid #b8c4d1;border-radius:6px;padding:7px 12px;background:#fff;color:var(--text);cursor:pointer;font:inherit}.button:hover,button:hover{text-decoration:none;background:#f6f8fa}.primary{color:#fff!important;background:var(--accent)!important;border-color:var(--accent)!important}.primary:hover{background:var(--accent-dark)!important}.danger{color:var(--danger)!important;border-color:#d8abab!important}label{display:block;font-weight:650;margin:12px 0 5px}input[type=text],input[type=number],input[type=search],select{width:100%;padding:9px 10px;border:1px solid #c8d0d9;border-radius:6px;background:#fff;font:inherit}input[type=checkbox]{width:auto;margin-right:7px}form.inline{display:flex;gap:8px;align-items:center}form.inline input{width:auto;min-width:150px;flex:1}.notice{padding:10px 13px;background:var(--success);border:1px solid #bddcc8;border-radius:7px;margin-bottom:16px}.error{background:#fff0f0;border-color:#e2b8b8}.research-layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:18px;align-items:start}.reading-layout{display:grid;grid-template-columns:265px minmax(0,1fr) 220px;gap:18px;align-items:start}.sidebar{position:sticky;top:72px;max-height:calc(100vh - 96px);overflow:auto}.page-list{list-style:none;padding:0;margin:0}.page-list li{border-bottom:1px solid #edf0f3;padding:8px 0}.page-list li:last-child{border-bottom:0}.page-list a{display:block;line-height:1.4}.category-block{margin:0 0 18px}.category-block h3{font-size:15px;margin:0 0 8px;padding-bottom:6px;border-bottom:1px solid var(--line)}.category-count{float:right;color:var(--muted);font-weight:400;font-size:12px}.workflow-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.workflow-card{border:1px solid var(--line);border-radius:8px;padding:14px;background:#fff}.workflow-card h3{margin:0 0 8px;font-size:16px}.workflow-card ul{margin:0;padding-left:19px}.workflow-card li{margin:3px 0}.section-title{margin:28px 0 12px}.split{display:grid;grid-template-columns:1fr 1fr;gap:16px}.storage-grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}.storage-grid>div{min-width:0}.prompt{position:relative;padding:14px 52px 14px 14px;background:#f7f9fb;border:1px solid var(--line);border-radius:7px;white-space:pre-wrap;font-family:Consolas,monospace;font-size:13px}.prompt button{position:absolute;right:8px;top:8px;padding:4px 8px;font-family:inherit;font-size:12px}.recent-list{list-style:none;margin:0;padding:0}.recent-list li{padding:9px 0;border-bottom:1px solid #edf0f3}.recent-list li:last-child{border-bottom:0}.document{min-width:0;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:28px 34px;box-shadow:var(--shadow);font-size:16px;line-height:1.82}.document h1,.document h2,.document h3,.document h4{line-height:1.35;margin-top:1.55em;scroll-margin-top:82px}.document h1:first-child{margin-top:0}.document h2{padding-bottom:.32em;border-bottom:1px solid #e6eaee}.document pre{overflow:auto;margin:0;padding:16px;background:#15191f;color:#e7edf5;border-radius:7px;line-height:1.55}.document code{font-family:Consolas,monospace;font-size:.92em;background:#eef1f4;padding:.12em .3em;border-radius:4px}.document pre code{background:none;padding:0}.code-block{position:relative;margin:1em 0}.code-language{position:absolute;right:10px;top:6px;color:#aab6c4;font-size:11px}.document blockquote{margin:1em 0;padding:3px 16px;border-left:4px solid #b8c3d0;color:#4e5966}.callout{margin:1em 0;border:1px solid #bfd0e7;border-left:4px solid var(--accent);background:#f5f9ff;border-radius:6px;padding:12px 15px}.callout-title{font-weight:700}.frontmatter{background:#f8fafc;border:1px solid var(--line);border-radius:7px;padding:9px 12px;margin-bottom:20px}.frontmatter summary{cursor:pointer;font-weight:600}.frontmatter dl{display:grid;grid-template-columns:minmax(100px,180px) 1fr}.frontmatter dt,.frontmatter dd{border-top:1px solid #e8ebef;padding:5px 0;margin:0;overflow-wrap:anywhere}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}th,td{border:1px solid var(--line);padding:7px 9px;text-align:left}th{background:#f1f4f7}figure{margin:1em 0}figure img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px}figcaption{color:var(--muted);font-size:12px}.wikilink{background:#eef4ff;padding:0 3px;border-radius:3px}.doc-toolbar{display:flex;justify-content:space-between;gap:14px;align-items:center;margin-bottom:12px}.breadcrumbs{color:var(--muted);font-size:13px}.toc{font-size:13px}.toc ul{list-style:none;margin:0;padding:0}.toc li{margin:5px 0}.toc .level-3{padding-left:12px}.toc .level-4{padding-left:24px}.search-result{padding:14px 0;border-bottom:1px solid var(--line)}.search-result mark{background:#fff0a8}.search-result h2{font-size:18px;margin:0 0 4px}.empty{padding:30px;text-align:center;color:var(--muted)}details.settings{margin-top:15px;border-top:1px solid var(--line);padding-top:12px}details.settings summary{cursor:pointer;font-weight:650}.help{padding:12px 14px;background:#f7f9fb;border-left:3px solid #9db6cf;color:#44505d}.page-filter{margin-bottom:10px}.mobile-only{display:none}@media(max-width:980px){.reading-layout{grid-template-columns:230px minmax(0,1fr)}.toc-panel{display:none}.research-layout{grid-template-columns:1fr}.sidebar{position:static;max-height:none}.stats{grid-template-columns:repeat(2,1fr)}}@media(max-width:720px){nav{padding:10px 14px;gap:12px;flex-wrap:wrap}nav form{order:5;width:100%}main{padding:0 14px 40px;margin-top:18px}.hero{display:block}.hero .actions{margin-top:12px}.stats,.split,.storage-grid{grid-template-columns:1fr}.reading-layout{grid-template-columns:1fr}.reading-layout>.sidebar{display:none}.document{padding:20px 17px}.mobile-only{display:inline-block}}.literature-toolbar{display:flex;align-items:end;justify-content:space-between;gap:18px;margin-bottom:16px}.literature-toolbar h2{margin-bottom:2px}.literature-toolbar .page-filter{max-width:360px;margin:0}.literature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px}.paper-card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px;box-shadow:var(--shadow);display:flex;flex-direction:column;min-height:290px}.paper-card h2{font-size:18px;line-height:1.45;margin:10px 0 6px}.paper-card .actions{margin-top:auto}.paper-notes{margin:12px 0;padding:11px 13px;background:#f7f9fb;border-radius:7px}.paper-notes ul{margin:5px 0 0;padding-left:20px}.paper-prompt{margin:12px 0}.paper-prompt summary{cursor:pointer;font-weight:650;color:var(--accent)}.paper-viewer{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}.paper-viewer-head{display:flex;justify-content:space-between;gap:20px;align-items:end;padding:16px 18px;border-bottom:1px solid var(--line)}.paper-viewer-head h1{font-size:21px;margin:5px 0 0}.paper-frame{display:block;width:100%;height:calc(100vh - 205px);min-height:650px;border:0;background:#e9edf1}.compare-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;height:calc(100vh - 165px);min-height:700px}.compare-pane{min-width:0;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}.compare-pane-head{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:12px 15px;border-bottom:1px solid var(--line)}.compare-pane-head h2{font-size:17px;line-height:1.35;margin:2px 0}.compare-pane .paper-frame{height:100%;min-height:0;flex:1}.note-pane{overflow:hidden}.note-document{padding:22px 28px;overflow:auto;flex:1}.note-document h1{font-size:25px}.note-selector{display:flex;align-items:end;gap:9px;margin:0 0 12px}.note-selector label{margin:0}.note-selector select{max-width:520px}.document-fallback{margin:auto}.document-fallback h2{font-size:18px}@media(max-width:980px){.compare-layout{grid-template-columns:1fr;height:auto}.compare-pane{min-height:680px}.note-pane{min-height:520px}.literature-grid{grid-template-columns:1fr}}@media(max-width:720px){.literature-toolbar,.paper-viewer-head{display:block}.literature-toolbar .page-filter{max-width:none;margin-top:12px}.paper-frame{height:70vh;min-height:480px}.compare-pane{min-height:520px}.note-selector{display:block}.note-selector select{margin:6px 0}.note-document{padding:18px 16px}}@media print{header,footer,.sidebar,.toc-panel,.doc-toolbar,.no-print{display:none!important}body,main{background:#fff;margin:0;padding:0}.reading-layout{display:block}.document{border:0;box-shadow:none;padding:0;font-size:12pt}}
/* Obsidian-inspired literature workspace: quiet chrome, persistent navigation, content-first panes. */
.literature-app{display:grid;grid-template-columns:250px minmax(0,1fr);gap:0;min-height:calc(100vh - 170px);background:var(--panel);border:1px solid var(--line);border-radius:6px;overflow:visible;box-shadow:none}
.literature-sidebar{position:sticky;top:68px;align-self:start;height:calc(100vh - 88px);overflow:auto;background:#f7f7f8;border-right:1px solid var(--line);padding:14px 10px;border-radius:6px 0 0 6px}
.library-title{display:flex;align-items:center;gap:9px;padding:4px 8px 13px;border-bottom:1px solid var(--line);margin-bottom:12px}.library-title strong{display:block;line-height:1.3}.library-title span:last-child{display:block;color:var(--muted);font-size:11px}.library-mark{display:grid!important;place-items:center;width:28px;height:28px;border-radius:7px;background:#1677ff;color:#fff!important;font-weight:800;font-size:13px!important;flex:0 0 auto}
.library-search{margin:0 4px 12px!important;background:#fff!important}.library-nav-group{margin:13px 0}.library-nav-group h2{padding:0 9px;margin:0 0 4px;color:#86909c;font-size:11px;line-height:1.5;letter-spacing:.08em;text-transform:uppercase}.library-nav-item{display:flex;width:100%;align-items:center;justify-content:space-between;gap:10px;border:0;border-radius:6px;padding:6px 9px;background:transparent;color:#1f2329;text-align:left;font-size:13px;line-height:1.4}.library-nav-item:hover{background:#f2f7ff;text-decoration:none}.library-nav-item.is-active{background:#e8f3ff;color:#0958d9;font-weight:700}.library-nav-item .nav-count{color:#86909c;font-size:11px;font-variant-numeric:tabular-nums}.library-nav-separator{height:1px;background:var(--line);margin:12px 8px}.library-sidebar-help{padding:10px 9px;color:var(--muted);font-size:11px;line-height:1.55}
.literature-content{min-width:0;padding:22px 24px 32px;background:#fbfbfc;border-radius:0 6px 6px 0}.library-breadcrumbs{color:var(--muted);font-size:12px;margin-bottom:12px}.library-header{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;padding-bottom:18px;border-bottom:1px solid var(--line)}.library-header h1{font-size:28px;line-height:1.25;margin:2px 0 5px}.library-header p{margin:0;color:var(--muted)}.library-header-actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.library-summary{display:flex;gap:8px;flex-wrap:wrap;margin:15px 0}.summary-pill{display:inline-flex;align-items:baseline;gap:5px;padding:5px 9px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--muted);font-size:12px}.summary-pill strong{color:var(--text);font-size:14px}.library-results-head{display:flex;justify-content:space-between;gap:16px;align-items:center;margin:18px 0 10px}.library-results-head h2{font-size:17px;margin:0}.view-switch{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}.view-switch button{border:0;border-radius:0;padding:5px 9px;font-size:12px}.view-switch button+button{border-left:1px solid var(--line)}.view-switch button.is-active{background:#e7dff2;color:#4f3475;font-weight:700}
.literature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:12px}.literature-grid.is-list{grid-template-columns:1fr}.paper-card{position:relative;background:#fff;border:1px solid var(--line);border-radius:8px;padding:15px 16px;box-shadow:none;display:flex;flex-direction:column;min-height:260px;transition:border-color .15s ease,box-shadow .15s ease,transform .15s ease}.paper-card:hover{border-color:#b7c7e5;box-shadow:0 3px 10px rgba(31,35,41,.08);transform:translateY(-1px)}.paper-card h2{font-size:16px;line-height:1.45;margin:8px 0 5px}.paper-card .actions{margin-top:auto}.paper-card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}.paper-file-type{display:inline-grid;place-items:center;min-width:34px;height:22px;border-radius:4px;background:#e8f3ff;color:#0958d9;font-size:10px;font-weight:800;letter-spacing:.04em}.reading-state{font-size:11px;color:#4e765b}.reading-state.pending{color:#9a6b20}.paper-notes{margin:10px 0;padding:9px 11px;background:#f7faff;border-radius:6px;border-left:3px solid #91caff}.paper-notes ul{margin:4px 0 0;padding-left:18px}.paper-prompt{margin:10px 0}.paper-path{color:#85808b;font-size:11px;line-height:1.45}.literature-grid.is-list .paper-card{min-height:0;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 18px}.literature-grid.is-list .paper-card>.actions{grid-column:2;grid-row:1/6;align-self:center;margin:0;flex-direction:column}.literature-grid.is-list .paper-notes,.literature-grid.is-list .paper-prompt{max-width:820px}.literature-filter-empty{display:none;margin:18px 0}.literature-filter-empty.is-visible{display:block}.literature-support{margin-top:22px}.literature-support details+details{margin-top:8px}.workflow-steps{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;list-style:none;padding:0;margin:14px 0}.workflow-steps li{position:relative;padding:10px 10px 10px 36px;border:1px solid var(--line);border-radius:7px;background:#fff;font-size:12px}.workflow-steps strong{display:block;font-size:13px}.workflow-steps span{position:absolute;left:10px;top:10px;display:grid;place-items:center;width:19px;height:19px;border-radius:50%;background:#7656a7;color:#fff;font-size:10px;font-weight:800}
@media(max-width:1050px){.literature-app{grid-template-columns:220px minmax(0,1fr)}.workflow-steps{grid-template-columns:repeat(3,1fr)}}
@media(max-width:780px){.literature-app{display:block}.literature-sidebar{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line);border-radius:6px 6px 0 0}.library-nav{display:grid;grid-template-columns:1fr 1fr;gap:8px}.library-nav-group{margin:5px 0}.literature-content{padding:17px 14px 24px;border-radius:0 0 6px 6px}.library-header{display:block}.library-header-actions{justify-content:flex-start;margin-top:12px}.literature-grid{grid-template-columns:1fr}.literature-grid.is-list .paper-card{display:flex}.workflow-steps{grid-template-columns:1fr 1fr}}
@media(max-width:520px){.library-nav{display:block}.workflow-steps{grid-template-columns:1fr}.library-summary{display:grid;grid-template-columns:1fr 1fr}.library-results-head{align-items:flex-start}.paper-card{padding:14px}.view-switch{display:none}}

/* Console shell: dense, predictable, enterprise-style navigation. */
:root{--accent:#1677ff;--accent-dark:#0958d9;--bg:#f5f7fa;--panel:#fff;--text:#1f2329;--muted:#86909c;--line:#e5e6eb;--soft:#f2f3f5;--sidebar:#182230;--sidebar-muted:#aeb8c4;--sidebar-active:#1677ff;--shadow:0 1px 2px rgba(31,35,41,.04)}
html,body{min-height:100%;background:var(--bg)}
body{font-size:14px;line-height:1.6;color:var(--text)}
body.console-locked{overflow:hidden}
.console-shell{min-height:100vh;background:var(--bg)}
.console-topbar{position:sticky;top:0;z-index:50;height:64px;background:#fff;border-bottom:1px solid var(--line);box-shadow:0 1px 4px rgba(31,35,41,.05)}
.console-topbar-inner{height:64px;display:flex;align-items:center;gap:18px;padding:0 24px}
.console-brand{display:flex;align-items:center;gap:10px;min-width:225px;color:var(--text);text-decoration:none}
.console-brand:hover{text-decoration:none}
.console-brand-mark{display:grid;place-items:center;width:30px;height:30px;border-radius:6px;background:#1677ff;color:#fff;font-size:12px;font-weight:800;letter-spacing:-.04em}
.console-brand-copy{display:flex;align-items:baseline;gap:8px;white-space:nowrap}
.console-brand-name{font-size:16px;font-weight:700;letter-spacing:.01em}
.console-brand-product{color:var(--muted);font-size:12px}
.console-project-switcher{position:relative;min-width:205px}
.console-project-switcher summary{display:flex;align-items:center;gap:8px;min-height:36px;padding:5px 10px;border:1px solid var(--line);border-radius:5px;background:#fff;cursor:pointer;list-style:none}
.console-project-switcher summary::-webkit-details-marker{display:none}.console-project-switcher-icon{display:grid;place-items:center;width:20px;height:20px;border-radius:4px;background:#e8f3ff;color:#1677ff;font-size:8px;flex:0 0 auto}.console-project-switcher-icon::before{content:"";display:block;width:6px;height:6px;border-radius:1px;background:currentColor}
.console-project-switcher summary:after{content:'v';margin-left:auto;color:var(--muted);font-size:14px}
.console-project-switcher summary:hover{border-color:#b7c7e5;background:#f7faff}
.console-project-switcher-label{display:block;color:var(--muted);font-size:11px;line-height:1.1}
.console-project-switcher-name{display:block;max-width:165px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600;line-height:1.25}
.console-project-menu{position:absolute;top:43px;left:0;z-index:60;width:290px;padding:6px;background:#fff;border:1px solid var(--line);border-radius:6px;box-shadow:0 8px 24px rgba(31,35,41,.14)}
.console-project-menu a{display:block;padding:9px 10px;border-radius:4px;color:var(--text);font-size:13px}
.console-project-menu a:hover{background:#f2f7ff;text-decoration:none}
.console-project-menu a.is-current{background:#e8f3ff;color:#0958d9;font-weight:650}
.console-project-menu .path{display:block;margin-top:2px;color:var(--muted);font-size:11px;font-weight:400}
.console-global-search{display:flex;align-items:center;gap:8px;max-width:420px;flex:1;margin-left:auto}
.console-global-search input{height:36px;padding:7px 12px;border-radius:5px;background:#f7f8fa;border-color:#e5e6eb}
.console-global-search input:focus{background:#fff;border-color:#1677ff;box-shadow:0 0 0 2px rgba(22,119,255,.12);outline:0}
.console-global-search button{height:36px;padding:0 14px;border-radius:5px;white-space:nowrap}
.console-runtime{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px;white-space:nowrap}
.console-runtime-dot{width:7px;height:7px;border-radius:50%;background:#27ae60;box-shadow:0 0 0 3px #e9f8ee}
.console-topbar-link{color:#4e5969;font-size:13px;white-space:nowrap}
.console-topbar-link:hover{color:#1677ff;text-decoration:none}
.console-menu-button{display:none;border:0!important;background:transparent!important;padding:5px!important;font-size:22px;line-height:1;color:#1f2329}
.console-body{display:grid;grid-template-columns:236px minmax(0,1fr);min-height:calc(100vh - 64px)}
.console-sidebar{position:sticky;top:64px;height:calc(100vh - 64px);overflow-y:auto;background:var(--sidebar);color:#fff;padding:16px 12px 22px}
.console-sidebar::-webkit-scrollbar{width:5px}.console-sidebar::-webkit-scrollbar-thumb{background:#3a4657;border-radius:99px}
.console-sidebar-header{display:flex;align-items:center;gap:9px;padding:7px 10px 15px;border-bottom:1px solid rgba(255,255,255,.1);margin-bottom:13px}
.console-sidebar-project-dot{display:grid;place-items:center;width:30px;height:30px;border-radius:5px;background:#29415f;color:#8fc1ff;font-weight:700}
.console-sidebar-project-name{min-width:0}.console-sidebar-project-name strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}.console-sidebar-project-name span{display:block;color:var(--sidebar-muted);font-size:11px;margin-top:2px}
.console-sidebar-group{margin:0 0 18px}.console-sidebar-label{padding:0 10px 7px;color:#7f8b99;font-size:11px;font-weight:650;letter-spacing:.04em}
.console-nav-item{display:flex;align-items:center;gap:10px;min-height:36px;margin:2px 0;padding:7px 10px;border-radius:4px;color:#c6ced8;font-size:13px;transition:background .12s,color .12s}
.console-nav-item:hover{background:rgba(255,255,255,.08);color:#fff;text-decoration:none}.console-nav-item.is-active{background:var(--sidebar-active);color:#fff;font-weight:650}.console-nav-icon{width:17px;text-align:center;color:#93a0b0;font-size:13px}.console-nav-item.is-active .console-nav-icon{color:#fff}.console-nav-item .nav-count{margin-left:auto;color:#8e9bab;font-size:11px}.console-nav-item.is-active .nav-count{color:#d9ebff}
.console-sidebar-note{margin:24px 6px 0;padding:11px 10px;border:1px solid rgba(255,255,255,.1);border-radius:5px;color:#98a5b4;font-size:11px;line-height:1.55}
.console-content{min-width:0;background:var(--bg)}
.console-breadcrumbs{display:flex;align-items:center;gap:8px;min-height:44px;padding:14px 32px 0;color:var(--muted);font-size:12px}
.console-breadcrumbs a{color:#667085}.console-breadcrumbs a:hover{color:#1677ff;text-decoration:none}.console-breadcrumbs strong{color:#4e5969;font-weight:600}.console-breadcrumbs-separator{color:#c9cdd4}
.console-content>main{max-width:none;margin:0;padding:0 32px 46px}
.console-content>footer{max-width:none;margin:0;padding:18px 32px 28px;border-top:1px solid var(--line);font-size:12px}
.hero{align-items:flex-start;margin:0;padding:17px 0 20px}.hero h1{font-size:26px;letter-spacing:-.01em}.hero p{font-size:13px;color:#86909c}.eyebrow{color:#1677ff;font-size:11px;letter-spacing:.08em}
.hero .actions{margin-top:4px}.section-title{font-size:17px;margin:24px 0 11px}
.card,.panel{border-radius:6px;box-shadow:none;border-color:var(--line);padding:18px}.card h2,.panel h2,.panel h3{line-height:1.35}.panel h2{font-size:17px}.panel h3{font-size:14px}
.stats{gap:10px;margin:0 0 18px}.stat{min-height:78px;border-radius:6px;padding:14px 16px}.stat strong{font-size:23px}.stat span{font-size:12px;color:#86909c}
.button,button{border-radius:4px;min-height:34px;padding:6px 12px;font-size:13px}.primary{box-shadow:0 1px 2px rgba(22,119,255,.18)}
input[type=text],input[type=number],input[type=search],select{border-radius:4px;min-height:36px;padding:7px 10px}
.notice{border-radius:5px;margin:10px 0 14px}.help{border-radius:5px;background:#f0f7ff;border:1px solid #cfe4ff;color:#4e5969;padding:11px 14px;font-size:13px}
.project-card{min-height:0}.project-card .actions{margin-top:15px}.workflow-grid{gap:10px}.workflow-card{border-radius:5px;padding:13px;background:#fff}.workflow-card h3{font-size:14px}.recent-list li{padding:8px 0}
.console-project-table{overflow:hidden;padding:0}.console-project-table-head,.console-project-row{display:grid;grid-template-columns:minmax(270px,1.7fr) 110px 110px 150px minmax(205px,1fr);gap:18px;align-items:center;padding:12px 18px}.console-project-table-head{background:#f7f8fa;border-bottom:1px solid var(--line);color:#86909c;font-size:12px}.console-project-row{min-height:78px;border-bottom:1px solid #f0f1f3}.console-project-row:last-child{border-bottom:0}.console-project-row:hover{background:#fafcff}.console-project-main{min-width:0}.console-project-main h2{font-size:15px;margin:0 0 4px}.console-project-main h2 a{color:#1f2329}.console-project-main h2 a:hover{color:#1677ff;text-decoration:none}.console-project-main .path{font-size:11px;color:#86909c;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.console-project-metric strong{display:block;font-size:16px;font-weight:650}.console-project-metric span{display:block;color:#86909c;font-size:11px}.console-project-status{color:#4e5969;font-size:12px;white-space:nowrap}.status-dot{display:inline-block;width:7px;height:7px;margin-right:6px;border-radius:50%;background:#27ae60;vertical-align:1px}.status-dot-warn{background:#ff9900}.console-project-actions{display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end}.console-project-actions .button{min-height:30px;padding:4px 9px;font-size:12px}
.console-section-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:25px 0 10px}.console-section-head h2{margin:0;font-size:17px}.console-section-head p{margin:0;color:#86909c;font-size:12px}
.doc-toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:8px 0 14px}.doc-toolbar .breadcrumbs{color:#86909c;font-size:12px}.doc-toolbar .actions{margin:0}.paper-viewer,.compare-pane{border-radius:6px;box-shadow:none}.literature-app{border-radius:6px;box-shadow:none}
.console-overlay{display:none}
@media(max-width:1100px){.console-brand{min-width:190px}.console-project-switcher{min-width:175px}.console-topbar-link{display:none}.console-project-table-head,.console-project-row{grid-template-columns:minmax(220px,1.6fr) 90px 90px 140px minmax(180px,1fr);gap:12px}.console-content>main{padding-left:24px;padding-right:24px}.console-breadcrumbs{padding-left:24px;padding-right:24px}}
@media(max-width:820px){.console-topbar-inner{padding:0 15px;gap:10px}.console-menu-button{display:block}.console-brand{min-width:0;flex:1}.console-brand-product{display:none}.console-project-switcher{min-width:0}.console-project-switcher summary{width:36px;justify-content:center;padding:5px}.console-project-switcher summary:after{display:none}.console-project-switcher summary .console-switcher-copy{display:none}.console-project-switcher-name,.console-project-switcher-label{display:none}.console-project-menu{left:auto;right:0}.console-global-search{display:none}.console-runtime{display:none}.console-body{display:block}.console-sidebar{position:fixed;left:0;top:64px;z-index:45;width:236px;transform:translateX(-102%);transition:transform .18s ease;box-shadow:8px 0 20px rgba(0,0,0,.18)}.console-sidebar.is-open{transform:translateX(0)}.console-overlay{position:fixed;inset:64px 0 0;z-index:40;background:rgba(15,23,42,.45)}.console-overlay.is-visible{display:block}.console-content>main{padding-left:15px;padding-right:15px}.console-breadcrumbs{padding-left:15px;padding-right:15px}.console-project-table{overflow-x:auto}.console-project-table-head,.console-project-row{min-width:760px}.hero{display:block}.hero .actions{margin-top:14px}.research-layout,.reading-layout{grid-template-columns:1fr}.sidebar{position:static;max-height:none}.toc-panel{display:none}.split,.storage-grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.doc-toolbar{display:block}.doc-toolbar .actions{margin-top:10px}}
@media(max-width:520px){.console-brand-name{font-size:14px}.console-brand-mark{width:28px;height:28px}.console-content>main{padding-bottom:32px}.hero h1{font-size:22px}.stats{gap:8px}.stat{min-height:68px;padding:11px 12px}.stat strong{font-size:20px}.panel,.card{padding:14px}.library-summary{grid-template-columns:1fr 1fr}}
"""

CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("研究总览", ("index", "overview", "readme", "project", "研究总览", "项目总览", "概览")),
    ("文献与阅读", ("paper", "papers", "literature", "survey", "文献", "论文", "阅读", "综述")),
    ("方法与实现", ("method", "methods", "algorithm", "implementation", "architecture", "方法", "算法", "实现", "模型")),
    ("数据与样本", ("dataset", "data", "sample", "数据集", "数据", "样本", "标注")),
    ("实验记录", ("experiment", "experiments", "ablation", "run", "实验", "消融", "训练记录", "运行记录")),
    ("结果与分析", ("result", "results", "metric", "evaluation", "analysis", "结果", "指标", "评估", "分析")),
    ("结论与问题", ("claim", "conclusion", "question", "finding", "结论", "问题", "发现", "假设")),
    ("计划与待办", ("plan", "roadmap", "todo", "backlog", "goal", "计划", "路线图", "待办", "目标")),
    ("论文与成果", ("thesis", "patent", "publication", "defense", "manuscript", "学位论文", "专利", "投稿", "答辩", "成果")),
    ("决策记录", ("decision", "decisions", "meeting", "决策", "会议", "讨论记录")),
    ("资料索引", ("source", "sources", "reference", "bibliography", "资料", "来源", "参考文献", "索引")),
    ("研究笔记", ("note", "notes", "journal", "diary", "笔记", "日志", "日记")),
)
CATEGORY_ORDER = [name for name, _ in CATEGORY_RULES] + ["其他页面"]


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def safe_path(root: Path, relative: str) -> Path:
    target = (root / relative.replace("/", str(Path("/")))).resolve(strict=False)
    if not within(target, root):
        raise LLMWikiError("请求的文件不在 Wiki 目录中。")
    return target


def purl(project_id: str) -> str:
    return f"/project/{quote(project_id, safe='')}"


def pageurl(project_id: str, page: str) -> str:
    return f"{purl(project_id)}/page/{quote(page, safe='/')}"



SCRIPT = """
function copyText(id, button) {
  const el = document.getElementById(id);
  if (!el) return;
  const clone = el.cloneNode(true);
  clone.querySelectorAll('button').forEach(item => item.remove());
  navigator.clipboard.writeText(clone.innerText.trim()).then(() => {
    const old = button.innerText;
    button.innerText = '已复制';
    setTimeout(() => button.innerText = old, 1200);
  });
}
function filterPages(input) {
  const q = input.value.trim().toLowerCase();
  document.querySelectorAll('[data-page-title]').forEach(el => {
    el.style.display = el.dataset.pageTitle.includes(q) ? '' : 'none';
  });
}
const literatureState = {status: 'all', kind: 'all', query: ''};
function setLiteratureFilter(group, value, button) {
  literatureState[group] = value;
  document.querySelectorAll(`[data-filter-group="${group}"]`).forEach(el => el.classList.remove('is-active'));
  if (button) button.classList.add('is-active');
  applyLiteratureFilters();
}
function filterLiterature(input) {
  literatureState.query = input.value.trim().toLowerCase();
  applyLiteratureFilters();
}
function applyLiteratureFilters() {
  const cards = Array.from(document.querySelectorAll('[data-literature-card]'));
  let visible = 0;
  cards.forEach(card => {
    const statusOK = literatureState.status === 'all' || card.dataset.literatureStatus === literatureState.status;
    const kindOK = literatureState.kind === 'all' || card.dataset.literatureKind === literatureState.kind;
    const queryOK = !literatureState.query || card.dataset.pageTitle.includes(literatureState.query);
    const show = statusOK && kindOK && queryOK;
    card.hidden = !show;
    if (show) visible += 1;
  });
  const count = document.getElementById('literature-result-count');
  if (count) count.textContent = String(visible);
  const empty = document.getElementById('literature-filter-empty');
  if (empty) empty.classList.toggle('is-visible', cards.length > 0 && visible === 0);
}
function setLiteratureView(view, button) {
  const list = document.getElementById('literature-list');
  if (!list) return;
  list.classList.toggle('is-list', view === 'list');
  document.querySelectorAll('[data-literature-view]').forEach(el => el.classList.remove('is-active'));
  if (button) button.classList.add('is-active');
  try { localStorage.setItem('llmwiki-literature-view', view); } catch (_) {}
}
function toggleConsoleSidebar(force) {
  const sidebar = document.getElementById('console-sidebar');
  const overlay = document.getElementById('console-overlay');
  if (!sidebar || !overlay) return;
  const open = typeof force === 'boolean' ? force : !sidebar.classList.contains('is-open');
  sidebar.classList.toggle('is-open', open);
  overlay.classList.toggle('is-visible', open);
  document.body.classList.toggle('console-locked', open);
}
function initConsoleShell() {
  const overlay = document.getElementById('console-overlay');
  if (overlay) overlay.addEventListener('click', () => toggleConsoleSidebar(false));
  document.querySelectorAll('.console-sidebar a').forEach(link => {
    link.addEventListener('click', () => toggleConsoleSidebar(false));
  });
}
function initLiterature() {
  const list = document.getElementById('literature-list');
  if (!list) return;
  let view = 'list';
  try { view = localStorage.getItem('llmwiki-literature-view') || 'list'; } catch (_) {}
  const button = document.querySelector(`[data-literature-view="${view}"]`);
  setLiteratureView(view, button);
  applyLiteratureFilters();
}
document.addEventListener('DOMContentLoaded', () => {
  initConsoleShell();
  initLiterature();
});
"""

def _layout_context(
    home: str | None, project_id: str | None
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None]:
    try:
        listed = list_projects(home)
        projects = list(listed.get("projects") or [])
        current_id = project_id or listed.get("current_project_id")
    except (LLMWikiError, OSError, ValueError):
        return [], project_id, None
    project: dict[str, Any] | None = None
    if current_id:
        project = next(
            (item for item in projects if str(item.get("id")) == str(current_id)), None
        )
        if project is None:
            try:
                project = get_project(str(current_id), home=home)["project"]
            except (LLMWikiError, OSError, ValueError):
                project = None
    return projects, str(current_id) if current_id else None, project


def _console_nav_item(
    href: str, label: str, icon: str, key: str, active: str
) -> str:
    active_class = " is-active" if key == active else ""
    current = ' aria-current="page"' if key == active else ""
    return (
        f'<a class="console-nav-item{active_class}" href="{href}"{current}>'
        f'<span class="console-nav-icon" aria-hidden="true">{icon}</span>'
        f'<span>{esc(label)}</span></a>'
    )


def _console_breadcrumbs(
    title: str, project: dict[str, Any] | None, active: str
) -> str:
    parts = ['<a href="/">' + "\u63a7\u5236\u53f0" + '</a>']
    if project and active in {"overview", "literature", "pages"}:
        project_id = str(project["id"])
        parts.append(f'<a href="{purl(project_id)}">{esc(project["name"])}</a>')
    breadcrumb_title = {
        "overview": "\u7814\u7a76\u603b\u89c8",
        "pages": "\u77e5\u8bc6\u9875\u9762",
    }.get(active, title)
    if active == "literature" and title.endswith("\u00b7 \u6587\u732e\u4e2d\u5fc3"):
        breadcrumb_title = "\u6587\u732e\u4e2d\u5fc3"
    parts.append(f"<strong>{esc(breadcrumb_title)}</strong>")
    return '<div class="console-breadcrumbs">' + '<span class="console-breadcrumbs-separator">/</span>'.join(parts) + "</div>"


def layout(
    title: str,
    body: str,
    query: str = "",
    project_id: str | None = None,
    active: str = "",
    home: str | None = None,
) -> str:
    projects, current_id, project = _layout_context(home, project_id)
    if project_id and project is None:
        try:
            project = get_project(project_id, home=home)["project"]
        except (LLMWikiError, OSError, ValueError):
            project = None
    current_name = str(project["name"]) if project else "未选择研究项目"
    current_path = str(project["source_root"]) if project else ""
    project_menu = "".join(
        f'<a class="{"is-current" if str(item.get("id")) == str(current_id) else ""}" href="{purl(str(item["id"]))}">'
        f'{esc(item.get("name", item.get("id", "")))}<span class="path">{esc(item.get("source_root", ""))}</span></a>'
        for item in projects
    )
    if not project_menu:
        project_menu = '<span class="muted">还没有已注册项目</span>'
    project_picker = (
        f'<details class="console-project-switcher"><summary aria-label="\u5207\u6362\u5f53\u524d\u7814\u7a76\u9879\u76ee">'
        f'<span class="console-project-switcher-icon" aria-hidden="true">&#9632;</span>'
        f'<span class="console-switcher-copy">'
        f'<span class="console-project-switcher-label">\u5f53\u524d\u7814\u7a76\u9879\u76ee</span>'
        f'<span class="console-project-switcher-name">{esc(current_name)}</span></span></summary>'
        f'<div class="console-project-menu">{project_menu}<a href="/settings">\u7ba1\u7406\u9879\u76ee\u4e0e\u5b58\u50a8</a></div></details>'
    )
    sidebar_project = ""
    if project:
        sidebar_project = (
            f'<div class="console-sidebar-header"><span class="console-sidebar-project-dot">研</span>'
            f'<div class="console-sidebar-project-name"><strong>{esc(project["name"])}</strong>'
            f'<span title="{esc(current_path)}">当前项目</span></div></div>'
        )
    else:
        sidebar_project = (
            '<div class="console-sidebar-header"><span class="console-sidebar-project-dot">W</span>'
            '<div class="console-sidebar-project-name"><strong>LLM Wiki Lite</strong><span>本地科研知识库</span></div></div>'
        )
    project_links = ""
    if project:
        pid = str(project["id"])
        project_links = (
            '<div class="console-sidebar-group"><div class="console-sidebar-label">当前项目</div>'
            + _console_nav_item(purl(pid), "研究总览", "&#9635;", "overview", active)
            + _console_nav_item(f"{purl(pid)}/literature", "文献中心", "&#9634;", "literature", active)
            + _console_nav_item(f"{purl(pid)}#research-content", "知识页面", "&#9776;", "pages", active)
            + _console_nav_item(f"/settings#project-{quote(pid, safe='')}", "项目存储", "&#8646;", "project-storage", active)
            + "</div>"
        )
    sidebar = (
        f'<aside class="console-sidebar" id="console-sidebar">{sidebar_project}'
        '<div class="console-sidebar-group"><div class="console-sidebar-label">工作台</div>'
        + _console_nav_item("/", "总览", "&#8962;", "home", active)
        + _console_nav_item("/search", "科研检索", "&#8981;", "search", active)
        + _console_nav_item("/settings", "存储设置", "&#9881;", "settings", active)
        + "</div>"
        + project_links
        + '<div class="console-sidebar-note">语义研究由 Codex 完成；网页只负责查看本地 Markdown、文献原文和项目存储状态。</div>'
        + "</aside>"
    )
    runtime = '<span class="console-runtime-dot"></span><span>本机运行</span>'
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(title)} &middot; LLM Wiki Lite</title><link rel="stylesheet" href="/static/style.css"></head>'
        '<body><div class="console-shell">'
        '<header class="console-topbar"><div class="console-topbar-inner">'
        '<button class="console-menu-button" type="button" aria-label="打开导航" onclick="toggleConsoleSidebar()">&#9776;</button>'
        '<a class="console-brand" href="/"><span class="console-brand-mark">LW</span>'
        '<span class="console-brand-copy"><span class="console-brand-name">LLM Wiki Lite</span>'
        '<span class="console-brand-product">科研控制台</span></span></a>'
        + project_picker
        + f'<form class="console-global-search" action="/search"><input type="search" name="q" value="{esc(query)}" placeholder="搜索知识页、论文、方法、实验"><button class="button">搜索</button></form>'
        + f'<div class="console-runtime">{runtime}</div><a class="console-topbar-link" href="/settings">设置</a>'
        + '</div></header><div class="console-body">'
        + sidebar
        + '<div class="console-overlay" id="console-overlay"></div><div class="console-content">'
        + _console_breadcrumbs(title, project, active)
        + f'<main>{body}</main><footer>本地 Markdown 科研知识库 - 网页仅监听 127.0.0.1 - 源项目不会被修改</footer>'
        + '</div></div></div><script>'
        + SCRIPT
        + '</script></body></html>'
    )


def notice(params: dict[str, list[str]]) -> str:
    if params.get("message"):
        return f'<div class="notice">{esc(params["message"][0])}</div>'
    if params.get("error"):
        return f'<div class="notice error">{esc(params["error"][0])}</div>'
    return ""


def project_status(project: dict[str, Any]) -> dict[str, Any]:
    try:
        return status(str(project["source_root"]), state_root=str(project["state_root"]))
    except (LLMWikiError, OSError, ValueError) as exc:
        return {"wiki_page_count": 0, "snapshot_file_count": 0, "snapshot_at": None, "dirty_paths": [], "error": str(exc)}


def format_time(value: str | None) -> str:
    if not value:
        return "尚未扫描"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def classify_page(path: str, title: str) -> str:
    normalized = f"{path} {title}".replace("\\", "/").lower()
    parts = re.split(r"[/_.\-\s]+", normalized)
    for category, keywords in CATEGORY_RULES:
        for keyword in keywords:
            key = keyword.lower()
            if key in parts or key in normalized:
                return category
    return "其他页面"


def page_records(project: dict[str, Any], pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wiki_root = Path(str(project["wiki_root"]))
    records: list[dict[str, Any]] = []
    for page in pages:
        item = dict(page)
        item["category"] = classify_page(str(page["path"]), str(page["title"]))
        target = safe_path(wiki_root, str(page["path"]))
        try:
            item["mtime"] = target.stat().st_mtime
            item["updated"] = datetime.fromtimestamp(item["mtime"]).strftime("%Y-%m-%d %H:%M")
        except OSError:
            item["mtime"] = 0.0
            item["updated"] = "未知"
        records.append(item)
    return records


def grouped(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result = {name: [] for name in CATEGORY_ORDER}
    for page in records:
        result[str(page["category"])].append(page)
    for pages in result.values():
        pages.sort(key=lambda item: (str(item["title"]).lower(), str(item["path"])))
    return result


def page_link(project_id: str, page: dict[str, Any], *, show_path: bool = False) -> str:
    path = str(page["path"])
    details = f'<div class="meta path">{esc(path)}</div>' if show_path else ""
    return f'<li data-page-title="{esc((str(page["title"]) + " " + path).lower())}"><a href="{pageurl(project_id, path)}">{esc(page["title"])}</a>{details}</li>'

def home_page(home: str, params: dict[str, list[str]]) -> str:
    listed = list_projects(home)
    projects = listed["projects"]
    rows: list[str] = []
    total_pages = total_files = total_dirty = 0
    for project in projects:
        state = project_status(project)
        pages = int(state.get("wiki_page_count", 0))
        files = int(state.get("snapshot_file_count", 0))
        dirty = state.get("dirty_paths") or []
        total_pages += pages
        total_files += files
        total_dirty += len(dirty)
        status_label = (
            f'<span class="status-dot status-dot-warn"></span>{len(dirty)} 项待核对'
            if dirty
            else '<span class="status-dot"></span>状态稳定'
        )
        rows.append(
            f'''<div class="console-project-row"><div class="console-project-main"><h2><a href="{purl(project["id"])}">{esc(project["name"])}</a></h2><div class="path" title="{esc(project["source_root"])}">{esc(project["source_root"])}</div><div class="meta">Wiki：{esc(project["wiki_root"])} - 最近扫描：{esc(format_time(state.get("snapshot_at")))}</div></div><div class="console-project-metric"><strong>{pages}</strong><span>知识页</span></div><div class="console-project-metric"><strong>{files}</strong><span>源文件</span></div><div class="console-project-status">{status_label}</div><div class="console-project-actions"><a class="button primary" href="{purl(project["id"])}">进入研究台</a><a class="button" href="{purl(project["id"])}/literature">文献中心</a><a class="button" href="/search?project={quote(project["id"], safe='')}">检索</a></div></div>'''
        )
    stats = f'''<section class="stats"><div class="stat"><strong>{len(projects)}</strong><span>已注册研究项目</span></div><div class="stat"><strong>{total_pages}</strong><span>可阅读知识页</span></div><div class="stat"><strong>{total_files}</strong><span>已记录源文件</span></div><div class="stat"><strong>{total_dirty}</strong><span>待核对变更提示</span></div></section>'''
    empty = '''<div class="panel empty"><h2>还没有研究项目</h2><p>先注册 Codex 当前打开的项目。注册只建立项目身份和存储位置，不会假装已经完成分析。</p><a class="button primary" href="/settings">注册第一个研究项目</a></div>'''
    project_table = (
        f'<section class="panel console-project-table"><div class="console-project-table-head"><span>研究项目</span><span>知识页</span><span>源文件</span><span>当前状态</span><span>操作</span></div>{"".join(rows)}</section>'
        if rows
        else empty
    )
    body = notice(params) + '''<section class="hero"><div><div class="eyebrow">LLM WIKI LITE - 控制台总览</div><h1>中文科研知识工作台</h1><p>用一个清晰的本地控制台管理项目、文献和 Markdown 知识。</p></div><div class="actions"><a class="button" href="/search">科研检索</a><a class="button primary" href="/settings">项目与存储设置</a></div></section>''' + stats + '<div class="console-section-head"><div><h2>我的研究项目</h2><p>从这里进入具体研究台。</p></div><a class="button" href="/settings">注册项目</a></div>''' + project_table
    return layout("中文科研知识工作台", body, active="home", home=home)


def next_steps(records: list[dict[str, Any]], state: dict[str, Any]) -> list[str]:
    categories = {str(item["category"]) for item in records}
    steps: list[str] = []
    if state.get("dirty_paths"):
        steps.append("先让 Codex 检查待核对变化，并增量更新受影响的知识页。")
    if not records:
        steps.append("让 Codex 先理解项目，只创建少量真正能回答问题的核心知识页。")
    else:
        if "研究总览" not in categories:
            steps.append("补一页研究总览：研究问题、输入输出、主要方法、当前进展与关键风险。")
        if "文献与阅读" not in categories:
            steps.append("建立文献脉络：代表论文、方法演进、与你课题的关系和待精读问题。")
        if "实验记录" not in categories and "结果与分析" not in categories:
            steps.append("沉淀实验设计、配置、指标与失败现象，避免只保留最终结论。")
        if "计划与待办" not in categories:
            steps.append("把下一阶段目标拆成可验证的研究任务，并记录完成证据。")
    return steps[:4]


def project_page(home: str, project_id: str, params: dict[str, list[str]]) -> str:
    project = get_project(project_id, home=home)["project"]
    state = project_status(project)
    pages = wiki_list(str(project["source_root"]), state_root=str(project["state_root"]))["pages"]
    records = page_records(project, pages)
    groups = grouped(records)
    workflow_cards: list[str] = []
    for category in CATEGORY_ORDER:
        category_pages = groups[category]
        if not category_pages:
            continue
        links = "".join(page_link(project_id, item) for item in category_pages[:8])
        more = f'<p class="meta">另有 {len(category_pages)-8} 篇，可在左侧筛选查看。</p>' if len(category_pages) > 8 else ""
        workflow_cards.append(f'<section class="workflow-card"><h3>{esc(category)} <span class="category-count">{len(category_pages)}</span></h3><ul class="page-list">{links}</ul>{more}</section>')
    all_pages = "".join(
        f'<section class="category-block"><h3>{esc(category)}<span class="category-count">{len(groups[category])}</span></h3><ul class="page-list">{"".join(page_link(project_id, item, show_path=True) for item in groups[category])}</ul></section>'
        for category in CATEGORY_ORDER if groups[category]
    ) or '<div class="empty">尚无 Markdown 知识页</div>'
    recent = sorted(records, key=lambda item: float(item["mtime"]), reverse=True)[:8]
    recent_html = "".join(f'<li><a href="{pageurl(project_id, item["path"])}">{esc(item["title"])}</a><div class="meta">{esc(item["category"])} · {esc(item["updated"])}</div></li>' for item in recent) or '<li class="muted">暂无更新记录</li>'
    dirty_paths = state.get("dirty_paths") or []
    dirty_html = "".join(f'<li class="path">{esc(path)}</li>' for path in dirty_paths[:30]) or '<li class="muted">当前没有待核对变化</li>'
    step_html = "".join(f'<li>{esc(step)}</li>' for step in next_steps(records, state))
    prompt_text = f'''请维护研究项目“{project["name"]}”的 LLM Wiki。先检查最近变化，阅读必要源码与已有 Wiki，只更新真正受影响的页面。请用简体中文写给研究生阅读，保留代码、路径、API、算法名和必要英文术语；不要批量生成空模板。最后说明更新了什么、依据是什么、还有哪些不确定问题。'''
    dirty_badge = f'<span class="badge warn">{len(dirty_paths)} 项待核对变化</span>' if dirty_paths else '<span class="badge">知识库状态稳定</span>'
    content = f'''{notice(params)}<section class="hero"><div><div class="eyebrow">项目研究台</div><h1>{esc(project["name"])}</h1><p>围绕真实研究问题组织知识，而不是按固定模板堆页面。</p></div><div class="actions"><a class="button primary" href="{purl(project_id)}/literature">进入文献中心</a><a class="button" href="/search?project={quote(project_id, safe='')}">检索本项目</a><a class="button" href="#research-content">查看研究内容</a></div></section><section class="stats"><div class="stat"><strong>{len(records)}</strong><span>知识页</span></div><div class="stat"><strong>{int(state.get("snapshot_file_count",0))}</strong><span>源文件记录</span></div><div class="stat"><strong>{len([v for v in groups.values() if v])}</strong><span>已有研究主题</span></div><div class="stat"><strong>{len(dirty_paths)}</strong><span>待核对变化</span></div></section><div class="research-layout"><div><section class="panel" id="research-content"><h2>研究内容</h2><p class="muted">网页按科研流程自动归类现有 Markdown；不会改变真实文件目录，也不会强制生成固定类型页面。</p><p class="meta">浏览视图：研究总览 · 文献与阅读 · 方法与实现 · 数据与样本 · 实验记录 · 结果与分析 · 结论与问题 · 计划与待办。</p><div class="workflow-grid">{"".join(workflow_cards) if workflow_cards else '<div class="empty">暂无知识页。可以把下方 Prompt 交给 Codex 开始理解项目。</div>'}</div></section><h2 class="section-title">建议下一步</h2><section class="panel"><ol>{step_html}</ol><h3>交给 Codex 的维护指令</h3><div class="prompt" id="project-prompt">{esc(prompt_text)}<button onclick="copyText('project-prompt',this)">复制</button></div></section><h2 class="section-title">最近更新</h2><section class="panel"><ul class="recent-list">{recent_html}</ul></section></div><aside><section class="panel sidebar"><h2>全部知识页</h2><input class="page-filter" type="search" oninput="filterPages(this)" placeholder="筛选标题或路径">{all_pages}</section></aside></div><section class="panel section-title"><div>{dirty_badge}</div><details class="settings"><summary>待核对变化与项目存储位置</summary><div class="split"><div><h3>待核对变化</h3><ul>{dirty_html}</ul></div><div><h3>存储位置</h3><p class="meta">源项目（只读理解）</p><div class="path">{esc(project["source_root"])}</div><p class="meta">人类可读 Wiki</p><div class="path">{esc(project["wiki_root"])}</div><p class="meta">机器状态</p><div class="path">{esc(project["state_root"])}</div><p class="meta">最近扫描：{esc(format_time(state.get("snapshot_at")))}</p><a class="button" href="/settings#project-{quote(project_id, safe='')}">修改存储位置</a></div></div></details></section>'''
    return layout(str(project["name"]), content, project_id=project_id, active="overview", home=home)

def heading_slug(text: str) -> str:
    return re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", text.strip().lower()).strip("-") or "section"


def extract_headings(markdown: str) -> list[tuple[int, str, str]]:
    headings: list[tuple[int, str, str]] = []
    in_code = False
    for line in markdown.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = re.match(r"^ {0,3}(#{1,4})\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = re.sub(r"[`*_~\[\]]", "", match.group(2)).strip()
        headings.append((len(match.group(1)), title, heading_slug(match.group(2))))
    return headings


def page_view(home: str, project_id: str, relative: str) -> str:
    project = get_project(project_id, home=home)["project"]
    wiki_root = Path(str(project["wiki_root"])).resolve(strict=False)
    target = safe_path(wiki_root, relative)
    if target.suffix.lower() != ".md" or not target.is_file():
        raise FileNotFoundError(relative)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise LLMWikiError("Markdown 文件必须是 UTF-8 编码。") from exc
    pages = wiki_list(str(project["source_root"]), state_root=str(project["state_root"]))["pages"]
    records = page_records(project, pages)
    groups = grouped(records)
    current = next((item for item in records if item["path"] == relative), {"title": target.stem, "path": relative, "category": classify_page(relative, target.stem), "updated": datetime.fromtimestamp(target.stat().st_mtime).strftime("%Y-%m-%d %H:%M")})
    page_groups = "".join(
        f'<section class="category-block"><h3>{esc(category)}<span class="category-count">{len(groups[category])}</span></h3><ul class="page-list">{"".join(page_link(project_id, item) for item in groups[category])}</ul></section>'
        for category in CATEGORY_ORDER if groups[category]
    )
    headings = extract_headings(text)
    toc = "".join(f'<li class="level-{level}"><a href="#{esc(slug)}">{esc(title)}</a></li>' for level, title, slug in headings)
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_words = len(re.findall(r"\b[A-Za-z0-9_]+\b", text))
    reading_units = chinese_chars + latin_words * 2
    minutes = max(1, (reading_units + 499) // 500)
    rendered = render_markdown(text, project_id, relative)
    body = f'''<div class="doc-toolbar"><div class="breadcrumbs"><a href="/">研究项目</a> / <a href="{purl(project_id)}">{esc(project["name"])}</a> / {esc(current["title"])}</div><div class="actions"><button onclick="navigator.clipboard.writeText({esc(repr(relative))})">复制页面路径</button><button onclick="window.print()">打印 / 导出 PDF</button></div></div><div class="reading-layout"><aside class="panel sidebar"><h2>项目知识页</h2><input class="page-filter" type="search" oninput="filterPages(this)" placeholder="筛选页面">{page_groups}<a class="button mobile-only" href="{purl(project_id)}">返回项目研究台</a></aside><article class="document"><div class="meta"><span class="category-tag">{esc(current["category"])}</span> 最近更新 {esc(current["updated"])} · 约 {reading_units} 字词 · 预计阅读 {minutes} 分钟</div>{rendered}</article><aside class="panel sidebar toc toc-panel"><h2>本页目录</h2>{f'<ul>{toc}</ul>' if toc else '<p class="muted">本页暂无标题目录</p>'}<div class="actions"><a class="button" href="{purl(project_id)}">返回项目研究台</a></div></aside></div>'''
    return layout(str(current["title"]), body, project_id=project_id, active="pages", home=home)


def highlighted_excerpt(text: str, query: str, radius: int = 110) -> str:
    lowered = text.lower()
    index = lowered.find(query.lower())
    if index < 0:
        return esc(text[: radius * 2].replace("\n", " "))
    start = max(0, index - radius)
    end = min(len(text), index + len(query) + radius)
    before = esc(text[start:index].replace("\n", " "))
    match = esc(text[index : index + len(query)])
    after = esc(text[index + len(query) : end].replace("\n", " "))
    return ("…" if start else "") + before + f"<mark>{match}</mark>" + after + ("…" if end < len(text) else "")


def search_page(home: str, params: dict[str, list[str]]) -> str:
    query = (params.get("q") or [""])[0].strip()
    selected = (params.get("project") or [""])[0].strip()
    projects = list_projects(home)["projects"]
    options = ['<option value="">全部研究项目</option>'] + [f'<option value="{esc(project["id"])}" {"selected" if selected == project["id"] else ""}>{esc(project["name"])}</option>' for project in projects]
    results: list[dict[str, Any]] = []
    if query:
        for project in projects:
            if selected and project["id"] != selected:
                continue
            pages = wiki_list(str(project["source_root"]), state_root=str(project["state_root"]))["pages"]
            for page in pages:
                target = safe_path(Path(str(project["wiki_root"])), str(page["path"]))
                try:
                    if target.stat().st_size > 2 * 1024 * 1024:
                        continue
                    text = target.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                haystack = f'{page["title"]}\n{page["path"]}\n{text}'
                if query.lower() not in haystack.lower():
                    continue
                results.append({"project": project, "page": page, "category": classify_page(str(page["path"]), str(page["title"])), "excerpt": highlighted_excerpt(haystack, query)})
    result_html = "".join(f'''<article class="search-result"><div><span class="category-tag">{esc(item["category"])}</span><span class="meta">{esc(item["project"]["name"])}</span></div><h2><a href="{pageurl(item["project"]["id"], item["page"]["path"])}">{esc(item["page"]["title"])}</a></h2><div class="meta path">{esc(item["page"]["path"])}</div><p>{item["excerpt"]}</p></article>''' for item in results)
    if query and not results:
        result_html = '<div class="empty"><h2>没有找到相关内容</h2><p>可尝试项目术语、算法名、实验指标、作者名或更短的关键词。</p></div>'
    if not query:
        result_html = '''<div class="panel"><h2>适合检索什么？</h2><div class="workflow-grid"><div class="workflow-card"><h3>文献与方法</h3><p>论文作者、算法名、研究问题、方法模块、理论概念。</p></div><div class="workflow-card"><h3>实验与结果</h3><p>数据集、实验配置、消融、指标、异常现象、失败原因。</p></div><div class="workflow-card"><h3>结论与计划</h3><p>已有结论、证据、不确定问题、下一步任务和决策记录。</p></div></div></div>'''
    body = f'''<section class="hero"><div><div class="eyebrow">科研检索</div><h1>检索论文、方法、实验和结论</h1><p>检索本机已注册项目中的 Markdown 正文、标题和路径。</p></div></section><section class="panel"><form action="/search"><div class="split"><div><label for="q">关键词</label><input id="q" type="search" name="q" value="{esc(query)}" placeholder="例如：低纹理配准、ICP、消融实验、准确率"></div><div><label for="project">研究项目</label><select id="project" name="project">{"".join(options)}</select></div></div><div class="actions"><button class="primary">开始检索</button></div></form></section><h2 class="section-title">{f'检索结果（{len(results)}）' if query else '检索说明'}</h2>{result_html}'''
    return layout("科研检索", body, query, active="search", home=home)


def settings_page(home: str, params: dict[str, list[str]]) -> str:
    settings = load_settings(home)
    projects = list_projects(home)["projects"]
    project_forms: list[str] = []
    for project in projects:
        project_forms.append(f'''<section class="panel" id="project-{quote(project["id"], safe='')}"><h2>{esc(project["name"])}</h2><p class="meta">源项目（保持不变）</p><div class="path">{esc(project["source_root"])}</div><form method="post" action="{purl(project["id"])}/storage"><label>人类可读 Wiki 目录</label><input type="text" name="wiki_root" value="{esc(project["wiki_root"])}"><p class="meta">存放给你和 Codex 阅读的 Markdown，可选择 Obsidian 库中的目录。</p><details class="settings"><summary>高级设置：机器状态目录</summary><label>机器状态目录</label><input type="text" name="state_root" value="{esc(project["state_root"])}"><p class="meta">保存快照、哈希和变更提示，一般无需手动查看。</p></details><label><input type="checkbox" name="copy_existing" value="1" checked>修改位置时复制现有内容</label><div class="actions"><button class="primary">保存项目位置</button></div></form><form method="post" action="{purl(project["id"])}/unregister" onsubmit="return confirm('只取消注册，不删除任何文件。确定继续吗？')"><div class="actions"><button class="danger">取消注册</button></div></form></section>''')
    default_root = settings.get("default_wiki_root") or ""
    body = f'''{notice(params)}<section class="hero"><div><div class="eyebrow">项目与存储</div><h1>存储设置</h1><p>人类知识与机器状态分开保存；修改位置不会删除旧目录。</p></div></section><div class="help"><strong>推荐：</strong>Windows 用户可把人类可读 Wiki 根目录设为 <code>E:\\wiki_obsidian</code>，新项目会在其中建立独立目录；未设置时默认使用当前项目下的 <code>wiki</code> 目录。</div><h2 class="section-title">默认位置</h2><section class="panel"><form method="post" action="/settings/default-wiki-root"><label>人类可读 Wiki 默认根目录</label><input type="text" name="default_wiki_root" value="{esc(default_root)}" placeholder="例如 E:\\wiki_obsidian"><p class="meta">只影响以后注册且未单独指定 Wiki 位置的项目。</p><label>本地网站端口</label><input type="number" name="web_port" min="1024" max="65535" value="{int(settings.get("web_port",8765))}"><details class="settings"><summary>高级设置：注册表与机器状态</summary><p class="meta">LLM Wiki 本机注册表目录</p><div class="path">{esc(home)}</div><p class="meta">每个项目的机器状态位置可在下方单独修改。</p></details><div class="actions"><button class="primary">保存默认设置</button></div></form></section><h2 class="section-title">注册研究项目</h2><section class="panel"><form method="post" action="/project/register"><label>项目目录</label><input type="text" name="source_root" required placeholder="Codex 当前打开的项目绝对路径"><label>项目名称（可选）</label><input type="text" name="name" placeholder="默认使用目录名"><label>人类可读 Wiki 目录（可选）</label><input type="text" name="wiki_root" placeholder="留空则使用默认规则"><details class="settings"><summary>高级设置：自定义机器状态目录</summary><label>机器状态目录（可选）</label><input type="text" name="state_root" placeholder="留空则由插件管理"></details><p class="meta">注册只建立项目身份和空存储位置，不等于已经扫描或理解项目。</p><div class="actions"><button class="primary">注册项目</button></div></form></section><h2 class="section-title">已注册项目</h2><div class="grid">{"".join(project_forms) if project_forms else '<div class="panel empty">暂无已注册项目</div>'}</div>'''
    return layout("存储设置", body, active="settings", home=home)
