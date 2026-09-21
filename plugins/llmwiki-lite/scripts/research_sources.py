"""报告的确定性补充来源：只读项目 Git、变化提示和显式授权的既有活动库。

本模块不采集会话、不猜数据库位置、不写缓存/数据库、不推断科研结论。
Git/提示不需要聊天权限；活动正文必须先通过 capture、host、source_text 三重授权。
所有上限都产生覆盖缺口；observed_at 和数据库位置不参与来源指纹。
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from collections.abc import Mapping
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import research_capture as capture
from llmwiki_core import _ignored

CST = timezone(timedelta(hours=8))
GIT_TIMEOUT = 10
MAX_COMMITS = 64
MAX_PATHS = 128
MAX_GIT_BYTES = 512 * 1024
MAX_DIFF_BYTES = 128 * 1024
MAX_CONFIG_BYTES = 64 * 1024
MAX_HINT_BYTES = 1024 * 1024
MAX_HINT_LINE_BYTES = 64 * 1024
MAX_HINTS = 2000
MAX_ACTIVITIES = 1000
MAX_ACTIVITY_BYTES = 256 * 1024
MAX_TOTAL_TEXT_BYTES = 2 * 1024 * 1024
SENSITIVE_PATTERNS = (
    *capture.DEFAULT_EXCLUDE_PATTERNS, '*.p12', '*.pfx', 'id_rsa*', 'id_ed25519*',
    'credentials*', '**/.ssh/**', '**/.aws/**', '**/.gnupg/**', '**/secrets/**',
)
_MESSAGE_KINDS = {'user_message': 'user', 'assistant_message': 'assistant'}
_AUTO_FIELDS = ('automation_id', 'automation_self', 'is_automation', 'generated_report', 'report_id')
_ORIGIN_FIELDS = ('thread_source', 'origin', 'source', 'kind', 'type')
_AUTO_MARKER = re.compile(r'automation|heartbeat|scheduled|(?:^|[_:/-])cron(?:$|[_:/-])|generated[_-]?report|report[_-](?:generation|summary)', re.I)
_REPORT_PATH = re.compile(r'(?:^|[/\\])records[/\\]reports(?:[/\\]|$)', re.I)


class _Unavailable(Exception):
    """只含固定、不带原文/路径的覆盖缺口。"""


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _clock(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('now 必须是含时区的 datetime。')
    return now.astimezone(timezone.utc)


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('日期必须是 YYYY-MM-DD。')
    return date.fromisoformat(value)


def _midnight(value):
    return datetime.combine(_date(value), day_time(), CST).astimezone(timezone.utc)


def _selected(project, settings):
    pid = project.get('id')
    if not isinstance(pid, str) or not pid:
        raise ValueError('项目缺少 id。')
    return 'project_ids' not in settings or pid in settings['project_ids']


def _state_file(project, raw):
    """相对路径只相对 state_root；解析后不得经链接逃逸。"""
    state = Path(project['state_root']).resolve()
    path = Path(raw)
    if '..' in path.parts:
        raise _Unavailable('项目状态路径越界，未读取来源。')
    path = (path if path.is_absolute() else state / path).resolve()
    try:
        path.relative_to(state)
    except ValueError as exc:
        raise _Unavailable('项目状态路径越界，未读取来源。') from exc
    if path == state:
        raise _Unavailable('项目状态路径不是文件。')
    return path


def _consent(project):
    path = _state_file(project, f'{capture.WORKBENCH_DIR_NAME}/{capture.CONSENT_FILE_NAME}')
    if path.exists() and (not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES):
        raise _Unavailable('采集授权文件不可用或超过读取上限。')
    return capture.read_consent(project)


class _Bundle:
    def __init__(self, project):
        self.project = project
        self.items = []
        self.gaps = set()
        self.size = 0

    def add(self, kind, locator, text, *, occurred=None, observed=None, revision='', certainty='event', version=None):
        text, count = capture.redact(text)
        locator, locator_count = capture.redact(locator)
        if count or locator_count:
            self.gaps.add('部分材料包含敏感字段，已脱敏；脱敏不等于完整秘密检测。')
        size = len(text.encode('utf-8'))
        if self.size + size > MAX_TOTAL_TEXT_BYTES:
            self.gaps.add('来源正文总量超过本轮读取上限，超出部分未计入覆盖。')
            return
        # 事件时间的修正属于来源变化；读取时间和缓存/库路径不是来源变化。
        revision = version or _hash([revision, text, occurred, certainty])
        pid = self.project['id']
        self.items.append({'id': _hash([pid, kind, locator, revision]), 'role': 'source',
                           'project_id': pid, 'kind': kind, 'occurred_at': occurred,
                           'observed_at': observed, 'locator': locator, 'revision': revision,
                           'text': text, 'certainty': certainty})
        self.size += size

    def result(self):
        unique = {item['id']: item for item in self.items}
        return sorted(unique.values(), key=lambda x: (x['kind'], x['locator'], x['id'])), sorted(self.gaps)


class _Policy:
    def __init__(self, project):
        self.root = Path(project['source_root']).resolve()
        self.excludes = list(SENSITIVE_PATTERNS)
        self.excludes.extend(_consent(project).get('exclude_patterns', []))
        self.patterns = []
        ignore = self.root / '.llmwikiignore'
        if ignore.exists():
            try:
                ignore.resolve().relative_to(self.root)
            except ValueError as exc:
                raise _Unavailable('忽略配置越界，未读取 Git/提示正文。') from exc
            with ignore.open('rb') as handle:
                data = handle.read(MAX_CONFIG_BYTES + 1)
            if len(data) > MAX_CONFIG_BYTES:
                raise _Unavailable('忽略配置超过读取上限，未读取 Git/提示正文。')
            self.patterns = [s.replace('\\', '/').lstrip('/') for line in data.decode('utf-8').splitlines()
                             if (s := line.strip()) and not s.startswith(('#', '!'))]
        self.protected = []
        for key in ('state_root', 'wiki_root'):
            if project.get(key):
                self.protected.append(Path(project[key]).resolve())

    def allows(self, value):
        if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
            return False
        normalized = value.replace('\\', '/')
        path = PurePosixPath(normalized)
        if path.is_absolute() or '..' in path.parts or ':' in normalized or path.as_posix() in ('', '.'):
            return False
        if _REPORT_PATH.search(normalized) or '.research-progress' in path.parts:
            return False
        actual = (self.root / normalized).resolve()
        if not actual.is_relative_to(self.root) or any(actual.is_relative_to(p) for p in self.protected):
            return False
        for index in range(1, len(path.parts) + 1):
            part = '/'.join(path.parts[:index])
            if _ignored(part, is_dir=index < len(path.parts), patterns=self.patterns):
                return False
        lowered = normalized.lower()
        for pattern in self.excludes:
            pattern = pattern.lower().replace('\\', '/').lstrip('/')
            variants = (pattern, pattern[3:]) if pattern.startswith('**/') else (pattern,)
            if any(fnmatch.fnmatchcase(lowered, p) or fnmatch.fnmatchcase(path.name.lower(), p)
                   or (p.endswith('/**') and lowered == p[:-3]) for p in variants):
                return False
        return True


class _GitResult(NamedTuple):
    data: bytes
    code: int
    limited: bool


def _git(root, args, *, limit=MAX_GIT_BYTES, input_data=None):
    """有界管道；不用 communicate() 无界缓存，也不写临时差异文件。"""
    env = os.environ.copy()
    for key in list(env):
        if key.startswith('GIT_') and key not in ('GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM'):
            env.pop(key)
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1', LC_ALL='C')
    # check-ignore 的 stdin 本来就是字面路径，不支持 Git 的 literal pathspec 开关。
    literal = [] if args[0] == 'check-ignore' else ['--literal-pathspecs']
    argv = ['git', '--no-pager', *literal, '-c', 'core.fsmonitor=false',
            '-c', 'core.untrackedCache=false', '-c', 'status.renames=true',
            '-c', 'log.showSignature=false', '-c', 'core.pager=cat',
            '-c', 'submodule.recurse=false', '-C', str(root), *args]
    chunks = bytearray()
    limited = threading.Event()
    try:
        proc = subprocess.Popen(argv, shell=False, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError as exc:
        raise _Unavailable('Git 不可用，未计入对应覆盖。') from exc

    def read_output():
        try:
            while block := proc.stdout.read(min(8192, limit + 1 - len(chunks))):
                chunks.extend(block)
                if len(chunks) > limit:
                    limited.set()
                    proc.kill()
                    break
        except OSError:
            limited.set()

    def feed_input():
        try:
            proc.stdin.write(input_data)
            proc.stdin.close()
        except (OSError, BrokenPipeError):
            pass

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    writer = None
    if input_data is not None:
        writer = threading.Thread(target=feed_input, daemon=True)
        writer.start()
    try:
        proc.wait(timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        raise _Unavailable('Git 只读查询超时（10秒），对应材料未计入覆盖。') from exc
    finally:
        reader.join(timeout=1)
        if writer:
            writer.join(timeout=1)
        proc.stdout.close()
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    return _GitResult(bytes(chunks), proc.returncode, limited.is_set())


class _GitProject:
    def __init__(self, policy, bundle):
        self.policy, self.bundle = policy, bundle
        result = _git(policy.root, ['rev-parse', '--show-toplevel'], limit=16384)
        if result.code or result.limited:
            raise _Unavailable('未发现可读取的 Git 仓库；仍可使用文件变化提示。')
        repo = Path(result.data.decode('utf-8').strip()).resolve()
        self.prefix = policy.root.relative_to(repo).as_posix()
        self.prefix = '' if self.prefix == '.' else self.prefix + '/'

    def read(self, args, *, label, limit=MAX_GIT_BYTES):
        result = _git(self.policy.root, args, limit=limit)
        if result.limited:
            self.bundle.gaps.add(f'{label}超过本轮读取上限，已跳过未完整读取的内容。')
            return None
        if result.code:
            raise _Unavailable(f'{label}不可用，未计入对应覆盖。')
        try:
            return result.data.decode('utf-8')
        except UnicodeDecodeError:
            self.bundle.gaps.add(f'{label}不是有效 UTF-8 文本，已跳过。')
            return None

    def allowed(self, paths):
        paths = sorted({p for p in paths if self.policy.allows(p)})
        if len(paths) > MAX_PATHS or sum(len(p.encode('utf-8')) + 1 for p in paths) > 16000:
            self.bundle.gaps.add('变化路径超过本轮读取上限，超出部分未计入覆盖。')
            kept, size = [], 0
            for path in paths[:MAX_PATHS]:
                size += len(path.encode('utf-8')) + 1
                if size > 16000:
                    break
                kept.append(path)
            paths = kept
        if not paths:
            return []
        result = _git(self.policy.root, ['check-ignore', '--no-index', '-z', '--stdin'],
                      input_data=('\0'.join(paths) + '\0').encode('utf-8'))
        if result.limited or result.code not in (0, 1):
            raise _Unavailable('Git 忽略规则无法核验，未读取差异正文。')
        ignored = set(result.data.decode('utf-8').strip('\0').split('\0'))
        return [p for p in paths if p not in ignored]

    def changed(self, args):
        raw = self.read([*args, '--name-status', '-z', '--find-renames', '--', '.'], label='Git 变化路径')
        if raw is None:
            return []
        fields = raw.rstrip('\0').split('\0') if raw else []
        groups, index = [], 0
        while index < len(fields):
            status = fields[index]
            width = 2 if status.startswith(('R', 'C')) else 1
            paths = fields[index + 1:index + 1 + width]
            if len(paths) != width or not re.fullmatch(r'[ACDMRTUXB][0-9]*', status):
                raise _Unavailable('Git 变化路径格式不可用，未读取差异正文。')
            groups.append(paths)
            index += width + 1
        allowed = set(self.allowed([p for group in groups for p in group]))
        # 重命名前后任一路径敏感/被忽略，就不让另一端作为新文件泄露旧正文。
        return sorted({p for group in groups if all(p in allowed for p in group) for p in group})

    def patch(self, args, paths):
        if not paths:
            return ''
        text = self.read([*args, '--patch', '--no-renames', '--unified=3', '--', *paths],
                         label='Git 文本差异', limit=MAX_DIFF_BYTES)
        if text is None:
            return ''
        if '\0' in text:
            self.bundle.gaps.add('Git 差异含二进制内容，未计入文本覆盖。')
            return ''
        if 'Binary files ' in text:
            self.bundle.gaps.add('Git 二进制变化仅提供路径，不读取二进制正文。')
        return text


_DIFF_FLAGS = ['--relative', '--no-ext-diff', '--no-textconv', '--no-color', '--ignore-submodules=all']


def _git_sources(git, day, now, *, metadata_only=False):
    bundle = git.bundle
    start = _midnight(day)
    end = start + timedelta(days=1)
    raw = git.read(['log', '--all', f'--since-as-filter={start.isoformat()}',
                    f'--until={min(end, now).isoformat()}', f'--max-count={MAX_COMMITS + 1}',
                    '--format=%H%x00%cI%x00%P%x00%B', '-z', '--', '.'], label='Git 提交清单')
    fields = raw.removesuffix('\0').split('\0') if raw else []
    if len(fields) % 4:
        bundle.gaps.add('Git 提交清单格式不可用，未读取提交正文。')
        fields = []
    if len(fields) // 4 > MAX_COMMITS:
        bundle.gaps.add(f'Git 提交超过本轮 {MAX_COMMITS} 条上限，未覆盖全部提交。')
    for offset in range(0, min(len(fields), MAX_COMMITS * 4), 4):
        oid, stamp, parents, message = fields[offset:offset + 4]
        occurred = _timestamp(stamp)
        if not re.fullmatch(r'[0-9a-f]{40,64}', oid) or occurred is None:
            bundle.gaps.add('部分 Git 提交身份或时间不可用。')
            continue
        if not (start <= occurred < end and occurred <= now):
            continue
        if metadata_only:
            bundle.add('git_commit', f'git:commit:{oid}',
                       f'Git 提交 {oid}\n提交时间（committer）：{occurred.isoformat()}\n提交说明：\n{message.strip()}',
                       occurred=occurred.isoformat(), observed=occurred.isoformat(), revision=oid)
            continue
        parent = parents.split()[0] if parents else None
        if parent and not re.fullmatch(r'[0-9a-f]{40,64}', parent):
            bundle.gaps.add('Git 父提交身份不可用。')
            continue
        args = ['diff', *_DIFF_FLAGS, parent, oid] if parent else [
            'diff-tree', '--root', '--no-commit-id', '-r', *_DIFF_FLAGS, oid]
        paths = git.changed(args)
        if not paths:
            continue
        patch = git.patch(args, paths)
        text = f'Git 提交 {oid}\n提交时间（committer）：{occurred.isoformat()}\n{message.strip()}\n'
        text += '可用变化路径：\n' + '\n'.join(paths) + '\n\n' + patch
        bundle.add('git_commit', f'git:commit:{oid}', text, occurred=occurred.isoformat(),
                   observed=occurred.isoformat(), revision=oid)
    if metadata_only:
        return
    if _date(day) != now.astimezone(CST).date():
        bundle.gaps.add('历史工作区 status/diff 未捕获，不能据当前状态回填历史。')
        return
    raw = git.read(['status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignore-submodules=all',
                    '--', '.'], label='Git 当前状态')
    if not raw:
        return
    fields = raw.rstrip('\0').split('\0')
    groups, index = [], 0
    while index < len(fields):
        entry = fields[index]
        if len(entry) < 4 or entry[2] != ' ':
            raise _Unavailable('Git 当前状态格式不可用。')
        status, paths = entry[:2], [entry[3:]]
        if 'R' in status or 'C' in status:
            index += 1
            if index >= len(fields):
                raise _Unavailable('Git 重命名状态不完整。')
            paths.append(fields[index])
        # porcelain v1 的路径始终相对仓库根，而不是当前目录。
        if all(p.startswith(git.prefix) for p in paths):
            groups.append((status, [p[len(git.prefix):] for p in paths]))
        index += 1
    allowed = set(git.allowed([p for _, paths in groups for p in paths]))
    groups = [(status, paths) for status, paths in groups if all(p in allowed for p in paths)]
    if not groups:
        return
    paths = sorted({p for _, group in groups for p in group})
    status_text = '\n'.join(f'{status} ' + ' <- '.join(group) for status, group in groups)
    if any(status == '??' for status, _ in groups):
        bundle.gaps.add('未跟踪文件仅记录状态和路径，没有读取正文。')
    staged = git.patch(['diff', '--cached', *_DIFF_FLAGS], paths)
    unstaged = git.patch(['diff', *_DIFF_FLAGS], paths)
    bundle.add('git_worktree', 'git:worktree', '本次工作区观察，不证明历史工作发生时间。\n'
               + status_text + '\n\n暂存差异：\n' + staged + '\n工作区差异：\n' + unstaged,
               observed=now.isoformat(), certainty='observation')


def _automated(value):
    if not isinstance(value, dict):
        return False
    if any(value.get(key) not in (None, '', False, 0, 'false') for key in _AUTO_FIELDS):
        return True
    if any(_AUTO_MARKER.search(str(value.get(key, ''))) for key in _ORIGIN_FIELDS):
        return True
    if any(_REPORT_PATH.search(str(value.get(key, ''))) for key in ('file', 'path', 'locator', 'source_key')):
        return True
    return any(_automated(value[key]) for key in ('metadata', 'session', 'session_meta', 'thread')
               if isinstance(value.get(key), dict))


def _hints(project, day, now, policy, git, bundle):
    path = _state_file(project, 'events.jsonl')
    if not path.is_file():
        bundle.gaps.add('文件变化提示未接入或不存在。')
        return
    total = 0
    with path.open('rb') as handle:
        for number in range(1, MAX_HINTS + 2):
            raw = handle.readline(min(MAX_HINT_LINE_BYTES, MAX_HINT_BYTES - total) + 1)
            if not raw:
                break
            total += len(raw)
            if number > MAX_HINTS or total > MAX_HINT_BYTES or len(raw) > MAX_HINT_LINE_BYTES:
                bundle.gaps.add('文件变化提示超过本轮读取上限，未覆盖剩余记录。')
                break
            try:
                event = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                bundle.gaps.add('部分文件变化提示格式不完整，已跳过。')
                continue
            if not isinstance(event, dict) or event.get('kind') != 'file-change-hint':
                continue
            if event.get('project_id', project['id']) != project['id'] or _automated(event):
                continue
            stamp = _timestamp(event.get('timestamp'))
            if stamp is None:
                stamp = now
                bundle.gaps.add('部分变化提示缺少可信时间，仅可归于本次观察。')
            if stamp > now or stamp.astimezone(CST).date() != _date(day):
                continue
            paths = event.get('paths')
            if not isinstance(paths, list):
                bundle.gaps.add('部分文件变化提示缺少路径，已跳过。')
                continue
            paths = [p for p in paths if policy.allows(p)]
            paths = git.allowed(paths) if git else sorted(set(paths))[:MAX_PATHS]
            if len(event['paths']) > MAX_PATHS:
                bundle.gaps.add('文件变化提示路径超过读取上限，未覆盖全部路径。')
            if paths:
                text = '文件变化提示（仅路径观察，不等同于已完成工作）：\n' + '\n'.join(paths)
                bundle.add('file_change_hint', f'events.jsonl:{number}', text,
                           observed=stamp.isoformat(), certainty='observation',
                           revision=event.get('timestamp') if _timestamp(event.get('timestamp')) else '')


class ActivitySourceUnavailable(RuntimeError):
    """无法验证不等于删除。code 供知识/文献确认逻辑保留待核验状态。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _authorization(project, settings):
    if settings is not None and not _selected(project, settings):
        raise ActivitySourceUnavailable('NOT_AUTHORIZED', '项目未获当前取材设置授权，未读取活动正文。')
    try:
        consent = _consent(project)
    except (capture.CaptureError, _Unavailable, OSError, ValueError, KeyError):
        raise ActivitySourceUnavailable('NOT_AUTHORIZED', '采集授权不可验证，未读取活动正文。') from None
    hosts = {h for h, enabled in consent['hosts'].items() if enabled is True}
    if settings is not None and 'capture_hosts' in settings:
        hosts.intersection_update(settings['capture_hosts'])
    floor = _timestamp(consent.get('authorized_at'))
    if not consent['capture_enabled'] or not consent['allow_source_text'] or not hosts or floor is None:
        raise ActivitySourceUnavailable('NOT_AUTHORIZED', '未授权 capture、host 或 source_text；对话未接入。')
    if settings is not None and settings.get('start_date'):
        floor = max(floor, _midnight(settings['start_date']))
    return consent, hosts, floor


def _activity_path(project, settings):
    if settings is None:
        raw = 'workbench/runtime.sqlite3'
    else:
        paths = settings.get('activity_db_paths')
        raw = paths.get(project['id']) if isinstance(paths, dict) else None
        if not isinstance(raw, str) or not raw:
            raise ActivitySourceUnavailable('NOT_CONFIGURED', '对话未接入：未显式配置该项目的活动库。')
    try:
        path = _state_file(project, raw)
        if not path.is_file():
            raise ActivitySourceUnavailable('UNREADABLE', '既有活动库不存在或不可读；不能据此判定来源删除。')
        # SQLite 可能读取现存 WAL/SHM；这些文件也不能通过链接越出 state_root。
        for suffix in ('-wal', '-shm', '-journal'):
            _state_file(project, str(path) + suffix)
        return path
    except (_Unavailable, OSError, ValueError, KeyError):
        raise ActivitySourceUnavailable('UNREADABLE', '活动库路径不可用或越出项目状态目录。') from None


def _schema(conn):
    if conn.execute('PRAGMA user_version').fetchone()[0] > 1:
        raise ActivitySourceUnavailable('UNSUPPORTED_SCHEMA', '活动库版本较新，未读取活动正文。')
    required = {
        'activities': {'id', 'project_id', 'session_id', 'kind', 'source_key', 'source_revision',
                       'occurred_at', 'observed_at', 'evidence_json', 'status'},
        'sessions': {'id', 'project_id', 'host', 'host_session_id', 'state'},
    }
    columns = {}
    for table, expected in required.items():
        record = conn.execute("SELECT type,sql FROM sqlite_master WHERE name=?", (table,)).fetchone()
        if not record or record[0] != 'table' or 'VIRTUAL TABLE' in (record[1] or '').upper():
            raise ActivitySourceUnavailable('UNSUPPORTED_SCHEMA', '活动库缺少已知实体表，对话不可用。')
        columns[table] = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if not expected.issubset(columns[table]):
            raise ActivitySourceUnavailable('UNSUPPORTED_SCHEMA', '活动库字段不完整，无法先核验项目/宿主授权。')
    return columns


def _activity_version(source_revision, kind, occurred_at):
    """库内 source_revision 是采集器保存的正文版本；不能混入本次读取时间。

    在读正文之前可计算，因此 known_sources 能在 SQL LIMIT 前排除旧批。
    更新活动正文时必须遵守既有 schema 契约，同时更新 source_revision。
    """
    occurred = _timestamp(occurred_at)
    return _hash(['activity-v1', source_revision, kind, occurred.isoformat() if occurred else None])


class _Activities:
    def __init__(self, project, settings, now):
        self.project, self.settings, self.now = project, settings, now
        # 授权在定位/连接数据库以前完成；绝不因打开库而补建 consent 或 schema。
        self.consent, self.hosts, self.floor = _authorization(project, settings)
        from research_capture_runtime import session_bindings
        self.bindings = session_bindings(project)
        self.path = _activity_path(project, settings)
        self.conn = None

    def __enter__(self):
        try:
            self.conn = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=2)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute('PRAGMA query_only=ON')
            self.conn.execute('PRAGMA trusted_schema=OFF')
            deadline = time.monotonic() + 5
            self.conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            self.columns = _schema(self.conn)
            self.conn.execute('BEGIN')
            fields = [f'a.{name}' for name in ('id', 'project_id', 'session_id', 'kind', 'source_key',
                                              'source_revision', 'occurred_at', 'observed_at', 'status')]
            fields += ['s.project_id AS session_project_id', 's.host', 's.host_session_id', 's.state AS session_state']
            for name in (*_AUTO_FIELDS, *_ORIGIN_FIELDS, 'metadata_json'):
                if name in self.columns['sessions']:
                    fields.append(f's.{name} AS session_{name}')
                if name in self.columns['activities'] and name not in ('kind',):
                    fields.append(f'a.{name} AS activity_{name}')
            self.select = 'SELECT ' + ','.join(fields) + ' FROM activities a LEFT JOIN sessions s ON a.session_id=s.id'
            return self
        except BaseException:
            if self.conn:
                self.conn.close()
            raise

    def __exit__(self, *args):
        self.conn.close()

    def session_floor(self, host, session_id):
        binding = self.bindings.get(session_id) if host == 'codex' else None
        floor = _timestamp(binding['since']) if binding else self.floor
        if self.settings and self.settings.get('start_date'):
            floor = max(floor, _midnight(self.settings['start_date']))
        return floor

    def unchanged_consent(self):
        from research_capture_runtime import session_bindings
        current, hosts, floor = _authorization(self.project, self.settings)
        if (current != self.consent or hosts != self.hosts or floor != self.floor
                or session_bindings(self.project) != self.bindings):
            raise ActivitySourceUnavailable('CONSENT_CHANGED', '取材期间授权发生变化，活动来源须重新验证。')

    def rows(self, start, end, known_sources=None):
        instant = "CASE WHEN a.occurred_at IS NULL OR a.occurred_at='' THEN a.observed_at ELSE a.occurred_at END"
        host_slots = ','.join('?' for _ in self.hosts)
        known = dict(known_sources or {})
        def seen(aid, revision, occurred, kind):
            previous = known.get('conversation:' + str(aid))
            return int(previous is not None and previous == _activity_version(revision, kind, occurred))

        self.conn.create_function('llmwiki_source_seen', 4, seen, deterministic=True)
        self.conn.create_function('llmwiki_authorized_since', 2,
                                  lambda host, sid: (self.session_floor(host, sid) - timedelta(seconds=1)).isoformat(),
                                  deterministic=True)
        # 先在 SQL 限定项目/宿主/状态/时间，再取有界元数据；这里不 SELECT evidence_json。
        # SQL 日期精度有限，留一秒边界余量，正文读取前用 datetime 精确核验。
        query = self.select + f" WHERE a.project_id=? AND s.project_id=? AND a.status='active' " \
            f"AND s.state IN ('active','closed') AND s.host IN ({host_slots}) " \
            "AND a.kind IN ('user_message','assistant_message') " \
            "AND NOT llmwiki_source_seen(a.id,a.source_revision,a.occurred_at,a.kind) " \
            f"AND (julianday({instant}) IS NULL OR (julianday({instant})>=max(julianday(?), " \
            "julianday(llmwiki_authorized_since(s.host,s.host_session_id))) " \
            f"AND julianday({instant})<=julianday(?))) ORDER BY {instant},a.id LIMIT ?"
        args = [self.project['id'], self.project['id'], *sorted(self.hosts),
                (start - timedelta(seconds=1)).isoformat(),
                (end + timedelta(seconds=1)).isoformat(), MAX_ACTIVITIES + 1]
        return self.conn.execute(query, args).fetchall()

    def source(self, row, bundle, *, start=None, end=None):
        self.unchanged_consent()
        row = dict(row)
        if row['project_id'] != self.project['id'] or row['session_project_id'] != self.project['id']:
            raise ActivitySourceUnavailable('SOURCE_EXCLUDED', '活动项目绑定不一致，未读取正文。')
        if row['status'] != 'active' or row['session_state'] not in ('active', 'closed'):
            raise ActivitySourceUnavailable('SOURCE_EXCLUDED', '活动或会话已排除/撤回，不能判定为删除。')
        if row['host'] not in self.hosts:
            raise ActivitySourceUnavailable('NOT_AUTHORIZED', '该活动宿主未获授权，未读取正文。')
        if row['kind'] not in _MESSAGE_KINDS:
            raise ActivitySourceUnavailable('SOURCE_EXCLUDED', '该活动不是用户/助手文字。')
        for prefix in ('session_', 'activity_'):
            meta = {key[len(prefix):]: value for key, value in row.items() if key.startswith(prefix)}
            if meta.get('metadata_json'):
                try:
                    meta['metadata'] = json.loads(meta['metadata_json'])
                except (ValueError, TypeError, RecursionError):
                    raise ActivitySourceUnavailable('INVALID_SOURCE', '活动线程元数据不可验证。') from None
            if _automated(meta):
                raise ActivitySourceUnavailable('SOURCE_EXCLUDED', '自动化/生成报告活动已排除。')
        occurred = _timestamp(row['occurred_at'])
        observed = _timestamp(row['observed_at'])
        if row['occurred_at'] not in (None, '') and occurred is None:
            raise ActivitySourceUnavailable('INVALID_SOURCE', '活动发生时间格式不可验证。')
        event = occurred or observed
        if event is None or observed is None:
            raise ActivitySourceUnavailable('INVALID_SOURCE', '活动缺少可信事件/观察时间。')
        floor = self.session_floor(row['host'], row['host_session_id'])
        if event < floor or observed < floor or event > self.now or observed > self.now:
            raise ActivitySourceUnavailable('SOURCE_EXCLUDED', '活动不在已授权且已发生的时间范围内。')
        if (start and event < start) or (end and event >= end):
            return
        if not all(isinstance(row[k], str) and row[k] for k in ('id', 'source_key', 'source_revision')):
            raise ActivitySourceUnavailable('INVALID_SOURCE', '活动身份或版本字段不可验证。')
        if len(row['id']) > 256 or len(row['source_key']) > 4096 or len(row['source_revision']) > 256:
            raise ActivitySourceUnavailable('INVALID_SOURCE', '活动元数据超过读取上限。')
        if _automated({'source_key': row['source_key']}):
            raise ActivitySourceUnavailable('SOURCE_EXCLUDED', '生成报告来源已排除。')
        # 两阶段读取：身份、时间、host、当前授权均通过后，才 SELECT 有界正文。
        result = self.conn.execute(
            'SELECT CASE WHEN length(CAST(evidence_json AS BLOB))<=? THEN evidence_json END '
            'FROM activities WHERE id=? AND project_id=? AND status=\'active\'',
            (MAX_ACTIVITY_BYTES, row['id'], self.project['id'])).fetchone()
        if not result or result[0] is None:
            raise ActivitySourceUnavailable('INVALID_SOURCE', '活动正文不可用或超过读取上限，未计入覆盖。')
        try:
            evidence = json.loads(result[0])
        except (ValueError, TypeError, RecursionError):
            raise ActivitySourceUnavailable('INVALID_SOURCE', '活动正文结构不可验证。') from None
        role = _MESSAGE_KINDS[row['kind']]
        if not isinstance(evidence, dict) or not isinstance(evidence.get('text'), str):
            raise ActivitySourceUnavailable('INVALID_SOURCE', '活动没有已知的用户/助手文字字段。')
        if (evidence.get('host', row['host']) != row['host']
                or evidence.get('project_id', self.project['id']) != self.project['id']
                or evidence.get('kind', row['kind']) != row['kind']
                or evidence.get('role', role) != role):
            raise ActivitySourceUnavailable('SOURCE_EXCLUDED', '活动正文与已授权身份不一致，已排除。')
        text = evidence['text']
        if (evidence.get('tool_name') or evidence.get('call_id') or _automated(evidence)
                or evidence.get('origin') in ('function_call', 'function_call_output', 'tool_call', 'tool_result')
                or text.lstrip().startswith(capture.INJECTION_PREFIXES) or capture.is_automatic_trigger(text)):
            raise ActivitySourceUnavailable('SOURCE_EXCLUDED', '工具调用、注入指令或自动生成活动已排除。')
        patterns = [*SENSITIVE_PATTERNS, *self.consent.get('exclude_patterns', [])]
        if (capture.matches_exclude_patterns(text, patterns)
                or capture.matches_exclude_patterns(str(evidence.get('file', '')), patterns)):
            raise ActivitySourceUnavailable('SOURCE_EXCLUDED', '活动涉及敏感/生成路径，已排除正文。')
        if not text.strip():
            raise ActivitySourceUnavailable('INVALID_SOURCE', '活动没有可用文字，不能判定为删除。')
        if not occurred:
            bundle.gaps.add('部分对话缺少发生时间，按已记录的观察时间归属，不回填历史。')
        bundle.add('conversation', 'conversation:' + row['id'], role + ':\n' + text,
                   occurred=occurred.isoformat() if occurred else None, observed=observed.isoformat(),
                   revision=row['source_revision'], certainty='event' if occurred else 'observation',
                   version=_activity_version(row['source_revision'], row['kind'], row['occurred_at']))


def _activity_range(project, *, settings, start, end, now, known_sources=None):
    bundle = _Bundle(project)
    if start > now or end <= start:
        return bundle.result()
    try:
        with _Activities(project, settings, now) as reader:
            rows = reader.rows(start, min(end, now + timedelta(microseconds=1)), known_sources)
            if len(rows) > MAX_ACTIVITIES:
                bundle.gaps.add(f'活动超过本轮 {MAX_ACTIVITIES} 条上限，未覆盖全部对话。')
            for row in rows[:MAX_ACTIVITIES]:
                try:
                    reader.source(row, bundle, start=start, end=end)
                except ActivitySourceUnavailable as exc:
                    if exc.code in ('NOT_AUTHORIZED', 'CONSENT_CHANGED'):
                        raise
                    # 排除工具/自动化是边界，而非缺失正文；坏数据和超限须报告。
                    if exc.code != 'SOURCE_EXCLUDED':
                        bundle.gaps.add(str(exc))
            reader.unchanged_consent()
    except ActivitySourceUnavailable as exc:
        if exc.code in ('NOT_AUTHORIZED', 'CONSENT_CHANGED'):
            bundle.items.clear()
        bundle.gaps.add(str(exc))
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
        bundle.gaps.add('既有活动库不可读或查询受限，不能声称对话覆盖完整。')
    return bundle.result()


def activity_sources(project, *, settings, start_date, now, known_sources=None):
    """读取已存在、已授权的用户/助手文字，返回 (items, gaps)。

    known_sources 可选，格式 {"conversation:<activity_id>": 上次返回的 revision}。
    已消费且版本未变的活动在 SQL LIMIT 前跳过，同 id 修订仍会返回；调用方应仅
    在成功消费后更新映射。日报不传此参数，超限如实报 gap。settings=None 仅访问
    canonical state_root/workbench/runtime.sqlite3，不搜索其他库。
    """
    if known_sources is not None and (not isinstance(known_sources, Mapping)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in known_sources.items())):
        raise ValueError('known_sources 必须是 locator 到 revision 的映射。')
    now = _clock(now)
    return _activity_range(project, settings=settings, start=_midnight(start_date),
                           end=now + timedelta(microseconds=1), now=now, known_sources=known_sources)


def read_activity_source(project, locator, *, settings=None, now=None):
    """按 conversation:<activity_id> 重验来源。

    None 仅表示在已授权、可读的指定库内确认该项目记录不存在。
    未授权、库丢失/损坏、未知 schema、排除/撤回均抛 ActivitySourceUnavailable，
    不得被调用方解释成原文删除。settings=None 只允许 canonical runtime.sqlite3。
    """
    if not isinstance(locator, str) or not locator.startswith('conversation:') or not 0 < len(locator[13:]) <= 256:
        raise ActivitySourceUnavailable('INVALID_LOCATOR', '活动定位符必须是 conversation:<activity_id>。')
    now = _clock(now if now is not None else datetime.now(timezone.utc))
    try:
        with _Activities(project, settings, now) as reader:
            row = reader.conn.execute(reader.select + ' WHERE a.id=? AND a.project_id=?',
                                      (locator[13:], project['id'])).fetchone()
            reader.unchanged_consent()
            if row is None:
                return None
            bundle = _Bundle(project)
            reader.source(row, bundle)
            reader.unchanged_consent()
            if not bundle.items:
                raise ActivitySourceUnavailable('INVALID_SOURCE', '活动正文未能完整返回，不能判定为删除。')
            return bundle.items[0]
    except ActivitySourceUnavailable:
        raise
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
        raise ActivitySourceUnavailable('UNREADABLE', '既有活动库不可读，不能判定为来源删除。') from None


def extra_sources(project, day, *, settings, now, report_only=False):
    """补充某个北京时间自然日的来源，不调用采集器、不读取账户会话文件。"""
    now = _clock(now)
    target = _date(day)
    bundle = _Bundle(project)
    if not _selected(project, settings):
        bundle.gaps.add('项目未获当前取材设置授权，未读取补充来源。')
        return bundle.result()
    if target > now.astimezone(CST).date():
        bundle.gaps.add('未来日期未覆盖，未读取补充来源。')
        return bundle.result()
    if settings.get('start_date') and target < _date(settings['start_date']):
        bundle.gaps.add('目标日期早于取材起始日，未读取补充来源。')
        return bundle.result()
    git, policy = None, None
    try:
        policy = _Policy(project)
        git = _GitProject(policy, bundle)
        _git_sources(git, day, now, metadata_only=report_only)
    except _Unavailable as exc:
        bundle.gaps.add(str(exc))
    except (capture.CaptureError, OSError, ValueError, KeyError, RuntimeError):
        bundle.gaps.add('Git/忽略规则不可验证，未声称覆盖对应材料。')
    if policy and not report_only:
        try:
            _hints(project, day, now, policy, git, bundle)
        except _Unavailable as exc:
            bundle.gaps.add(str(exc))
        except (OSError, ValueError, KeyError, RuntimeError):
            bundle.gaps.add('文件变化提示不可读，未计入对应覆盖。')
    items, gaps = _activity_range(project, settings=settings, start=_midnight(day),
                                 end=_midnight(day) + timedelta(days=1), now=now)
    for item in items:
        size = len(item['text'].encode('utf-8'))
        if bundle.size + size > MAX_TOTAL_TEXT_BYTES:
            bundle.gaps.add('来源正文总量超过本轮读取上限，超出部分未计入覆盖。')
            break
        bundle.items.append(item)
        bundle.size += size
    bundle.gaps.update(gaps)
    return bundle.result()
