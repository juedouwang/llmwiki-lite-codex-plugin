"""Report image references only; no image understanding, OCR or network reads.

The agent selects candidates by using their report-image:<id> Markdown target.
Only selected, cited candidates are copied to the existing managed image store.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote

from llmwiki_core import LLMWikiError
from research_capture import CaptureError
from research_notebook import MAX_IMAGE, upload
from research_sources import SENSITIVE_PATTERNS, _Policy, _Unavailable

IMAGE = re.compile(r'!\[([^\]\n]*)\]\(([^)\n]+)\)')
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
TOKEN = 'report-image:'


def _allowed(path, project, *, conversation=False):
    # Never follow a candidate outside its explicit storage/project boundary.
    from research_capture import matches_exclude_patterns, read_consent
    path = path.resolve()
    patterns = [*SENSITIVE_PATTERNS, *read_consent(project).get('exclude_patterns', [])]
    if path.suffix.lower() not in EXTENSIONS or matches_exclude_patterns(str(path), patterns):
        return False
    wiki = Path(project['wiki_root']).resolve()
    if path.is_relative_to(wiki / 'records' / 'assets'):
        return True
    if project.get('source_root'):
        root = Path(project['source_root']).resolve()
        if path.is_relative_to(root):
            return _Policy(project).allows(path.relative_to(root).as_posix())
    # Only explicitly attached clipboard images from this user message, not a
    # scan of Temp, arbitrary file paths, assistant tool output or external URLs.
    return (conversation and path.parent == Path(tempfile.gettempdir()).resolve()
            and bool(re.fullmatch(r'codex-clipboard-[a-f0-9-]+\.(png|jpg|jpeg|gif|webp)', path.name, re.I)))


def discover(item, project):
    kind, text = item.get('kind'), item.get('text', '')
    candidates = []
    if kind in {'manual_note', 'research_record', 'daily_report'}:
        base = Path(project['wiki_root'])
        locator = item.get('locator', '').split('#')[0]
        if kind == 'daily_report':
            match = re.fullmatch(r'report:daily:(\d{4}-\d{2}-\d{2}):(draft|v\d+)', locator)
            locator = f'records/reports/daily-{match[1]}/draft.md' if match else ''
        for alt, target in IMAGE.findall(text):
            target = unquote(target.strip().strip('<>'))
            if target.startswith('/records/assets/'):
                path = base / target.lstrip('/')
            elif locator and not re.match(r'^[a-zA-Z]+:|^[/\\]', target):
                path = (base / locator).parent / target
            else:
                continue
            candidates.append((alt, path))
    elif kind == 'conversation' and text.startswith('user:\n'):
        for name, path in re.findall(r'^## ([^\n]+?): ([^\r\n]+)$', text, re.M):
            candidates.append((name, Path(path.strip())))
    else:
        return
    attachments, seen = [], set()
    for alt, raw in candidates[:64]:
        try:
            path = raw.resolve()
            if not _allowed(path, project, conversation=kind == 'conversation') or str(path) in seen:
                continue
            seen.add(str(path))
            info = path.stat() if path.is_file() else None
            snapshot = [info.st_size, info.st_mtime_ns] if info else None
            aid = hashlib.sha256((item['id'] + str(path) + str(snapshot)).encode()).hexdigest()
            attachments.append({'id': aid, 'target': TOKEN + aid, 'label': alt,
                                'path': str(path), 'snapshot': snapshot,
                                'available': bool(info and 0 < info.st_size <= MAX_IMAGE),
                                'owner_id': project['id']})
        except (OSError, ValueError, LLMWikiError, RuntimeError, CaptureError, _Unavailable):
            continue
    if attachments:
        item['attachments'] = attachments


def materialize(body, items, owner, *, home):
    from research_reports import ReportError, project_for, workspace
    available = {a['target']: (item, a) for item in items for a in item.get('attachments', [])}
    gaps = []
    copied = {}

    def image(match):
        alt, target = match.groups()
        if not target.startswith(TOKEN):
            return match.group(0)
        if target not in available:
            raise ReportError('图片必须来自本轮已引用来源中的附件候选。')
        item, attachment = available[target]
        if target not in copied:
            try:
                project = workspace(home) if attachment['owner_id'] == '__workspace__' else project_for(attachment['owner_id'], home)
                path = Path(attachment['path'])
                if not _allowed(path, project, conversation=item['kind'] == 'conversation'):
                    raise LLMWikiError('图片路径已不在允许范围。')
                info = path.stat()
                if [info.st_size, info.st_mtime_ns] != attachment['snapshot']:
                    raise LLMWikiError('图片在取材后发生变化。')
                if not 0 < info.st_size <= MAX_IMAGE:
                    raise LLMWikiError('图片为空或超过 10 MB。')
                with path.open('rb') as handle:
                    raw = handle.read(MAX_IMAGE + 1)
                name = upload(owner, {'data': base64.b64encode(raw).decode('ascii')})['image']
                copied[target] = '/records/assets/' + name
            except (OSError, ValueError, LLMWikiError, RuntimeError, CaptureError, _Unavailable):
                copied[target] = None
                gaps.append('选中的关键图片不可用或已变化，未保存该图片；请重新提供。')
        if copied[target] is None:
            return f'（图片未收录：{alt or "关键图片"}，原附件不可用或已变化。）'
        return f'![{alt}]({copied[target]})'

    return IMAGE.sub(image, body), sorted(set(gaps))
