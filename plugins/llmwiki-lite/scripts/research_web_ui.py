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
from research_records import MAX_LIST_RECORDS, list_records, read_record

IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

STYLE = """/* LLM Wiki Lite 中文科研工作台 — 单一样式系统 */
:root{--accent:#1677ff;--accent-dark:#0958d9;--bg:#f5f7fa;--panel:#fff;--text:#1f2329;--muted:#86909c;--line:#e5e6eb;--soft:#f2f3f5;--danger:#a43434;--success:#eaf6ee;--sidebar:#fff;--sidebar-muted:#7c8796;--sidebar-active:#eaf3ff;--sidebar-line:#e6ebf2;--shadow:0 1px 2px rgba(31,35,41,.04)}
*{box-sizing:border-box}html{scroll-behavior:smooth}html,body{min-height:100%}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif}body.console-locked{overflow:hidden}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}code{font-family:Consolas,"SFMono-Regular",monospace}

/* ---------- 通用组件 ---------- */
.button,button{display:inline-block;border:1px solid #b8c4d1;border-radius:4px;min-height:34px;padding:6px 12px;background:#fff;color:var(--text);font:inherit;font-size:13px;line-height:1.5;cursor:pointer;text-decoration:none}
.button:hover,button:hover{background:#f6f8fa;text-decoration:none}
.button.primary,.primary{color:#fff!important;background:var(--accent)!important;border-color:var(--accent)!important;box-shadow:0 1px 2px rgba(22,119,255,.18)}
.primary:hover{background:var(--accent-dark)!important}
.danger{color:var(--danger)!important;border-color:#d8abab!important}
label{display:block;font-weight:650;margin:12px 0 5px}
input[type=text],input[type=number],input[type=search],select{width:100%;padding:7px 10px;border:1px solid #c8d0d9;border-radius:4px;min-height:36px;background:#fff;font:inherit;font-size:13px}
input[type=text]:focus,input[type=number]:focus,input[type=search]:focus,select:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 2px rgba(22,119,255,.12)}
input[type=checkbox]{width:auto;margin-right:7px}
.notice{padding:10px 13px;background:var(--success);border:1px solid #bddcc8;border-radius:5px;margin:10px 0 14px}
.notice.error{background:#fff0f0;border-color:#e2b8b8}
.help{padding:11px 14px;background:#f0f7ff;border:1px solid #cfe4ff;border-left:3px solid #9db6cf;color:#4e5969;font-size:13px;border-radius:5px}
.badge,.category-tag{display:inline-block;padding:2px 8px;background:var(--soft);border:1px solid #dbe8f4;border-radius:99px;font-size:12px;margin:0 5px 4px 0}
.badge.warn{background:#fff8e8;border-color:#eadbad}
.meta,.muted{color:var(--muted);font-size:13px}
.path{font-family:Consolas,"SFMono-Regular",monospace;overflow-wrap:anywhere}
.panel,.card{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:18px;box-shadow:var(--shadow)}
.panel h2,.panel h3,.card h2{line-height:1.35;margin-top:0}
.panel h2{font-size:17px}.panel h3{font-size:14px}
.empty{padding:30px;text-align:center;color:var(--muted)}
.actions{display:flex;flex-wrap:wrap;gap:9px;margin-top:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(285px,1fr));gap:16px}
.split{display:grid;grid-template-columns:1fr 1fr;gap:16px}
details.settings{margin-top:15px;border-top:1px solid var(--line);padding-top:12px}
details.settings summary{cursor:pointer;font-weight:650}
.mobile-only{display:none}
.hero{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;padding:17px 0 20px}
.hero h1{font-size:26px;letter-spacing:-.01em;margin:0 0 4px;line-height:1.25}
.hero p{margin:0;color:#86909c;font-size:13px}
.hero .actions{margin-top:4px}
.eyebrow{color:var(--accent);font-size:11px;font-weight:700;letter-spacing:.08em}
.section-title{font-size:17px;margin:24px 0 11px}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:0 0 18px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:6px;min-height:78px;padding:14px 16px}
.stat strong{display:block;font-size:23px;line-height:1.2}
.stat span{color:#86909c;font-size:12px}

/* ---------- 控制台外壳（顶栏 / 侧栏 / 内容） ---------- */
.console-shell{min-height:100vh;background:var(--bg)}
.console-topbar{position:fixed;left:248px;right:0;top:0;height:60px;z-index:50;background:#fff;border-bottom:1px solid var(--line);box-shadow:0 1px 4px rgba(31,35,41,.05)}
.console-topbar-inner{height:60px;display:flex;align-items:center;gap:16px;padding:0 28px}
.console-menu-button{display:none;border:0!important;background:transparent!important;padding:5px!important;font-size:22px;line-height:1;color:#1f2329}
.console-topbar-title{display:flex;align-items:baseline;gap:9px;min-width:190px;color:#1f2329;white-space:nowrap}
.console-topbar-title span{font-size:12px;color:var(--muted)}
.console-topbar-title strong{font-size:15px;font-weight:650;overflow:hidden;text-overflow:ellipsis}
.console-global-search{display:flex;align-items:center;gap:8px;max-width:480px;flex:1;margin-left:auto}
.console-global-search input{flex:1;min-width:0;height:36px;background:#f7f8fa;border-color:#e5e6eb}
.console-global-search input:focus{background:#fff;border-color:var(--accent);box-shadow:0 0 0 2px rgba(22,119,255,.12);outline:0}
.console-global-search button{height:36px;padding:0 14px;white-space:nowrap}
.console-runtime{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px;white-space:nowrap}
.console-runtime-dot{width:7px;height:7px;border-radius:50%;background:#27ae60;box-shadow:0 0 0 3px #e9f8ee}
.console-body{min-height:100vh}
.console-sidebar{position:fixed;left:0;top:0;bottom:0;width:248px;z-index:45;display:flex;flex-direction:column;overflow-y:auto;background:var(--sidebar);color:#1f2329;border-right:1px solid var(--sidebar-line);padding:0 12px 18px}
.console-sidebar::-webkit-scrollbar{width:5px}.console-sidebar::-webkit-scrollbar-thumb{background:#d9e0e8;border-radius:99px}
.console-sidebar-brand{display:flex;align-items:center;gap:10px;height:60px;padding:0 10px;margin-bottom:14px;border-bottom:1px solid var(--sidebar-line);color:#1f2329;text-decoration:none}
.console-sidebar-brand:hover{text-decoration:none}
.console-sidebar-brand .console-brand-mark{width:30px;height:30px;background:#1677ff}
.console-brand-mark{display:grid;place-items:center;width:30px;height:30px;border-radius:6px;background:#1677ff;color:#fff;font-size:12px;font-weight:800;letter-spacing:-.04em;flex:0 0 auto}
.console-sidebar-brand strong{display:block;font-size:14px;line-height:1.25}
.console-sidebar-brand small{display:block;margin-top:2px;color:var(--muted);font-size:11px}
.console-sidebar-project-switcher{position:relative;margin:0 2px 17px}
.console-sidebar-project-switcher summary{display:flex;align-items:center;gap:8px;min-height:44px;padding:7px 9px;border:1px solid var(--sidebar-line);border-radius:6px;background:#f8fafc;cursor:pointer;list-style:none}
.console-sidebar-project-switcher summary::-webkit-details-marker{display:none}
.console-sidebar-project-switcher summary:hover{border-color:#b7c7e5;background:#f4f8ff}
.console-project-switcher-icon{display:grid;place-items:center;width:22px;height:22px;border-radius:5px;background:#e8f3ff;color:var(--accent);font-size:8px;flex:0 0 auto}
.console-project-switcher-icon::before{content:"";display:block;width:6px;height:6px;border-radius:1px;background:currentColor}
.console-switcher-copy{min-width:0;flex:1}
.console-project-switcher-label{display:block;color:var(--muted);font-size:10px;line-height:1.1}
.console-project-switcher-name{display:block;max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600;font-size:13px;line-height:1.25}
.console-project-switcher-chevron{color:#8b95a1;font-size:14px;line-height:1;transform:rotate(0deg)}
.console-sidebar-project-switcher[open] .console-project-switcher-chevron{transform:rotate(180deg)}
.console-project-menu{position:static;width:auto;margin-top:6px;padding:4px;background:#fff;border:1px solid var(--sidebar-line);border-radius:6px;box-shadow:0 5px 15px rgba(31,35,41,.08)}
.console-project-menu a{display:block;padding:8px 9px;border-radius:4px;color:var(--text);font-size:12px}
.console-project-menu a:hover{background:#f2f7ff;text-decoration:none}
.console-project-menu a.is-current{background:#e8f3ff;color:#0958d9;font-weight:650}
.console-project-menu .path{display:block;margin-top:2px;color:var(--muted);font-size:11px;font-weight:400}
.console-project-empty{display:block;padding:8px 9px;color:var(--muted);font-size:12px}
.console-navigation{display:flex;flex-direction:column;gap:2px}
.console-sidebar-label{padding:0 10px 7px;color:#8a95a3;font-size:11px;font-weight:650;letter-spacing:.04em}
.console-nav-item{display:flex;align-items:center;gap:10px;min-height:36px;margin:0;padding:7px 10px;border-radius:5px;color:#536071;font-size:13px;transition:background .12s,color .12s}
.console-nav-item:hover{background:#f3f6fa;color:#1f2329;text-decoration:none}
.console-nav-item.is-active{background:var(--sidebar-active);color:var(--accent);font-weight:650;box-shadow:inset 3px 0 0 var(--accent)}
.console-nav-icon{width:17px;text-align:center;color:#8b96a4;font-size:13px}
.console-nav-item.is-active .console-nav-icon{color:var(--accent)}
.console-nav-item .nav-count{margin-left:auto;color:#8e9bab;font-size:11px}
.console-nav-item.is-active .nav-count{color:#d9ebff}
.console-nav-group{margin:1px 0}
.console-nav-group-summary{display:flex;align-items:center;gap:10px;min-height:36px;padding:7px 10px;border-radius:5px;color:#536071;font-size:13px;cursor:pointer;list-style:none}
.console-nav-group-summary::-webkit-details-marker{display:none}
.console-nav-group-summary:hover{background:#f3f6fa;color:#1f2329}
.console-nav-group[open]>.console-nav-group-summary{color:#1f2329;font-weight:650}
.console-nav-chevron{margin-left:auto;color:#98a3af;font-size:14px;transition:transform .15s}
.console-nav-group[open] .console-nav-chevron{transform:rotate(180deg)}
.console-nav-children{margin:2px 0 5px 18px;padding-left:8px;border-left:1px solid #e5ebf2}
.console-nav-children .console-nav-item{min-height:34px;padding-top:6px;padding-bottom:6px;font-size:12px}
.console-nav-empty{padding:7px 10px;color:var(--muted);font-size:12px}
.console-sidebar-footer{margin-top:auto;padding-top:18px}
.console-sidebar-footer .console-nav-item{margin-bottom:8px}
.console-sidebar-note{margin:0 6px;padding:9px 10px;border:1px solid var(--sidebar-line);border-radius:6px;color:#8994a1;background:#fafbfd;font-size:11px;line-height:1.55}
.console-content{margin-left:248px;min-width:0;padding-top:60px}
.console-breadcrumbs{display:flex;align-items:center;gap:8px;min-height:44px;padding:14px 32px 0;color:var(--muted);font-size:12px}
.console-breadcrumbs a{color:#667085}
.console-breadcrumbs a:hover{color:var(--accent);text-decoration:none}
.console-breadcrumbs strong{color:#4e5969;font-weight:600}
.console-breadcrumbs-separator{color:#c9cdd4}
.console-content>main{max-width:none;margin:0;padding:0 32px 46px}
.console-content>footer{max-width:none;margin:0;padding:18px 32px 28px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
.console-overlay{display:none}

/* ---------- 首页：统计与项目表 ---------- */
.console-section-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:25px 0 10px}
.console-section-head h2{margin:0;font-size:17px}
.console-section-head p{margin:0;color:#86909c;font-size:12px}
.console-project-table{overflow:hidden;padding:0}
.console-project-table-head,.console-project-row{display:grid;grid-template-columns:minmax(270px,1.7fr) 110px 110px 150px minmax(205px,1fr);gap:18px;align-items:center;padding:12px 18px}
.console-project-table-head{background:#f7f8fa;border-bottom:1px solid var(--line);color:#86909c;font-size:12px}
.console-project-row{min-height:78px;border-bottom:1px solid #f0f1f3}
.console-project-row:last-child{border-bottom:0}
.console-project-row:hover{background:#fafcff}
.console-project-main{min-width:0}
.console-project-main h2{font-size:15px;margin:0 0 4px}
.console-project-main h2 a{color:#1f2329}
.console-project-main h2 a:hover{color:var(--accent);text-decoration:none}
.console-project-main .path{font-size:11px;color:#86909c;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.console-project-metric strong{display:block;font-size:16px;font-weight:650}
.console-project-metric span{display:block;color:#86909c;font-size:11px}
.console-project-status{color:#4e5969;font-size:12px;white-space:nowrap}
.status-dot{display:inline-block;width:7px;height:7px;margin-right:6px;border-radius:50%;background:#27ae60;vertical-align:1px}
.status-dot-warn{background:#ff9900}
.console-project-actions{display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end}
.console-project-actions .button{min-height:30px;padding:4px 9px;font-size:12px}

/* ---------- 项目研究台 ---------- */
.research-layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:18px;align-items:start}
.sidebar{position:sticky;top:72px;max-height:calc(100vh - 96px);overflow:auto}
.workflow-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.workflow-card{border:1px solid var(--line);border-radius:5px;padding:13px;background:#fff}
.workflow-card h3{margin:0 0 8px;font-size:14px}
.workflow-card ul{margin:0;padding-left:19px}
.workflow-card li{margin:3px 0}
.category-block{margin:0 0 18px}
.category-block h3{font-size:15px;margin:0 0 8px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.category-count{float:right;color:var(--muted);font-weight:400;font-size:12px}
.page-list{list-style:none;padding:0;margin:0}
.page-list li{border-bottom:1px solid #edf0f3;padding:8px 0}
.page-list li:last-child{border-bottom:0}
.page-list a{display:block;line-height:1.4}
.page-filter{margin-bottom:10px}
.prompt{position:relative;padding:14px 52px 14px 14px;background:#f7f9fb;border:1px solid var(--line);border-radius:7px;white-space:pre-wrap;font-family:Consolas,monospace;font-size:13px}
.prompt button{position:absolute;right:8px;top:8px;padding:4px 8px;font-family:inherit;font-size:12px}
.recent-list{list-style:none;margin:0;padding:0}
.recent-list li{padding:8px 0;border-bottom:1px solid #edf0f3}
.recent-list li:last-child{border-bottom:0}

/* ---------- 阅读页 ---------- */
.reading-layout{display:grid;grid-template-columns:265px minmax(0,1fr) 220px;gap:18px;align-items:start}
.doc-toolbar{display:flex;justify-content:space-between;gap:14px;align-items:center;padding:8px 0 14px}
.doc-toolbar .actions{margin:0}
.breadcrumbs{color:#86909c;font-size:12px}
.document{min-width:0;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:28px 34px;box-shadow:var(--shadow);font-size:16px;line-height:1.82}
.document h1,.document h2,.document h3,.document h4{line-height:1.35;margin-top:1.55em;scroll-margin-top:82px}
.document h1:first-child{margin-top:0}
.document h2{padding-bottom:.32em;border-bottom:1px solid #e6eaee}
.document pre{overflow:auto;margin:0;padding:16px;background:#15191f;color:#e7edf5;border-radius:7px;line-height:1.55}
.document code{font-family:Consolas,monospace;font-size:.92em;background:#eef1f4;padding:.12em .3em;border-radius:4px}
.document pre code{background:none;padding:0}
.code-block{position:relative;margin:1em 0}
.code-language{position:absolute;right:10px;top:6px;color:#aab6c4;font-size:11px}
.document blockquote{margin:1em 0;padding:3px 16px;border-left:4px solid #b8c3d0;color:#4e5966}
.callout{margin:1em 0;border:1px solid #bfd0e7;border-left:4px solid var(--accent);background:#f5f9ff;border-radius:6px;padding:12px 15px}
.callout-title{font-weight:700}
.frontmatter{background:#f8fafc;border:1px solid var(--line);border-radius:7px;padding:9px 12px;margin-bottom:20px}
.frontmatter summary{cursor:pointer;font-weight:600}
.frontmatter dl{display:grid;grid-template-columns:minmax(100px,180px) 1fr}
.frontmatter dt,.frontmatter dd{border-top:1px solid #e8ebef;padding:5px 0;margin:0;overflow-wrap:anywhere}
.table-wrap{overflow:auto}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:left}
th{background:#f1f4f7}
figure{margin:1em 0}
figure img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px}
figcaption{color:var(--muted);font-size:12px}
.wikilink{background:#eef4ff;padding:0 3px;border-radius:3px}
.toc{font-size:13px}
.toc ul{list-style:none;margin:0;padding:0}
.toc li{margin:5px 0}
.toc .level-3{padding-left:12px}
.toc .level-4{padding-left:24px}

/* ---------- 科研记录时间线 ---------- */
.records-layout{display:grid;grid-template-columns:230px minmax(0,1fr);gap:22px;align-items:start}
.records-sidebar{position:sticky;top:82px}
.records-guide{font-size:13px;color:var(--muted)}
.records-guide strong{color:var(--text)}
.records-toolbar{display:flex;justify-content:space-between;gap:14px;align-items:end;margin-bottom:18px}
.records-toolbar form{display:flex;gap:8px;flex:1;max-width:680px}
.records-toolbar input{flex:1}
.records-count{color:var(--muted);font-size:13px;white-space:nowrap}
.record-empty{padding:32px 20px;text-align:center}
.record-empty h2{margin-top:0}
.record-callout{margin-bottom:18px;padding:12px 14px;background:#f7faff;border-left:3px solid #91caff;border-radius:6px;color:#526174}
.record-callout code{font-size:12px}
.records-timeline{padding:4px 2px 18px}
.timeline-day{margin:0 0 25px}
.timeline-day-header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 10px;padding:0 0 8px;border-bottom:1px solid var(--line)}
.timeline-day-title{font-size:16px;font-weight:700;letter-spacing:.01em}
.timeline-day-count{color:var(--muted);font-size:12px}
.timeline-items{position:relative}
.timeline-items::before{content:"";position:absolute;left:9px;top:11px;bottom:13px;width:2px;background:linear-gradient(to bottom,#b9cce2,#dce5ee);border-radius:2px}
.timeline-entry{position:relative;padding-left:34px;margin:0 0 13px}
.timeline-entry:last-child{margin-bottom:0}
.timeline-marker{position:absolute;left:2px;top:18px;width:16px;height:16px;border:3px solid var(--panel);border-radius:50%;background:#8ea9c4;box-shadow:0 0 0 2px #8ea9c4;z-index:1}
.timeline-entry:first-child .timeline-marker{background:var(--accent);box-shadow:0 0 0 2px var(--accent),0 0 0 5px #245b9120}
.timeline-card{display:block;padding:16px 18px;background:#fff;border:1px solid var(--line);border-radius:9px;color:var(--text);text-decoration:none;cursor:pointer;transition:border-color .15s ease,box-shadow .15s ease,transform .15s ease}
.timeline-card:hover{border-color:#b7c7e5;box-shadow:0 5px 16px rgba(31,35,41,.08);transform:translateY(-1px);text-decoration:none}
.timeline-card-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:4px}
.timeline-time{font-family:Consolas,"SFMono-Regular",monospace;color:var(--accent);font-size:12px;font-weight:700}
.timeline-card h2{font-size:17px;line-height:1.45;margin:4px 0 7px}
.timeline-card p{margin:0 0 10px;color:#4e5968;line-height:1.7}
.timeline-meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;color:var(--muted);font-size:12px}
.timeline-meta .path{font-size:11px}
.record-document{max-width:900px;margin:0 auto}
.record-document .document-meta{display:flex;flex-wrap:wrap;gap:8px 14px;margin-bottom:18px}
.record-related{margin-top:18px}
.record-related ul{margin:8px 0 0;padding-left:20px}
.record-related li{margin:4px 0}
.record-related .related-file-list{margin:8px 0 0;padding-left:20px}
.record-material-block{margin:6px 0 16px}
.material-grid-hint{color:var(--muted);font-size:12px;margin:0 0 8px}
.record-material-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin:0;padding:0;list-style:none}
.material-thumb{margin:0}
.material-thumb-button{display:flex;flex-direction:column;width:100%;min-height:0;padding:0;border:1px solid var(--line);border-radius:7px;background:#fff;cursor:zoom-in;overflow:hidden;text-align:left;box-shadow:var(--shadow)}
.material-thumb-button:hover{border-color:var(--accent);box-shadow:0 4px 14px rgba(31,35,41,.1);text-decoration:none}
.material-thumb-button img{display:block;width:100%;height:112px;object-fit:contain;background:#f5f7fa;border-bottom:1px solid var(--line)}
.material-thumb-caption{display:block;padding:6px 8px 7px;font-size:12px;line-height:1.4;color:var(--text)}
.material-thumb-name{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:Consolas,"SFMono-Regular",monospace}
.material-thumb-dir{display:block;margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:11px}

/* ---------- 图片灯箱 ---------- */
.lightbox{position:fixed;inset:0;z-index:1000;display:flex;align-items:center;justify-content:center;padding:26px;background:rgba(13,18,26,.86);cursor:zoom-out}
.lightbox[hidden]{display:none}
.lightbox-stage{display:flex;flex-direction:column;align-items:center;max-width:94vw}
.lightbox-image{max-width:94vw;max-height:80vh;object-fit:contain;background:#10141a;border-radius:6px;box-shadow:0 24px 70px rgba(0,0,0,.55)}
.lightbox-caption{margin-top:13px;max-width:88vw;color:#e6ecf3;font-size:13px;font-family:Consolas,"SFMono-Regular",monospace;overflow-wrap:anywhere;text-align:center}
.lightbox-close{position:absolute;top:18px;right:22px;width:44px;height:44px;border:0;border-radius:50%;background:rgba(255,255,255,.12);color:#fff;font-size:24px;line-height:1;cursor:pointer}
.lightbox-close:hover{background:rgba(255,255,255,.22)}
.lightbox-nav{position:absolute;top:50%;transform:translateY(-50%);width:48px;height:64px;border:0;border-radius:8px;background:rgba(255,255,255,.12);color:#fff;font-size:30px;line-height:1;cursor:pointer}
.lightbox-nav:hover{background:rgba(255,255,255,.22)}
.lightbox-prev{left:20px}
.lightbox-next{right:20px}
body.lightbox-open{overflow:hidden}

/* ---------- 科研记录筛选与待办 ---------- */
.tag-cloud{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.tag-chip{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border:1px solid #dbe8f4;border-radius:99px;background:var(--soft);color:var(--text);font-size:12px}
.tag-chip:hover{border-color:var(--accent);text-decoration:none}
.tag-chip.is-active{background:var(--sidebar-active);border-color:var(--accent);color:var(--accent);font-weight:650}
.todo-record-card{margin:0 0 16px;padding:16px 18px}
.todo-record-card h3{margin:0 0 4px}
.todo-section-head{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:8px}
.todo-section-head h2{margin:0}
.todo-list{display:flex;flex-direction:column}
.todo-item{display:flex;gap:8px;align-items:flex-start;margin:5px 0;font-size:14px;line-height:1.55}
.todo-item input{margin-top:4px}
.todo-item input:checked + span{color:var(--muted);text-decoration:line-through}
.todo-done-details{margin-top:18px}
.todo-done-details>summary{cursor:pointer;font-weight:650;font-size:15px;list-style:none;display:flex;align-items:center;gap:6px}
.todo-done-details>summary::-webkit-details-marker{display:none}
.todo-done-details>summary::before{content:"\25B8";transition:transform .15s;color:var(--muted)}
.todo-done-details[open]>summary::before{transform:rotate(90deg)}
.record-pager{display:flex;justify-content:space-between;gap:10px;margin:18px 0}
.record-pager .button:first-child{margin-right:auto}

/* ---------- 检索结果 ---------- */
.search-result{padding:14px 0;border-bottom:1px solid var(--line)}
.search-result h2{font-size:18px;margin:0 0 4px}
.search-result mark{background:#fff0a8}

/* ---------- 文献中心 ---------- */
.literature-app{display:grid;grid-template-columns:250px minmax(0,1fr);gap:0;min-height:calc(100vh - 170px);background:var(--panel);border:1px solid var(--line);border-radius:6px}
.literature-sidebar{position:sticky;top:68px;align-self:start;height:calc(100vh - 88px);overflow:auto;background:#f7f7f8;border-right:1px solid var(--line);padding:14px 10px;border-radius:6px 0 0 6px}
.library-title{display:flex;align-items:center;gap:9px;padding:4px 8px 13px;border-bottom:1px solid var(--line);margin-bottom:12px}
.library-title strong{display:block;line-height:1.3}
.library-title span:last-child{display:block;color:var(--muted);font-size:11px}
.library-mark{display:grid;place-items:center;width:28px;height:28px;border-radius:7px;background:#1677ff;color:#fff;font-weight:800;font-size:13px;flex:0 0 auto}
.library-search{margin:0 4px 12px;background:#fff}
.library-nav-group{margin:13px 0}
.library-nav-group h2{padding:0 9px;margin:0 0 4px;color:#86909c;font-size:11px;line-height:1.5;letter-spacing:.08em;text-transform:uppercase}
.library-nav-item{display:flex;width:100%;align-items:center;justify-content:space-between;gap:10px;border:0;border-radius:6px;padding:6px 9px;background:transparent;color:#1f2329;text-align:left;font-size:13px;line-height:1.4;cursor:pointer}
.library-nav-item:hover{background:#f2f7ff;text-decoration:none}
.library-nav-item.is-active{background:#e8f3ff;color:#0958d9;font-weight:700}
.library-nav-item .nav-count{color:#86909c;font-size:11px;font-variant-numeric:tabular-nums}
.library-nav-separator{height:1px;background:var(--line);margin:12px 8px}
.library-sidebar-help{padding:10px 9px;color:var(--muted);font-size:11px;line-height:1.55}
.literature-content{min-width:0;padding:22px 24px 32px;background:#fbfbfc;border-radius:0 6px 6px 0}
.library-header{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;padding-bottom:18px;border-bottom:1px solid var(--line)}
.library-header h1{font-size:28px;line-height:1.25;margin:2px 0 5px}
.library-header p{margin:0;color:var(--muted)}
.library-header-actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.library-summary{display:flex;gap:8px;flex-wrap:wrap;margin:15px 0}
.summary-pill{display:inline-flex;align-items:baseline;gap:5px;padding:5px 9px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--muted);font-size:12px}
.summary-pill strong{color:var(--text);font-size:14px}
.library-results-head{display:flex;justify-content:space-between;gap:16px;align-items:center;margin:18px 0 10px}
.library-results-head h2{font-size:17px;margin:0}
.view-switch{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.view-switch button{border:0;border-radius:0;padding:5px 9px;font-size:12px}
.view-switch button+button{border-left:1px solid var(--line)}
.view-switch button.is-active{background:#e7dff2;color:#4f3475;font-weight:700}
.literature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:12px}
.literature-grid.is-list{grid-template-columns:1fr}
.paper-card{position:relative;background:#fff;border:1px solid var(--line);border-radius:8px;padding:15px 16px;display:flex;flex-direction:column;min-height:260px;transition:border-color .15s ease,box-shadow .15s ease,transform .15s ease}
.paper-card:hover{border-color:#b7c7e5;box-shadow:0 3px 10px rgba(31,35,41,.08);transform:translateY(-1px)}
.paper-card h2{font-size:16px;line-height:1.45;margin:8px 0 5px}
.paper-card .actions{margin-top:auto}
.paper-card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
.paper-card-top-right{display:flex;align-items:center;gap:8px}
.fav-button{border:0;background:transparent;font-size:20px;line-height:1;color:#c9cdd4;cursor:pointer;padding:0 2px;min-height:0}
.fav-button:hover{color:#f5a623}
.fav-button.is-fav{color:#f5a623}
.paper-file-type{display:inline-grid;place-items:center;min-width:34px;height:22px;border-radius:4px;background:#e8f3ff;color:#0958d9;font-size:10px;font-weight:800;letter-spacing:.04em}
.reading-state{font-size:11px;color:#4e765b}
.reading-state.pending{color:#9a6b20}
.paper-notes{margin:10px 0;padding:9px 11px;background:#f7faff;border-radius:6px;border-left:3px solid #91caff}
.paper-notes ul{margin:4px 0 0;padding-left:18px}
.paper-prompt{margin:10px 0}
.paper-prompt summary{cursor:pointer;font-weight:650;color:var(--accent)}
.paper-path{color:#85808b;font-size:11px;line-height:1.45}
.literature-grid.is-list .paper-card{min-height:0;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 18px}
.literature-grid.is-list .paper-card>.actions{grid-column:2;grid-row:1/6;align-self:center;margin:0;flex-direction:column}
.literature-grid.is-list .paper-notes,.literature-grid.is-list .paper-prompt{max-width:820px}
.literature-filter-empty{display:none;margin:18px 0}
.literature-filter-empty.is-visible{display:block}
.literature-support{margin-top:22px}
.literature-support details+details{margin-top:8px}
.workflow-steps{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;list-style:none;padding:0;margin:14px 0}
.workflow-steps li{position:relative;padding:10px 10px 10px 36px;border:1px solid var(--line);border-radius:7px;background:#fff;font-size:12px}
.workflow-steps strong{display:block;font-size:13px}
.workflow-steps span{position:absolute;left:10px;top:10px;display:grid;place-items:center;width:19px;height:19px;border-radius:50%;background:#7656a7;color:#fff;font-size:10px;font-weight:800}

/* ---------- 原文阅读与对照 ---------- */
.paper-viewer{background:var(--panel);border:1px solid var(--line);border-radius:6px;overflow:hidden}
.paper-viewer-head{display:flex;justify-content:space-between;gap:20px;align-items:end;padding:16px 18px;border-bottom:1px solid var(--line)}
.paper-viewer-head h1{font-size:21px;margin:5px 0 0}
.paper-frame{display:block;width:100%;height:calc(100vh - 205px);min-height:650px;border:0;background:#e9edf1}
.document-fallback{margin:auto}
.document-fallback h2{font-size:18px}
.compare-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;height:calc(100vh - 165px);min-height:700px}
.compare-pane{min-width:0;background:var(--panel);border:1px solid var(--line);border-radius:6px;overflow:hidden;display:flex;flex-direction:column}
.compare-pane-head{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:12px 15px;border-bottom:1px solid var(--line)}
.compare-pane-head h2{font-size:17px;line-height:1.35;margin:2px 0}
.compare-pane .paper-frame{height:100%;min-height:0;flex:1}
.note-pane{overflow:hidden}
.note-document{padding:22px 28px;overflow:auto;flex:1}
.note-document h1{font-size:25px}
.note-selector{display:flex;align-items:end;gap:9px;margin:0 0 12px}
.note-selector label{margin:0}
.note-selector select{max-width:520px}

/* ---------- 响应式 ---------- */
@media(max-width:1050px){.literature-app{grid-template-columns:220px minmax(0,1fr)}.workflow-steps{grid-template-columns:repeat(3,1fr)}}
@media(max-width:980px){.reading-layout{grid-template-columns:230px minmax(0,1fr)}.toc-panel{display:none}.research-layout{grid-template-columns:1fr}.sidebar{position:static;max-height:none}.stats{grid-template-columns:repeat(2,1fr)}.compare-layout{grid-template-columns:1fr;height:auto}.compare-pane{min-height:680px}.note-pane{min-height:520px}.literature-grid{grid-template-columns:1fr}}
@media(max-width:820px){.console-topbar{left:0}.console-topbar-inner{padding:0 15px;gap:10px}.console-menu-button{display:block}.console-topbar-title{min-width:0;flex:1}.console-topbar-title span{display:none}.console-global-search{display:none}.console-runtime{display:none}.console-sidebar{top:60px;width:248px;height:calc(100vh - 60px);transform:translateX(-102%);transition:transform .18s ease;box-shadow:8px 0 20px rgba(0,0,0,.14)}.console-sidebar.is-open{transform:translateX(0)}.console-overlay{position:fixed;inset:60px 0 0;z-index:40;background:rgba(15,23,42,.35)}.console-overlay.is-visible{display:block}.console-content{margin-left:0;padding-top:60px}.console-content>main{padding-left:15px;padding-right:15px}.console-breadcrumbs{padding-left:15px;padding-right:15px}.console-content>footer{padding-left:15px;padding-right:15px}.console-project-table{overflow-x:auto}.console-project-table-head,.console-project-row{min-width:760px}.hero{display:block}.hero .actions{margin-top:14px}.research-layout,.reading-layout{grid-template-columns:1fr}.sidebar{position:static;max-height:none}.toc-panel{display:none}.split{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.doc-toolbar{display:block}.doc-toolbar .actions{margin-top:10px}}
@media(max-width:780px){.literature-app{display:block}.literature-sidebar{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line);border-radius:6px 6px 0 0}.library-nav{display:grid;grid-template-columns:1fr 1fr;gap:8px}.library-nav-group{margin:5px 0}.literature-content{padding:17px 14px 24px;border-radius:0 0 6px 6px}.library-header{display:block}.library-header-actions{justify-content:flex-start;margin-top:12px}.literature-grid{grid-template-columns:1fr}.literature-grid.is-list .paper-card{display:flex}.workflow-steps{grid-template-columns:1fr 1fr}.records-layout{display:block}.records-sidebar{position:static;margin-bottom:14px}.records-toolbar{display:block}.records-toolbar form{max-width:none;margin-top:12px}.records-count{display:block;margin-top:10px}.timeline-card{padding:14px 15px}.timeline-card-head{align-items:flex-start}.timeline-day-title{font-size:15px}}
@media(max-width:720px){.stats,.split{grid-template-columns:1fr}.reading-layout{grid-template-columns:1fr}.reading-layout>.sidebar{display:none}.document{padding:20px 17px}.mobile-only{display:inline-block}}
@media(max-width:520px){.library-nav{display:block}.workflow-steps{grid-template-columns:1fr}.library-summary{grid-template-columns:1fr 1fr}.library-results-head{align-items:flex-start}.paper-card{padding:14px}.view-switch{display:none}.console-content>main{padding-bottom:32px}.hero h1{font-size:22px}.stat{min-height:68px;padding:11px 12px}.stat strong{font-size:20px}.panel,.card{padding:14px}}

/* ---------- 打印 ---------- */
@media print{header,footer,.sidebar,.toc-panel,.doc-toolbar{display:none!important}body,main{background:#fff;margin:0;padding:0}.reading-layout{display:block}.document{border:0;box-shadow:none;padding:0;font-size:12pt}}
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


def recordurl(project_id: str, record_id: str) -> str:
    normalized = record_id.replace(chr(92), "/").lstrip("/")
    prefix = "records/"
    if normalized.lower().startswith(prefix):
        normalized = normalized[len(prefix) :]
    return f"{purl(project_id)}/records/{quote(normalized, safe='/')}"


def source_asset_url(project_id: str, relative: str) -> str:
    normalized = relative.replace(chr(92), "/").lstrip("/")
    return f"{purl(project_id)}/source-asset/{quote(normalized, safe='/')}"



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
  const scope = input.closest('.sidebar') || input.closest('.panel') || document;
  scope.querySelectorAll('[data-page-title]').forEach(el => {
    el.style.display = el.dataset.pageTitle.includes(q) ? '' : 'none';
  });
}
const literatureState = {status: 'all', kind: 'all', query: '', favorite: false};
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
    const favOK = !literatureState.favorite || card.dataset.favorite === '1';
    const queryOK = !literatureState.query || card.dataset.pageTitle.includes(literatureState.query);
    const show = statusOK && kindOK && favOK && queryOK;
    card.hidden = !show;
    if (show) visible += 1;
  });
  const count = document.getElementById('literature-result-count');
  if (count) count.textContent = String(visible);
  const empty = document.getElementById('literature-filter-empty');
  if (empty) empty.classList.toggle('is-visible', cards.length > 0 && visible === 0);
}
function toggleFavorites(button) {
  literatureState.favorite = !literatureState.favorite;
  if (button) button.classList.toggle('is-active', literatureState.favorite);
  applyLiteratureFilters();
}
function initFavorites() {
  const buttons = Array.from(document.querySelectorAll('.fav-button'));
  const setFav = (btn, on) => {
    btn.textContent = on ? '★' : '☆';
    btn.classList.toggle('is-fav', on);
    const card = btn.closest('[data-literature-card]');
    if (card) card.dataset.favorite = on ? '1' : '0';
  };
  buttons.forEach(btn => {
    const key = 'llmwiki-fav-' + btn.dataset.favPath;
    try { setFav(btn, localStorage.getItem(key) === '1'); } catch (_) {}
    btn.addEventListener('click', function(e) {
      e.preventDefault();
      e.stopPropagation();
      const on = !btn.classList.contains('is-fav');
      setFav(btn, on);
      try { localStorage.setItem(key, on ? '1' : '0'); } catch (_) {}
      applyLiteratureFilters();
    });
  });
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
let lightboxGroup = [];
let lightboxIndex = 0;
function openLightbox(src, caption) {
  const lb = document.getElementById('lightbox');
  if (!lb) return;
  lightboxGroup = Array.from(document.querySelectorAll('[data-lightbox-src]'));
  lightboxIndex = Math.max(0, lightboxGroup.findIndex(el => el.dataset.lightboxSrc === src));
  renderLightbox(src, caption);
  lb.hidden = false;
  document.body.classList.add('lightbox-open');
}
function renderLightbox(src, caption) {
  const img = document.getElementById('lightbox-image');
  const cap = document.getElementById('lightbox-caption');
  if (img) { img.src = src; img.alt = caption || ''; }
  if (cap) cap.textContent = caption || '';
}
function closeLightbox() {
  const lb = document.getElementById('lightbox');
  if (lb) lb.hidden = true;
  document.body.classList.remove('lightbox-open');
}
function stepLightbox(delta) {
  if (!lightboxGroup.length) return;
  lightboxIndex = (lightboxIndex + delta + lightboxGroup.length) % lightboxGroup.length;
  const el = lightboxGroup[lightboxIndex];
  if (el) renderLightbox(el.dataset.lightboxSrc, el.dataset.lightboxTitle || '');
}
function initLightbox() {
  const lb = document.getElementById('lightbox');
  if (!lb) return;
  document.addEventListener('click', function(e) {
    const trigger = e.target.closest('[data-lightbox-src]');
    if (trigger) {
      e.preventDefault();
      openLightbox(trigger.dataset.lightboxSrc, trigger.dataset.lightboxTitle || '');
    }
  });
  lb.addEventListener('click', function(e) {
    if (e.target.closest('.lightbox-nav') || e.target.closest('.lightbox-close')) return;
    closeLightbox();
  });
  const closeBtn = lb.querySelector('.lightbox-close');
  const prevBtn = lb.querySelector('.lightbox-prev');
  const nextBtn = lb.querySelector('.lightbox-next');
  if (closeBtn) closeBtn.addEventListener('click', closeLightbox);
  if (prevBtn) prevBtn.addEventListener('click', function() { stepLightbox(-1); });
  if (nextBtn) nextBtn.addEventListener('click', function() { stepLightbox(1); });
  document.addEventListener('keydown', function(e) {
    if (lb.hidden) return;
    if (e.key === 'Escape') closeLightbox();
    else if (e.key === 'ArrowLeft') stepLightbox(-1);
    else if (e.key === 'ArrowRight') stepLightbox(1);
  });
}
function todoIsDone(id) {
  try { return localStorage.getItem('llmwiki-todo-' + id) === '1'; } catch (_) { return false; }
}
function todoSetDone(id, done) {
  try { localStorage.setItem('llmwiki-todo-' + id, done ? '1' : '0'); } catch (_) {}
}
function reorderTodoCard(card) {
  const items = Array.from(card.querySelectorAll('.todo-item'));
  items.sort((a, b) => (a.querySelector('input').checked ? 1 : 0) - (b.querySelector('input').checked ? 1 : 0));
  const container = card.querySelector('.todo-items');
  items.forEach(el => container.appendChild(el));
}
function cardAllDone(card) {
  const boxes = Array.from(card.querySelectorAll('input[type=checkbox]'));
  return boxes.length > 0 && boxes.every(b => b.checked);
}
function placeTodoCard(card) {
  const active = document.getElementById('todos-active-list');
  const done = document.getElementById('todos-done-list');
  (cardAllDone(card) ? done : active).appendChild(card);
  updateTodoCounts();
}
function updateTodoCounts() {
  const active = document.getElementById('todos-active-list');
  const done = document.getElementById('todos-done-list');
  const activeCount = active ? active.querySelectorAll('.todo-record-card').length : 0;
  const doneCount = done ? done.querySelectorAll('.todo-record-card').length : 0;
  const doneCountEl = document.getElementById('todos-done-count');
  if (doneCountEl) doneCountEl.textContent = String(doneCount);
  const empty = document.getElementById('todos-active-empty');
  if (empty) empty.hidden = activeCount !== 0;
  const remaining = Array.from(document.querySelectorAll('.todo-item input')).filter(b => !b.checked).length;
  const totalEl = document.getElementById('todos-total-count');
  if (totalEl) totalEl.textContent = remaining + ' 项';
}
function initTodos() {
  const cards = Array.from(document.querySelectorAll('.todo-record-card'));
  cards.forEach(card => {
    card.querySelectorAll('input[type=checkbox]').forEach(box => {
      box.checked = todoIsDone(box.dataset.todoId);
      box.addEventListener('change', function() {
        todoSetDone(box.dataset.todoId, box.checked);
        reorderTodoCard(card);
        placeTodoCard(card);
      });
    });
    reorderTodoCard(card);
    placeTodoCard(card);
  });
  updateTodoCounts();
}
document.addEventListener('DOMContentLoaded', () => {
  initConsoleShell();
  initLiterature();
  initLightbox();
  initTodos();
  initFavorites();
});
"""

LIGHTBOX_HTML = (
    '<div class="lightbox" id="lightbox" hidden role="dialog" aria-modal="true" aria-label="图片预览">'
    '<button class="lightbox-close" type="button" aria-label="关闭预览">&times;</button>'
    '<button class="lightbox-nav lightbox-prev" type="button" aria-label="上一张">&#8249;</button>'
    '<button class="lightbox-nav lightbox-next" type="button" aria-label="下一张">&#8250;</button>'
    '<div class="lightbox-stage">'
    '<img class="lightbox-image" id="lightbox-image" src="" alt="" draggable="false">'
    '<div class="lightbox-caption" id="lightbox-caption"></div>'
    '</div></div>'
)

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
    href: str,
    label: str,
    icon: str,
    key: str,
    active: str,
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
    parts = ['<a href="/">控制台</a>']
    if project and active in {"overview", "literature", "records", "pages", "todos"}:
        project_id = str(project["id"])
        parts.append(f'<a href="{purl(project_id)}">{esc(project["name"])}</a>')
    breadcrumb_title = {
        "overview": "研究总览",
        "pages": "知识页面",
        "records": "科研记录",
        "todos": "研究待办",
    }.get(active, title)
    if active == "literature" and title.endswith("· 文献中心"):
        breadcrumb_title = "文献中心"
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

    current_name = str(project["name"]) if project else "尚未选择项目"
    project_menu = "".join(
        f'<a class="{"is-current" if str(item.get("id")) == str(current_id) else ""}" href="{purl(str(item["id"]))}">'
        f'{esc(item.get("name", item.get("id", "")))}<span class="path">{esc(item.get("source_root", ""))}</span></a>'
        for item in projects
    )
    if not project_menu:
        project_menu = '<span class="console-project-empty">暂无研究项目</span>'

    project_picker = (
        '<details class="console-project-switcher console-sidebar-project-switcher">'
        '<summary aria-label="切换研究项目">'
        '<span class="console-project-switcher-icon" aria-hidden="true">&#9632;</span>'
        '<span class="console-switcher-copy">'
        '<span class="console-project-switcher-label">当前项目</span>'
        f'<span class="console-project-switcher-name">{esc(current_name)}</span></span>'
        '<span class="console-project-switcher-chevron" aria-hidden="true">&#8964;</span></summary>'
        f'<div class="console-project-menu">{project_menu}'
        '<a href="/settings">管理项目与存储</a></div></details>'
    )

    if project:
        pid = str(project["id"])
        overview_active = "overview" if active in {"overview", "pages"} else active
        project_section = (
            f'<details class="console-nav-group"{" open" if active in {"overview", "literature", "records", "pages", "todos"} else ""}>'
            '<summary class="console-nav-group-summary">'
            '<span class="console-nav-icon" aria-hidden="true">&#9635;</span>'
            '<span>当前项目</span><span class="console-nav-chevron" aria-hidden="true">&#8964;</span>'
            '</summary><div class="console-nav-children">'
            + _console_nav_item(purl(pid), "研究总览", "&#8962;", "overview", overview_active)
            + _console_nav_item(f"{purl(pid)}/literature", "文献中心", "&#9634;", "literature", active)
            + _console_nav_item(f"{purl(pid)}/records", "科研记录", "&#9998;", "records", active)
            + _console_nav_item(f"{purl(pid)}/todos", "研究待办", "&#10003;", "todos", active)
            + '</div></details>'
        )
    else:
        project_section = (
            '<details class="console-nav-group">'
            '<summary class="console-nav-group-summary">'
            '<span class="console-nav-icon" aria-hidden="true">&#9635;</span>'
            '<span>当前项目</span><span class="console-nav-chevron" aria-hidden="true">&#8964;</span>'
            '</summary><div class="console-nav-children">'
            '<div class="console-nav-empty">还没有注册任何研究项目</div>'
            '</div></details>'
        )

    sidebar = (
        '<aside class="console-sidebar" id="console-sidebar">'
        '<a class="console-sidebar-brand" href="/">'
        '<span class="console-brand-mark">LW</span>'
        '<span><strong>LLM Wiki Lite</strong><small>中文科研工作台</small></span>'
        '</a>'
        + project_picker
        + '<nav class="console-navigation" aria-label="主导航">'
        '<div class="console-sidebar-label">导航</div>'
        + _console_nav_item("/", "控制台", "&#8962;", "home", active)
        + project_section
        + _console_nav_item("/search", "科研检索", "&#8981;", "search", active)
        + '</nav>'
        '<div class="console-sidebar-footer">'
        + _console_nav_item("/settings", "设置", "&#9881;", "settings", active)
        + '<div class="console-sidebar-note">把研究过程沉淀为 Markdown 知识页，由人类与 Codex 共同维护。</div>'
        '</div></aside>'
    )
    runtime = '<span class="console-runtime-dot"></span><span>本地服务</span>'
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(title)} &middot; LLM Wiki Lite</title><link rel="stylesheet" href="/static/style.css"></head>'
        '<body><div class="console-shell">'
        '<header class="console-topbar"><div class="console-topbar-inner">'
        '<button class="console-menu-button" type="button" aria-label="打开导航菜单" onclick="toggleConsoleSidebar()">&#9776;</button>'
        f'<div class="console-topbar-title"><span>当前页面</span><strong>{esc(title)}</strong></div>'
        f'<form class="console-global-search" action="/search"><input type="search" name="q" value="{esc(query)}" placeholder="检索论文、方法、实验和结论"><button class="button">搜索</button></form>'
        + f'<div class="console-runtime">{runtime}</div>'
        + '</div></header><div class="console-body">'
        + sidebar
        + '<div class="console-overlay" id="console-overlay"></div><div class="console-content">'
        + _console_breadcrumbs(title, project, active)
        + f'<main>{body}</main><footer>本机 LLM Wiki · 知识页为 Markdown · 仅监听 127.0.0.1 · 不对外发布</footer>'
        + '</div></div></div>' + LIGHTBOX_HTML + '<script>'
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
        more = f'<p class="meta">另有 {len(category_pages)-8} 篇，可在右侧筛选查看。</p>' if len(category_pages) > 8 else ""
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


def _record_day(record: dict[str, Any]) -> tuple[str, str]:
    value = str(record.get("recorded_at") or "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d"), parsed.strftime("%Y\u5e74%m\u6708%d\u65e5")
    except ValueError:
        path = str(record.get("path") or "")
        match = re.search(r"(\d{4})/(\d{2})/(\d{4}-\d{2}-\d{2})\.md$", path)
        if match:
            return match.group(3), f"{match.group(1)}\u5e74{match.group(2)}\u6708{match.group(3)[-2:]}\u65e5"
        return "0000-00-00", "\u672a\u77e5\u65e5\u671f"


def _record_time(record: dict[str, Any]) -> str:
    value = str(record.get("recorded_at") or "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%H:%M")
    except ValueError:
        return ""


def _record_day_groups(records: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    grouped_records: dict[str, list[dict[str, Any]]] = {}
    labels: dict[str, str] = {}
    for record in records:
        day_key, label = _record_day(record)
        grouped_records.setdefault(day_key, []).append(record)
        labels[day_key] = label
    return [(key, labels[key], grouped_records[key]) for key in sorted(grouped_records, reverse=True)]


def records_page(home: str, project_id: str, params: dict[str, list[str]]) -> str:
    project = get_project(project_id, home=home)["project"]
    query = (params.get("q") or [""])[0].strip()
    tag = (params.get("tag") or [""])[0].strip()
    try:
        limit = max(1, min(int((params.get("limit") or ["60"])[0]), MAX_LIST_RECORDS))
    except ValueError:
        limit = 60

    universe = list_records(
        str(project["source_root"]),
        state_root=str(project["state_root"]),
        max_records=MAX_LIST_RECORDS,
    )
    all_tags = sorted(
        {
            str(tag_value)
            for record in universe.get("records") or []
            for tag_value in (record.get("tags") or [])
            if str(tag_value).strip()
        }
    )

    result = list_records(
        str(project["source_root"]),
        state_root=str(project["state_root"]),
        query=query,
        tag=tag,
        max_records=limit,
    )
    records = list(result.get("records") or [])
    total_count = int(result.get("count") or 0)
    truncated = bool(result.get("truncated"))
    day_groups = _record_day_groups(records)

    timeline_days: list[str] = []
    for _, day_label, day_records in day_groups:
        entries: list[str] = []
        for record in day_records:
            record_id = str(record["id"])
            tags_html = "".join(
                f'<span class="badge">{esc(tag_value)}</span>' for tag_value in record.get("tags") or []
            )
            summary = str(record.get("summary") or "").strip()
            related_count = len(record.get("related_files") or []) + len(record.get("related_pages") or [])
            relation_html = (
                f'<span class="timeline-related">关联材料 {related_count} 项</span>'
                if related_count
                else ""
            )
            path_html = f'<span class="path">{esc(str(record.get("path") or ""))}</span>'
            record_time = _record_time(record)
            time_html = f'<time class="timeline-time">{esc(record_time)}</time>' if record_time else ""
            summary_html = f'<p>{esc(summary)}</p>' if summary else ""
            entries.append(
                f"""<article class="timeline-entry"><div class="timeline-marker" aria-hidden="true"></div><a class="timeline-card" href="{recordurl(project_id, record_id)}"><div class="timeline-card-head"><span class="category-tag">阶段性记录</span>{time_html}</div><h2>{esc(record["title"])}</h2>{summary_html}<div class="timeline-meta">{relation_html}{path_html}{tags_html}</div></a></article>"""
            )
        timeline_days.append(
            f'<section class="timeline-day"><div class="timeline-day-header"><span class="timeline-day-title">{esc(day_label)}</span><span class="timeline-day-count">{len(day_records)} 条记录</span></div><div class="timeline-items">{"".join(entries)}</div></section>'
        )
    if timeline_days:
        record_html = '<div class="records-timeline">' + "".join(timeline_days) + "</div>"
    elif query or tag:
        record_html = '<section class="panel record-empty"><h2>没有找到匹配的科研记录</h2><p class="muted">可以尝试研究问题、论文名、实验名或记录标题，或清除标签筛选。</p></section>'
    else:
        record_html = '<section class="panel record-empty"><h2>还没有科研记录</h2><p class="muted">和 Codex 讨论后说“记录刚才的讨论”、就会在当天的日档中追加一条新的阶段性记录。</p></section>'

    tag_chips: list[str] = []
    for tag_value in all_tags:
        if tag_value == tag:
            tag_chips.append(f'<span class="tag-chip is-active">{esc(tag_value)}</span>')
        else:
            tag_chips.append(
                f'<a class="tag-chip" href="{purl(project_id)}/records?tag={quote(tag_value, safe="")}">{esc(tag_value)}</a>'
            )
    tag_cloud_html = (
        '<div class="tag-cloud">' + "".join(tag_chips) + "</div>"
        if tag_chips
        else '<p class="muted">暂无标签</p>'
    )
    clear_html = (
        f'<div class="actions"><a class="button" href="{purl(project_id)}/records?q={quote(query, safe="")}">清除标签筛选</a></div>'
        if tag
        else ""
    )

    load_more_html = ""
    if truncated:
        params_parts = [f"limit={min(limit + 60, MAX_LIST_RECORDS)}"]
        if query:
            params_parts.append(f"q={quote(query, safe='')}")
        if tag:
            params_parts.append(f"tag={quote(tag, safe='')}")
        load_more_url = f"{purl(project_id)}/records?{'&'.join(params_parts)}"
        load_more_html = (
            f'<div class="actions" style="justify-content:center"><a class="button" href="{load_more_url}">'
            f"加载更多（已显示 {len(records)} / 共 {total_count} 条）</a></div>"
        )

    body = f"""<section class="hero"><div><div class="eyebrow">当前项目 · 科研过程</div><h1>科研记录</h1><p>按天归档，把和 Codex 讨论后形成的阶段性理解、决策和待验证问题串成一条研究工作流。</p></div><div class="actions"><a class="button primary" href="{purl(project_id)}">返回研究总览</a><a class="button" href="{purl(project_id)}/todos">研究待办</a></div></section><div class="records-layout"><aside class="records-sidebar"><section class="panel"><h2>记录方式</h2><p class="records-guide"><strong>明确触发，不自动抓取。</strong></p><p class="records-guide">讨论结束后直接告诉 Codex：</p><div class="record-callout"><code>记录刚才的讨论</code></div><p class="records-guide">同一天的多次记录会追加到同一个 Markdown 日档，不覆盖历史，也不把不同主题强行合并。</p></section><section class="panel"><h2>按标签筛选</h2>{tag_cloud_html}{clear_html}</section></aside><section><section class="panel records-toolbar"><form action="{purl(project_id)}/records"><input type="search" name="q" value="{esc(query)}" placeholder="搜索记录标题、阶段性理解或标签"><button class="button primary">检索</button></form><span class="records-count">共 {total_count} 条记录 · {len(day_groups)} 个日档</span></section>{record_html}{load_more_html}</section></div>"""
    return layout("科研记录 · " + str(project["name"]), body, query=query, project_id=project_id, active="records", home=home)


def _section_bullets(content: str, section_title: str) -> list[str]:
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    match = re.search(
        rf"(?ms)^###\s+{re.escape(section_title)}\s*\n(.*?)(?=^###\s|\Z)", text
    )
    if not match:
        return []
    items: list[str] = []
    for line in match.group(1).splitlines():
        bullet = re.match(r"^\s*[-+*]\s+(.+?)\s*$", line)
        if bullet and bullet.group(1).strip():
            items.append(bullet.group(1).strip())
    return items


def _todo_items_html(record_id: str, items: list[str]) -> str:
    rows: list[str] = []
    for index, item in enumerate(items):
        todo_id = f"{record_id}::{index}"
        rows.append(
            f'<label class="todo-item"><input type="checkbox" data-todo-id="{esc(todo_id)}"><span>{esc(item)}</span></label>'
        )
    return "".join(rows)


def todos_page(home: str, project_id: str, params: dict[str, list[str]]) -> str:
    project = get_project(project_id, home=home)["project"]
    result = list_records(
        str(project["source_root"]),
        state_root=str(project["state_root"]),
        max_records=MAX_LIST_RECORDS,
        include_content=True,
    )
    records = list(result.get("records") or [])
    todo_records: list[dict[str, Any]] = []
    total_items = 0
    for record in records:
        content = str(record.get("content") or "")
        items = _section_bullets(content, "尚未解决的问题") + _section_bullets(
            content, "下一步行动"
        )
        total_items += len(items)
        if items:
            todo_records.append({"record": record, "items": items})

    cards: list[str] = []
    for entry in todo_records:
        record = entry["record"]
        record_id = str(record["id"])
        title_html = (
            f'<a href="{recordurl(project_id, record_id)}">{esc(record["title"])}</a>'
        )
        date_html = esc(format_time(str(record.get("recorded_at") or "")))
        cards.append(
            f'<section class="panel todo-record-card" data-record-id="{esc(record_id)}">'
            f'<h3>{title_html}<span class="meta"> · {date_html}</span></h3>'
            f'<div class="todo-items">{_todo_items_html(record_id, entry["items"])}</div>'
            "</section>"
        )

    cards_html = "".join(cards)
    body = f'''<section class="hero"><div><div class="eyebrow">当前项目 · 研究进程</div><h1>研究待办</h1><p>把科研记录中的未解决问题和下一步行动汇总成待办，勾选即归档到「已完成」。</p></div><div class="actions"><a class="button" href="{purl(project_id)}/records">返回科研记录</a><a class="button primary" href="{purl(project_id)}">返回研究总览</a></div></section><section class="panel"><div class="todo-section-head"><h2>待办</h2><span class="meta" id="todos-total-count">{total_items} 项</span></div><div class="todo-list" id="todos-active-list">{cards_html}</div><div class="empty" id="todos-active-empty" hidden>当前没有待办事项 🎉</div></section><details class="panel todo-done-details" id="todos-done"><summary>已完成（<span id="todos-done-count">0</span>）</summary><div class="todo-list" id="todos-done-list"></div></details>'''
    return layout("研究待办 · " + str(project["name"]), body, project_id=project_id, active="todos", home=home)


MATERIAL_THUMBNAIL_SENTINEL = "LLMWIKIMATERIALTHUMBNAILS7F31E9C4"
EVIDENCE_SECTION_TITLE = "依据与关联材料"


def _material_path(item: str) -> str:
    """Normalize a bullet body into a source-relative path when possible."""
    item = item.strip()
    if item.startswith("!") and "](" in item:
        item = item.split("](", 1)[1].rstrip(")").strip()
    elif item.startswith("[") and "](" in item:
        item = item.split("](", 1)[1].rstrip(")").strip()
    return item.strip("` ").replace("\\", "/")


def _is_image_path(item: str) -> bool:
    return Path(_material_path(item)).suffix.lower() in IMAGE_MIME_TYPES


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def material_thumbnail(
    project: dict[str, Any], project_id: str, item: str
) -> str | None:
    """Render a small, lazily loaded, click-to-enlarge thumbnail."""
    normalized = _material_path(item).lstrip("/")
    if Path(normalized).suffix.lower() not in IMAGE_MIME_TYPES:
        return None
    try:
        target = safe_path(
            Path(str(project["source_root"])).resolve(strict=False), normalized
        )
    except (LLMWikiError, OSError, ValueError):
        return None
    if not target.is_file():
        return None
    url = esc(source_asset_url(project_id, normalized))
    label = esc(normalized)
    name = esc(Path(normalized).name)
    parent = esc(str(Path(normalized).parent).replace("\\", "/")) or "/"
    try:
        size_label = _format_bytes(target.stat().st_size)
    except OSError:
        size_label = ""
    dir_line = parent if not size_label else f"{parent} · {size_label}"
    return (
        f'<li class="material-thumb" role="listitem">'
        f'<button type="button" class="material-thumb-button" '
        f'data-lightbox-src="{url}" data-lightbox-title="{label}" title="{label}（点击放大）">'
        f'<img src="{url}" alt="{label}" loading="lazy" decoding="async">'
        f'<span class="material-thumb-caption">'
        f'<span class="material-thumb-name">{name}</span>'
        f'<span class="material-thumb-dir">{esc(dir_line)}</span>'
        f'</span></button></li>'
    )


def _extract_evidence_image_bullets(content: str) -> tuple[str, list[str], bool]:
    """Inline material images into the evidence section as thumbnails.

    Image bullets inside the "依据与关联材料" section are collected and replaced
    by a thumbnail placeholder; plain file bullets are wrapped in inline code so
    underscores in paths are not interpreted as emphasis. Returns
    (transformed_content, image_paths, has_evidence_section).
    """
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    images: list[str] = []
    in_evidence = False
    sentinel_inserted = False
    for line in text.split("\n"):
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            in_evidence = heading.group(2).strip() == EVIDENCE_SECTION_TITLE
            out.append(line)
            if in_evidence and not sentinel_inserted:
                out.extend(["", MATERIAL_THUMBNAIL_SENTINEL, ""])
                sentinel_inserted = True
            continue
        if in_evidence:
            bullet = re.match(
                r"^(?P<indent>\s*)(?P<mark>[-+*])\s+(?P<body>.+?)\s*$", line
            )
            if bullet:
                item = bullet.group("body").strip()
                candidate = _material_path(item)
                if candidate and Path(candidate).suffix.lower() in IMAGE_MIME_TYPES:
                    images.append(candidate)
                    continue
                if "`" not in item and "[" not in item and "]" not in item:
                    out.append(f'{bullet.group("indent")}{bullet.group("mark")} `{item}`')
                    continue
            out.append(line)
        else:
            out.append(line)
    return "\n".join(out), images, sentinel_inserted


def _material_gallery(
    project: dict[str, Any], project_id: str, images: list[str]
) -> str:
    thumbs: list[str] = []
    for item in images:
        thumb = material_thumbnail(project, project_id, item)
        if thumb:
            thumbs.append(thumb)
    if not thumbs:
        return ""
    return (
        '<div class="record-material-block" role="group" aria-label="实验图片预览">'
        f'<div class="material-grid-hint">实验图片 · {len(thumbs)} 张 · 点击放大，再次点击或按 Esc 关闭</div>'
        f'<ul class="record-material-grid" role="list">{"".join(thumbs)}</ul>'
        "</div>"
    )


def _material_panel(
    project_id: str,
    gallery: str,
    text_files: list[str],
    pages: list[str],
) -> str:
    """Fallback panel for materials that are not rendered inline in the body."""
    parts: list[str] = []
    if gallery:
        parts.append(gallery)
    if text_files:
        items = "".join(f"<li><code>{esc(x)}</code></li>" for x in text_files)
        parts.append(f'<h3>其他文件</h3><ul class="related-file-list">{items}</ul>')
    if pages:
        links = "".join(
            f'<li><a href="{pageurl(project_id, x)}">{esc(x)}</a></li>' for x in pages
        )
        parts.append(f'<h3>Wiki 页面</h3><ul>{links}</ul>')
    if not parts:
        return ""
    return '<section class="panel record-related"><h2>关联材料</h2>' + "".join(parts) + "</section>"


def _record_neighbors(
    project_root: str, state_root: str | None, current_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (older, newer) neighbors for chronological record paging."""
    result = list_records(
        project_root, state_root=state_root, max_records=MAX_LIST_RECORDS
    )
    records = list(reversed(list(result.get("records") or [])))
    ids = [str(item["id"]) for item in records]
    if current_id not in ids:
        return None, None
    index = ids.index(current_id)
    prev_record = records[index - 1] if index > 0 else None
    next_record = records[index + 1] if index + 1 < len(records) else None
    return prev_record, next_record


def record_view(home: str, project_id: str, record_id: str) -> str:
    project = get_project(project_id, home=home)["project"]
    result = read_record(
        str(project["source_root"]),
        record_id,
        state_root=str(project["state_root"]),
    )
    record = result["record"]
    content = str(record.get("content") or "")

    related_files = [str(x) for x in record.get("related_files") or [] if str(x).strip()]
    images: list[str] = [x for x in related_files if _is_image_path(x)]
    text_files: list[str] = [x for x in related_files if not _is_image_path(x)]
    pages = [str(x) for x in record.get("related_pages") or [] if str(x).strip()]

    content_for_render, evidence_images, has_evidence = _extract_evidence_image_bullets(content)
    for item in evidence_images:
        if item not in images:
            images.append(item)

    rendered = render_markdown(content_for_render, project_id, str(record["path"]))
    gallery = _material_gallery(project, project_id, images)

    if has_evidence:
        rendered = rendered.replace(f"<p>{MATERIAL_THUMBNAIL_SENTINEL}</p>", gallery or "")
        missing_text = [x for x in text_files if _material_path(x) not in content]
        extra = _material_panel(project_id, "", missing_text, pages) if (missing_text or pages) else ""
    else:
        extra = _material_panel(project_id, gallery, text_files, pages)

    tags_html = "".join(
        f'<span class="badge">{esc(tag)}</span>' for tag in record.get("tags") or []
    )
    prev_record, next_record = _record_neighbors(
        str(project["source_root"]), str(project["state_root"]), str(record["id"])
    )
    prev_link = (
        f'<a class="button" href="{recordurl(project_id, str(prev_record["id"]))}" title="{esc(prev_record["title"])}">&larr; 上一篇</a>'
        if prev_record
        else ""
    )
    next_link = (
        f'<a class="button" href="{recordurl(project_id, str(next_record["id"]))}" title="{esc(next_record["title"])}">下一篇 &rarr;</a>'
        if next_record
        else ""
    )
    pager_html = f'<div class="record-pager">{prev_link}{next_link}</div>' if (prev_link or next_link) else ""
    body = f'''<div class="doc-toolbar"><div class="breadcrumbs"><a href="{purl(project_id)}/records">科研记录</a> / {esc(record["title"])}</div><div class="actions"><a class="button" href="{purl(project_id)}/records">返回记录列表</a></div></div><section class="record-document"><div class="document-meta"><span class="category-tag">科研过程记录</span><span class="meta">记录时间：{esc(format_time(str(record.get("recorded_at") or "")))}</span><span class="meta path">{esc(record["path"])}</span>{tags_html}</div><article class="document">{rendered}</article>{pager_html}{extra}</section>'''
    return layout(str(record["title"]), body, project_id=project_id, active="records", home=home)

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
    try:
        web_port = int(settings.get("web_port") or 8765)
    except (TypeError, ValueError):
        web_port = 8765
    body = f'''{notice(params)}<section class="hero"><div><div class="eyebrow">项目与存储</div><h1>存储设置</h1><p>人类知识与机器状态分开保存；修改位置不会删除旧目录。</p></div></section><div class="help"><strong>推荐：</strong>Windows 用户可把人类可读 Wiki 根目录设为 <code>E:\\wiki_obsidian</code>，新项目会在其中建立独立目录；未设置时默认使用当前项目下的 <code>wiki</code> 目录。</div><h2 class="section-title">默认位置</h2><section class="panel"><form method="post" action="/settings/default-wiki-root"><label>人类可读 Wiki 默认根目录</label><input type="text" name="default_wiki_root" value="{esc(default_root)}" placeholder="例如 E:\\wiki_obsidian"><p class="meta">只影响以后注册且未单独指定 Wiki 位置的项目。</p><label>本地网站端口</label><input type="number" name="web_port" min="1024" max="65535" value="{web_port}"><details class="settings"><summary>高级设置：注册表与机器状态</summary><p class="meta">LLM Wiki 本机注册表目录</p><div class="path">{esc(home)}</div><p class="meta">每个项目的机器状态位置可在下方单独修改。</p></details><div class="actions"><button class="primary">保存默认设置</button></div></form></section><h2 class="section-title">注册研究项目</h2><section class="panel"><form method="post" action="/project/register"><label>项目目录</label><input type="text" name="source_root" required placeholder="Codex 当前打开的项目绝对路径"><label>项目名称（可选）</label><input type="text" name="name" placeholder="默认使用目录名"><label>人类可读 Wiki 目录（可选）</label><input type="text" name="wiki_root" placeholder="留空则使用默认规则"><details class="settings"><summary>高级设置：自定义机器状态目录</summary><label>机器状态目录（可选）</label><input type="text" name="state_root" placeholder="留空则由插件管理"></details><p class="meta">注册只建立项目身份和空存储位置，不等于已经扫描或理解项目。</p><div class="actions"><button class="primary">注册项目</button></div></form></section><h2 class="section-title">已注册项目</h2><div class="grid">{"".join(project_forms) if project_forms else '<div class="panel empty">暂无已注册项目</div>'}</div>'''
    return layout("存储设置", body, active="settings", home=home)
