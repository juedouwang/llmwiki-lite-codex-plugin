"""Explicitly scoped local conversation capture. No account-wide transcript scan."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from research_capture import (
    RolloutCaptureAdapter, open_store, project_for_cwd, read_consent,
    read_session_meta, strip_verbatim_prefix, write_consent,
)
from research_notebook import safe_file
from llmwiki_core import LLMWikiError

SUPPORTED_HOSTS = ('codex',)


def configure_capture(project, hosts, *, now=None):
    """Only called when the user explicitly saves capture choices."""
    if not isinstance(hosts, list) or any(h not in SUPPORTED_HOSTS for h in hosts):
        raise ValueError('当前仅支持已验证的本机会话适配器。')
    current = read_consent(project)
    if (current['capture_enabled'] == bool(hosts) and current['allow_source_text'] == bool(hosts)
            and {h for h, enabled in current['hosts'].items() if enabled} == set(hosts)):
        return current
    return write_consent(project, {
        'capture_enabled': bool(hosts),
        'hosts': {h: h in hosts for h in current['hosts']},
        'allow_source_text': bool(hosts),
    }, now=now)


def _same_root(raw, project):
    if not isinstance(raw, str):
        return False
    try:
        return Path(strip_verbatim_prefix(raw)).resolve().is_relative_to(Path(project['source_root']).resolve())
    except (OSError, ValueError):
        return False


def indexed_rollouts(project, projects, *, host_home=None, excluded_sessions=()):
    """Query only cwd metadata for the authorized root, then read matched transcripts.

    The local index is an optional, version-checked adapter, not a public API.
    Missing/changed schema is a coverage gap; never fall back to scanning all chats.
    """
    root = Path(host_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex').resolve()
    candidates = sorted(root.glob('state_*.sqlite'), key=lambda p: int(p.stem.split('_')[-1]) if p.stem.split('_')[-1].isdigit() else -1, reverse=True)
    if not candidates:
        return root, [], ['本机会话索引不可用；未扫描账户历史。']
    index = candidates[0]
    if index.is_symlink():
        return root, [], ['本机会话索引路径不可用。']
    prefix = str(Path(project['source_root']).resolve()).replace('\\', '/').lower()
    escaped = prefix.replace('!', '!!').replace('%', '!%').replace('_', '!_')
    try:
        with closing(sqlite3.connect(index.as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
            cols = {r[1] for r in conn.execute('pragma table_info(threads)')}
            if not {'id', 'cwd', 'rollout_path', 'updated_at'}.issubset(cols):
                return root, [], ['本机会话索引格式变化；未读取对话正文。']
            # Only project metadata. No title, preview, prompt, credential or other project body.
            normalized = "lower(replace(replace(cwd, ?, ''), '\\', '/'))"
            rows = conn.execute(
                "select id,cwd,rollout_path from threads where " + normalized + " = ? or " +
                normalized + " like ? escape '!' order by updated_at desc,id limit 1001",
                ("\\\\?\\", prefix, "\\\\?\\", escaped + '/%')).fetchall()
    except (OSError, sqlite3.Error):
        return root, [], ['本机会话索引读取失败；未读取对话正文。']
    gaps = ['该项目会话超过1000条，本轮只处理最近1000条。'] if len(rows) > 1000 else []
    selected = []
    for sid, cwd, raw_path in rows[:1000]:
        if sid in excluded_sessions or not _same_root(cwd, project):
            continue
        bound = project_for_cwd(cwd, projects)
        if bound is None or bound['id'] != project['id']:
            continue
        try:
            target = Path(strip_verbatim_prefix(raw_path)).resolve()
            relative = target.relative_to(root).as_posix()
            if not relative.startswith(('sessions/', 'archived_sessions/')) or target.suffix != '.jsonl':
                raise ValueError('scope')
            safe_file(root, relative)
            meta = read_session_meta(target)
            bound = project_for_cwd(meta.get('cwd'), projects)
            if meta.get('session_id') != sid or not bound or bound['id'] != project['id']:
                gaps.append('会话索引与原文项目不一致，已跳过。')
                continue
            if any(token in str(meta.get('thread_source', '')).lower() for token in ('automation', 'heartbeat')):
                continue
            selected.append(target)
        except (OSError, ValueError, LLMWikiError):
            gaps.append('部分已关联会话不可读或越界。')
    return root, selected, sorted(set(gaps))


def capture_project(project, projects, *, host_home=None, excluded_sessions=()):
    consent = read_consent(project)
    if not consent['capture_enabled'] or not consent['hosts']['codex'] or not consent['allow_source_text']:
        return {'project_id': project['id'], 'written': 0, 'gaps': ['未授权当前项目的本机对话取材。']}
    root, paths, gaps = indexed_rollouts(project, projects, host_home=host_home, excluded_sessions=excluded_sessions)
    if not paths:
        return {'project_id': project['id'], 'written': 0, 'gaps': gaps or ['没有可读取的已关联项目会话。']}
    safe_file(Path(project['state_root']), 'workbench/runtime.sqlite3')
    store = open_store(project['state_root'])
    try:
        adapter = RolloutCaptureAdapter(store, root, projects)
        adapter.rollout_paths = lambda: paths  # Explicit metadata-selected set; never account scan.
        report = adapter.scan()
        if report['errors']:
            gaps.append('部分会话读取失败，未推进其读取位置。')
        if report['truncated']:
            gaps.append('部分会话本轮未读完，下次继续。')
        return {'project_id': project['id'], 'written': report['activities_written'],
                'gaps': sorted(set(gaps)), 'checked_at': datetime.now(timezone.utc).isoformat()}
    finally:
        store.close()
