"""Explicitly scoped local conversation capture. No account-wide transcript scan."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import sqlite3
from time import perf_counter

from research_capture import (
    RolloutCaptureAdapter, open_store, project_for_cwd, read_consent,
    read_session_meta, strip_verbatim_prefix, write_consent, _atomic_write_text,
)
from research_notebook import safe_file
from llmwiki_core import LLMWikiError

SUPPORTED_HOSTS = ('codex',)


def configure_capture(project, hosts, *, now=None):
    """Apply saved capture choices, including defaults inherited by a new project."""
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


def session_bindings(project):
    """Explicit project assignments; never infer a project from conversation text."""
    path = safe_file(Path(project['state_root']), 'workbench/capture-sessions.json')
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('会话关联配置无效。')
    for sid, item in data.items():
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', sid) or not isinstance(item, dict)
                or item.get('project_id') != project['id']):
            raise ValueError('会话关联配置无效。')
        if datetime.fromisoformat(item['since']).tzinfo is None:
            raise ValueError('会话关联起点必须包含时区。')
    return data


def bind_project_session(project, session_id, *, since=None, host_home=None):
    """User-requested exact-session binding; optional historical scope must be explicit."""
    consent = read_consent(project)
    if not (consent['capture_enabled'] and consent['hosts']['codex'] and consent['allow_source_text']):
        raise ValueError('请先明确授权该项目的对话取材。')
    if not isinstance(session_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session_id):
        raise ValueError('会话标识无效。')
    now = datetime.now(timezone.utc).isoformat()
    since = since or now
    if datetime.fromisoformat(since).tzinfo is None or datetime.fromisoformat(since) > datetime.fromisoformat(now):
        raise ValueError('会话关联起点必须是已发生的带时区时间。')
    root = Path(host_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex').resolve()
    indexes = sorted(root.glob('state_*.sqlite'), key=lambda p: int(p.stem.split('_')[-1]) if p.stem.split('_')[-1].isdigit() else -1, reverse=True)
    if not indexes or indexes[0].is_symlink():
        raise ValueError('本机会话索引不可用。')
    with closing(sqlite3.connect(indexes[0].as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        rows = conn.execute('select rollout_path from threads where id=?', (session_id,)).fetchall()
    if len(rows) != 1:
        raise ValueError('找不到唯一的指定会话。')
    path = Path(strip_verbatim_prefix(rows[0][0])).resolve()
    relative = path.relative_to(root).as_posix()
    if not relative.startswith(('sessions/', 'archived_sessions/')) or path.suffix != '.jsonl':
        raise ValueError('会话文件超出允许范围。')
    safe_file(root, relative)
    meta = read_session_meta(path)
    if meta.get('session_id') != session_id or any(t in str(meta.get('thread_source', '')).lower() for t in ('automation', 'heartbeat')):
        raise ValueError('会话身份不一致或属于独立自动任务。')
    bindings = session_bindings(project)
    entry = {'project_id': project['id'], 'since': since, 'bound_at': now}
    if session_id in bindings:
        if bindings[session_id]['since'] != since:
            raise ValueError('已有会话关联起点不同，不能静默扩大历史范围。')
        return bindings[session_id]
    bindings[session_id] = entry
    path = safe_file(Path(project['state_root']), 'workbench/capture-sessions.json')
    _atomic_write_text(path, json.dumps(bindings, ensure_ascii=False, indent=2) + '\n')
    return entry


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
    bindings = session_bindings(project)
    try:
        with closing(sqlite3.connect(index.as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
            cols = {r[1] for r in conn.execute('pragma table_info(threads)')}
            if not {'id', 'cwd', 'rollout_path', 'updated_at'}.issubset(cols):
                return root, [], ['本机会话索引格式变化；未读取对话正文。']
            # Only project metadata. No title, preview, prompt, credential or other project body.
            normalized = "lower(replace(replace(cwd, ?, ''), '\\', '/'))"
            where = normalized + " = ? or " + normalized + " like ? escape '!'"
            params = ["\\\\?\\", prefix, "\\\\?\\", escaped + '/%']
            if bindings:
                where += ' or id in (' + ','.join('?' for _ in bindings) + ')'
                params.extend(bindings)
            rows = conn.execute(
                'select id,cwd,rollout_path from threads where ' + where +
                ' order by updated_at desc,id limit 1001', params).fetchall()
    except (OSError, sqlite3.Error):
        return root, [], ['本机会话索引读取失败；未读取对话正文。']
    gaps = ['该项目会话超过1000条，本轮只处理最近1000条。'] if len(rows) > 1000 else []
    selected = []
    for sid, cwd, raw_path in rows[:1000]:
        if sid in excluded_sessions or (sid not in bindings and not _same_root(cwd, project)):
            continue
        bound = project_for_cwd(cwd, projects)
        if sid not in bindings and (bound is None or bound['id'] != project['id']):
            continue
        try:
            target = Path(strip_verbatim_prefix(raw_path)).resolve()
            relative = target.relative_to(root).as_posix()
            if not relative.startswith(('sessions/', 'archived_sessions/')) or target.suffix != '.jsonl':
                raise ValueError('scope')
            safe_file(root, relative)
            meta = read_session_meta(target)
            bound = project_for_cwd(meta.get('cwd'), projects)
            if meta.get('session_id') != sid or (sid not in bindings and (not bound or bound['id'] != project['id'])):
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
        adapter = RolloutCaptureAdapter(store, root, projects, session_bindings=session_bindings(project))
        # Freeze the target sizes: drain the existing backlog, not an endlessly
        # growing conversation. Each parser read remains bounded to 8 MiB.
        started = perf_counter()
        targets = {path: path.stat().st_size for path in paths}
        pending = list(paths)
        written = 0
        passes = 0
        while pending:
            adapter.rollout_paths = lambda: pending
            report = adapter.scan()
            passes += 1
            written += report['activities_written']
            if report['errors']:
                gaps.append('部分会话读取失败，未推进其读取位置。')
            files = {item['file']: item for item in report['files']}
            remaining = []
            for path in pending:
                item = files.get(adapter._label(path))
                if item is None:
                    gaps.append('部分会话未完成采集。')
                    continue
                if item.get('bad_lines'):
                    gaps.append('部分会话包含无法解析的日志行。')
                if item['offset_to'] >= targets[path]:
                    continue
                if item['offset_to'] <= item['offset_from']:
                    gaps.append('部分会话末尾尚未完整写入，保留读取位置等待下次采集。')
                    continue
                remaining.append(path)
            pending = remaining
        return {'project_id': project['id'], 'written': written,
                'gaps': sorted(set(gaps)), 'complete': not gaps,
                'passes': passes, 'elapsed_ms': round((perf_counter() - started) * 1000),
                'checked_at': datetime.now(timezone.utc).isoformat()}

    finally:
        store.close()
