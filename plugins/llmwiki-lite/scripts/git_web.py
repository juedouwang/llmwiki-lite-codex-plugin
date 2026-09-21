"""Explicit, project-bound Git HTTP adapter; no scheduler or model calls."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
import uuid
from functools import lru_cache
from datetime import datetime, timezone
from urllib.parse import urlsplit

from git_service import (GitError, GitLockError, RepositoryLock, _run_git_command,
                         detect_git_executable, get_git_version, get_head_info)
from git_graph import build_graph, GraphSnapshotExpiredError
from git_operations import commit_selected_files, create_branch, set_author
from git_merge import merge_fixed_commit
from git_revert_restore import restore_tree_as_commit
from llmwiki_core import LLMWikiError
from llmwiki_registry import get_project, _write_json

LIMIT = 1024 * 1024
PREVIEWS: dict = {}
PREVIEW_LOCK = threading.Lock()
ACTIONS = {
    'save': set(), 'create_branch': {'target_oid'}, 'switch_branch': {'branch'},
    'merge': {'source_branch'}, 'restore': {'target_oid'},
    'pull_apply': {'remote_id', 'branch', 'fetched_oid'},
    'push': {'remote_id', 'target_branch'},
}


class WebGitError(Exception):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


def fail(code, message, status=409):
    raise WebGitError(code, message, status)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')
    return hashlib.sha256(value).hexdigest()


def fields(data, required=(), optional=()):
    if not isinstance(data, dict) or set(data) - set(required) - set(optional) or set(required) - set(data):
        fail('invalid_request', '请求字段缺失或不受支持。', 400)


def text(value, maximum=1024):
    if not isinstance(value, str) or not value.strip() or '\0' in value or len(value) > maximum:
        fail('invalid_request', '请输入有效文字。', 400)
    return value


def scrub_url(value):
    try:
        if '://' in value:
            p = urlsplit(value)
            return f'{p.scheme}://{p.hostname or "远端"}{p.path}'
        return re.sub(r'^[^/@\s]+@', '', value).split('?')[0].split('#')[0]
    except ValueError:
        return '已配置远端'


def git_failure(exc, network=False):
    raw = getattr(exc, 'stderr', '') or ''
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', 'replace')
    raw = raw.lower()
    if 'already checked out' in raw or 'already used by worktree' in raw:
        fail('branch_in_use', '该分支正在其他工作树使用；请切换其他分支，或在原 Git 工具中释放后刷新。')
    if 'index.lock' in raw or 'cannot lock ref' in raw:
        fail('repo_busy', '仓库正由其他操作使用，请稍后重试。')
    if any(x in raw for x in ('authentication', 'permission denied', 'terminal prompts disabled', 'could not read username', 'host key verification')):
        fail('auth_required', '认证或主机信任尚未就绪，请在现有 Git 工具配置后重试。', 422)
    if network:
        fail('git_failed', '远端操作未完成，请检查连接和目标；不会自动重试或强制上传。', 500)
    fail('git_failed', 'Git 未完成操作，请检查仓库状态后重新确认。', 500)


@lru_cache(maxsize=4)
def checked_git():
    exe = detect_git_executable()
    get_git_version(exe)
    return exe


class Repo:
    def __init__(self, home, project_id, worktree_id=None):
        # A server launched from a Git hook/tool must not inherit another
        # worktree's index, object store, namespace or repository selection.
        if any(os.environ.get(k) for k in ('GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE',
                'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE')):
            fail('unsupported_repo', '服务继承了自定义 Git 仓库环境，请从普通环境重启；不会操作其他工作树。')
        self.home = home
        try:
            self.project = get_project(project_id, home=home)['project']
        except (LLMWikiError, ValueError):
            fail('invalid_request', '项目不存在。', 400)
        self.pid = self.project['id']
        self.root = Path(self.project['source_root']).resolve()
        self.state = Path(self.project['state_root']).resolve() / 'git-web'
        self.exe = checked_git()
        self.registered_root = self.root
        self.locate()
        self.registered_common = self.common
        self.worktree_id = worktree_id or ''
        if self.worktree_id:
            if not isinstance(self.worktree_id, str) or not re.fullmatch(r'[0-9a-f]{64}', self.worktree_id):
                fail('invalid_worktree', '工作树标识无效，请返回注册目录重新选择。', 400)
            worktrees = self.worktrees()
            if self.worktree_reason(worktrees):
                fail('worktree_unavailable', '注册目录的 Git 绑定已失效，请修复后重新选择。')
            selected = next((w for w in worktrees if self.worktree_token(w['path']) == self.worktree_id), None)
            if not selected or selected['bare'] or selected['prunable'] or not Path(selected['path']).is_dir():
                fail('worktree_unavailable', '该工作树已移走或不可用，请返回注册目录重新选择。')
            self.root = Path(selected['path'])
            try:
                self.locate()
                reason = self.worktree_reason(worktrees)
                if self.common != self.registered_common or reason:
                    fail('worktree_unavailable', reason or '工作树不再属于注册仓库。')
            except (GitError, OSError, ValueError, subprocess.CalledProcessError, WebGitError):
                fail('worktree_unavailable', '工作树绑定已失效，请返回注册目录重新选择。')
            if self.root != self.registered_root:
                self.state = self.state / 'worktrees' / self.worktree_id
        self.key = digest(str(self.common).casefold() if os.name == 'nt' else str(self.common))
        self.binding = digest([str(Path(home).resolve()), self.pid, str(self.registered_root), str(self.root), str(self.common), str(self.gitdir)])
        self._config = None

    def locate(self):
        # One process for related metadata; these are re-read for every request.
        meta = self.git('rev-parse', '--path-format=absolute', '--show-toplevel',
                        '--git-common-dir', '--absolute-git-dir', '--show-object-format',
                        '--show-superproject-working-tree', check=False)
        values = meta.stdout.splitlines()
        if meta.returncode or len(values) < 4 or Path(values[0]).resolve() != self.root:
            fail('unsupported_repo', '请注册代码仓库根目录；不会操作其父仓库或裸仓库。')
        self.common = Path(values[1]).resolve()
        self.gitdir = Path(values[2]).resolve()
        self.oid_length = 64 if values[3] == 'sha256' else 40
        self.superproject = values[4] if len(values) > 4 else ''

    def worktree_token(self, path):
        parts = [str(Path(self.home).resolve()), self.pid, str(self.registered_root),
                 str(self.registered_common), str(Path(path).resolve())]
        return digest([v.casefold() for v in parts] if os.name == 'nt' else parts)

    def worktree_options(self, worktrees):
        options = []
        for tree in worktrees:
            root = Path(tree['path'])
            gitdir = root / '.git'
            try:
                if gitdir.is_file():
                    value = gitdir.read_text(encoding='utf-8').strip()
                    if value.startswith('gitdir: '):
                        gitdir = (root / value[8:]).resolve()
                reason = self.worktree_reason(worktrees, root=root, gitdir=gitdir)
            except (OSError, ValueError):
                reason = '工作树目录不可用，请在原 Git 工具中修复后刷新。'
            options.append({'id': self.worktree_token(root), 'path': str(root), 'name': root.name,
                            'branch': tree['branch'].removeprefix('refs/heads/'),
                            'detached': not tree['branch'], 'current': root == self.root,
                            'registered': root == self.registered_root,
                            'available': not reason, 'reason': reason})
        return options

    @property
    def config(self):
        # History/detail reads don't need config; writes still load fresh config.
        if self._config is None:
            self._config = self.read_config()
        return self._config

    def git(self, *args, check=True, binary=False, data=None, timeout=30, env=None):
        return _run_git_command(self.exe, ['-c', 'core.fsmonitor=false', *args], cwd=self.root, check=check,
                                text=not binary, input_data=data, timeout=timeout,
                                env_override=env)

    def read_config(self):
        result_map = {}
        for chunk in _run_git_command(self.exe, ['config', '--null', '--list'], cwd=self.root, text=False).stdout.split(b'\0'):
            if b'\n' in chunk:
                key, value = chunk.split(b'\n', 1)
                parts = key.decode('utf-8', 'replace').split('.')
                parts[0], parts[-1] = parts[0].lower(), parts[-1].lower()
                result_map['.'.join(parts)] = value.decode('utf-8', 'replace')
        return result_map

    def head(self):
        head = get_head_info(self.root, self.exe)
        return {'oid': head.oid, 'branch': head.branch, 'unborn': head.unborn, 'detached': head.detached}

    def refs(self):
        result = self.git('for-each-ref', '--format=%(refname)%00%(objectname)', 'refs/heads/', 'refs/remotes/', 'refs/tags/').stdout
        return dict(line.split('\0', 1) for line in result.splitlines() if '\0' in line)

    def worktrees(self):
        # -z avoids quoted/escaped paths (spaces, Unicode and newlines included).
        # Do not inspect another worktree's files/index or inherit its dirtiness.
        records = []
        for record in self.git('worktree', 'list', '--porcelain', '-z', binary=True).stdout.split(b'\0\0'):
            fields = dict(part.partition(b' ')[::2] for part in record.split(b'\0') if part)
            if b'worktree' not in fields:
                continue
            records.append({
                'path': str(Path(os.fsdecode(fields[b'worktree'])).resolve()),
                'branch': os.fsdecode(fields.get(b'branch', b'')),
                'bare': b'bare' in fields, 'prunable': b'prunable' in fields,
            })
        return records

    def worktree_reason(self, worktrees, *, root=None, gitdir=None):
        root = self.root if root is None else root
        gitdir = self.gitdir if gitdir is None else gitdir
        current = [w for w in worktrees if Path(w['path']) == root]
        if len(current) != 1 or current[0]['bare'] or current[0]['prunable']:
            return '当前目录不是有效的已登记工作树，请在原 Git 工具中修复后刷新。'
        dotgit = root / '.git'
        if dotgit.is_symlink() or getattr(dotgit, 'is_junction', lambda: False)():
            return '工作树的 .git 路径被重定向，网页暂仅支持浏览。'
        try:
            if dotgit.is_file():
                value = dotgit.read_text(encoding='utf-8').strip()
                if not value.startswith('gitdir: ') or (root / value[8:]).resolve() != gitdir:
                    return '工作树 Git 目录绑定已改变，请刷新后重试。'
            elif not dotgit.is_dir() or dotgit.resolve() != gitdir:
                return '工作树 Git 目录绑定无效，请在原 Git 工具中处理。'
            if gitdir != self.common:
                # A registered linked worktree needs both directions of Git's
                # own binding, not merely a client-controlled .git pointer.
                if gitdir.parent != self.common / 'worktrees':
                    return '非标准工作树 Git 目录暂仅支持浏览。'
                common = (gitdir / (gitdir / 'commondir').read_text(encoding='utf-8').strip()).resolve()
                backlink = Path((gitdir / 'gitdir').read_text(encoding='utf-8').strip()).resolve()
                if common != self.common or backlink != dotgit.resolve():
                    return '工作树双向绑定不一致，请在原 Git 工具中修复后刷新。'
        except (OSError, ValueError):
            return '无法核对工作树绑定，请在原 Git 工具中修复后刷新。'
        return ''

    def switch_reason(self, branch, worktrees=None):
        owners = [w['path'] for w in (self.worktrees() if worktrees is None else worktrees)
                  if w['branch'] == 'refs/heads/' + branch and Path(w['path']) != self.root]
        return ('该分支正在其他工作树使用：' + '、'.join(owners)
                + '。可进入所在工作树；不会在当前目录重复检出。') if owners else ''

    def check_switch(self, branch):
        reason = self.switch_reason(branch)
        if reason:
            fail('branch_in_use', reason)

    def revalidate(self):
        # Repo was constructed before waiting for the common-dir lock. Resolve
        # registration/location/config again *under* that lock, before writing.
        fresh = Repo(self.home, self.pid, self.worktree_id)
        if (fresh.root, fresh.common, fresh.gitdir, fresh.state) != (self.root, self.common, self.gitdir, self.state):
            fail('state_changed', '注册目录或工作树绑定已改变，请刷新并重新确认。')
        self._config = None

    def capabilities(self, worktrees=None):
        reason = ''
        if self.config.get('core.sparsecheckout', '').lower() in {'true', '1'} or self.config.get('extensions.partialclone'):
            reason = '稀疏或部分克隆仓库暂仅支持浏览。'
        reason = self.worktree_reason(self.worktrees() if worktrees is None else worktrees) or reason
        if self.superproject:
            reason = '子模块仓库暂仅支持浏览。'
        tracked = self.git('ls-files', '--stage', '-z', binary=True).stdout
        if any(x.startswith(b'160000 ') for x in tracked.split(b'\0')):
            reason = '包含子模块的仓库暂仅支持浏览。'
        attributes = self.git('ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', ':(glob)**/.gitattributes', '.gitattributes', binary=True).stdout
        paths = [self.root / p.decode('utf-8') for p in attributes.split(b'\0') if p]
        paths += [self.root / '.gitattributes', self.common / 'info' / 'attributes']
        # An ignored attributes file still affects tracked files; do not execute
        # its filter merely because the attributes file itself is hidden.
        checked_parents = set()
        for record in tracked.split(b'\0'):
            if b'\t' not in record:
                continue
            value = record.split(b'\t', 1)[1].decode('utf-8')
            directory = str(Path(value).parent)
            if directory in checked_parents:
                continue
            parent = self.path(value).parent
            checked_parents.add(directory)
            while parent != self.root:
                paths.append(parent / '.gitattributes')
                parent = parent.parent
        paths = list(dict.fromkeys(paths))
        if self.config.get('core.attributesfile'):
            paths.append(Path(self.config['core.attributesfile']).expanduser())
        for path in paths:
            if path.is_file() and not path.is_symlink() and re.search(r'\bfilter\s*=', path.read_text(encoding='utf-8', errors='replace')):
                reason = '含 LFS 或自定义文件过滤器，网页仅浏览，请在原 Git 工具中写入。'
        try:
            relative = self.state.relative_to(self.root).as_posix()
        except ValueError:
            pass
        else:
            if self.git('check-ignore', '--quiet', '--', relative, check=False).returncode != 0:
                reason = '机器状态目录位于代码库内且未忽略，请在设置中移到仓库外或自行忽略。'
        return {'write': not reason, 'reason': reason, 'read': True}

    def guard(self, action, allow_merge=False):
        self.revalidate()
        cap = self.capabilities()
        if not cap['write']:
            fail('unsupported_repo', cap['reason'])
        if self.ongoing() and not allow_merge:
            fail('operation_in_progress', '仓库有进行中的操作，请先完成或取消。')
        if self.head()['detached'] and action not in {'create_branch', 'switch_branch', 'identity', 'fetch'}:
            fail('unsupported_repo', '当前未在分支上，请先切换或创建本地分支。')
        hook_dir = Path(self.config.get('core.hookspath', str(self.common / 'hooks')))
        if not hook_dir.is_absolute():
            hook_dir = self.root / hook_dir
        hooks = {
            'save': ('pre-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit', 'post-index-change'),
            'create_branch': ('reference-transaction',),
            'switch_branch': ('post-checkout', 'post-index-change'),
            'merge': ('pre-merge-commit', 'prepare-commit-msg', 'commit-msg', 'post-merge', 'post-commit', 'post-index-change'),
            'restore': ('post-checkout', 'pre-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit', 'post-index-change'),
            'push': ('pre-push',), 'pull_apply': ('pre-merge-commit', 'prepare-commit-msg', 'commit-msg', 'post-merge', 'post-commit', 'post-index-change'),
        }.get(action, ())
        if action != 'identity':
            hooks = (*hooks, 'reference-transaction')
        if any((hook_dir / x).is_file() and (os.name == 'nt' or os.access(hook_dir / x, os.X_OK)) for x in hooks):
            fail('unsupported_repo', '此操作有自定义 Git Hook，无法保证不弹窗，请在原 Git 工具中处理。')
        if action in {'save', 'merge', 'restore', 'pull_apply'} and self.config.get('commit.gpgsign', '').lower() in {'true', 'yes', 'on', '1'}:
            fail('unsupported_repo', '仓库要求签名提交，请在已配置签名的 Git 工具中处理。')
        if action in {'merge', 'pull_apply'} and (self.config.get('merge.verifysignatures', '').lower() in {'true', 'yes', 'on', '1'} or any(re.match(r'merge\..*\.driver$', k) for k in self.config)):
            fail('unsupported_repo', '仓库有自定义合并或签名验证策略，请在原 Git 工具中处理。')
        if action in {'merge', 'pull_apply'} and any(k.startswith('branch.') and k.endswith('.mergeoptions') for k in self.config):
            fail('unsupported_repo', '分支有自定义合并选项，请在原 Git 工具中处理。')
        if self.config.get('core.fsmonitor', '').lower() not in {'', 'false', 'true'}:
            fail('unsupported_repo', '仓库有自定义文件监视器，请在原 Git 工具中处理。')

    def files(self):
        data = self.git('-c', 'core.fsmonitor=false', 'status', '--porcelain=v1', '-z', '--untracked-files=all', binary=True).stdout
        parts, i, files = data.split(b'\0'), 0, []
        while i < len(parts) and parts[i]:
            part = parts[i]
            code, path = part[:2].decode('ascii'), part[3:].decode('utf-8')
            old = None
            if 'R' in code or 'C' in code:
                i += 1
                old = parts[i].decode('utf-8')
            files.append(self.file(path, old, code))
            i += 1
        return files

    def file(self, path, old=None, status='M'):
        return {'file_id': digest([self.binding, path, old, status]), 'path': path, 'old_path': old, 'status': status}

    def path(self, value):
        p = self.root / value
        if value.startswith(('/', '\\')) or re.match(r'^[A-Za-z]:', value) or '..' in Path(value).parts:
            fail('invalid_request', '路径无效。', 400)
        parent = p.parent.resolve()
        if parent != self.root and self.root not in parent.parents:
            fail('invalid_request', '文件路径越界。', 400)
        return p

    def file_hash(self, path):
        p = self.path(path)
        try:
            st = p.lstat()
        except FileNotFoundError:
            return 'missing'
        if stat.S_ISLNK(st.st_mode):
            data = os.readlink(p).encode('utf-8')
            return digest([st.st_mode, data.hex()])
        if stat.S_ISREG(st.st_mode):
            h = hashlib.sha256()
            with p.open('rb') as handle:
                for chunk in iter(lambda: handle.read(256 * 1024), b''):
                    h.update(chunk)
            return digest([st.st_mode, st.st_size, h.hexdigest()])
        return digest([st.st_mode, 'not-regular'])

    def snapshot(self, files):
        index = self.gitdir / 'index'
        return {'head': self.head(), 'refs': self.refs(), 'worktrees': self.worktrees(), 'config': digest(self.read_config()), 'index': digest(index.read_bytes()) if index.exists() else None,
                'files': {p: self.file_hash(p) for f in files for p in (f['path'], f.get('old_path')) if p}}

    def lock(self):
        return RepositoryLock(self.common / 'llmwiki-web' / 'locks', self.key, timeout=1)

    def ongoing(self):
        for name, kind in [('MERGE_HEAD', 'merge'), ('rebase-merge', 'rebase'), ('rebase-apply', 'rebase'), ('CHERRY_PICK_HEAD', 'cherry-pick'), ('REVERT_HEAD', 'revert'), ('sequencer', 'sequencer')]:
            if (self.gitdir / name).exists():
                owned = self.read_state('merge') if kind == 'merge' else None
                if owned and self.owned(owned):
                    return {'kind': kind, 'external': False, 'operation_id': owned['operation_id']}
                return {'kind': kind, 'external': True}
        return None

    def owned(self, state):
        merge = self.gitdir / 'MERGE_HEAD'
        return (state.get('project_id') == self.pid and state.get('root') == str(self.root)
                and merge.is_file() and merge.read_text().strip() == state.get('source_oid')
                and self.head()['oid'] == state.get('head_before'))

    def read_state(self, name):
        try:
            return json.loads((self.state / (name + '.json')).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def write_state(self, name, value):
        _write_json(self.state / (name + '.json'), value)

    def oid(self, value):
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{' + str(self.oid_length) + '}', value):
            fail('invalid_request', '需要完整有效的版本编号。', 400)
        head = self.head()['oid']
        tips = list(set(self.refs().values()) | ({head} if head else set()))
        if not tips or value not in self.git('rev-list', *tips).stdout.splitlines():
            fail('invalid_request', '版本不属于当前可浏览历史。', 400)
        return value

    def branch(self, value, exists=False):
        text(value)
        if value.startswith('-') or self.git('check-ref-format', 'refs/heads/' + value, check=False).returncode:
            fail('invalid_request', '分支名称无效。', 400)
        if exists and 'refs/heads/' + value not in self.refs():
            fail('state_changed', '分支不存在或已改变，请刷新。')
        return value

    def identity(self):
        if not self.config.get('user.name', '').strip() or not self.config.get('user.email', '').strip():
            fail('identity_required', '请先填写本仓库的提交姓名和邮箱。', 422)

    def clean(self):
        if self.files():
            fail('dirty_worktree', '还有未保存文件，请先保存；不会自动暂存或丢弃改动。')

    def target_safe(self, oid):
        # Switching/restoring must not introduce a filter or submodule that was
        # absent in the current tree. Inspect blobs, never check them out to test.
        for row in self.git('ls-tree', '-r', '-z', oid, binary=True).stdout.split(b'\0'):
            if not row:
                continue
            info, raw_path = row.split(b'\t', 1)
            mode, _, blob = info.split()
            if mode == b'160000':
                fail('unsupported_repo', '目标版本含子模块，请在原 Git 工具中处理。')
            if raw_path.rsplit(b'/', 1)[-1] == b'.gitattributes':
                content = self.git('cat-file', 'blob', blob.decode('ascii'), binary=True).stdout
                if re.search(rb'\bfilter\s*=', content):
                    fail('unsupported_repo', '目标版本含 LFS 或自定义过滤器，请在原 Git 工具中处理。')
        tracked = set(self.git('ls-files', '-z', binary=True).stdout.split(b'\0'))
        for raw in self.git('ls-tree', '-r', '--name-only', '-z', oid, binary=True).stdout.split(b'\0'):
            if not raw or raw in tracked:
                continue
            p = self.path(raw.decode('utf-8'))
            if p.exists() or p.is_symlink():
                fail('dirty_worktree', '目标版本会覆盖未跟踪或被忽略的文件，请先在原工具中整理。')
            for parent in p.parents:
                if parent == self.root:
                    break
                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                    fail('dirty_worktree', '目标路径被其他文件占用。')

    def remote(self, remote_id):
        for name in self.git('remote').stdout.splitlines():
            if digest([self.binding, name]) == remote_id:
                return name, self.git('remote', 'get-url', name).stdout.strip()
        fail('no_remote', '请选择已配置的远端。', 422)

    def remote_env(self, name, push=False):
        urls = self.git('remote', 'get-url', '--all', *(['--push'] if push else []), name).stdout.splitlines()
        url = urls[0] if urls else ''
        if len(urls) != 1 or '::' in url or (re.match(r'^[a-zA-Z][\w+.-]*://', url) and urlsplit(url).scheme not in {'http', 'https', 'ssh', 'git', 'file'}):
            fail('unsupported_repo', '此远端使用多目标或自定义传输，请在原工具中处理。')
        if any(k in self.config for k in ('core.sshcommand', 'core.gitproxy')) or os.environ.get('GIT_SSH_COMMAND') or os.environ.get('GIT_SSH'):
            fail('unsupported_repo', '自定义 SSH 或代理命令无法保证不弹窗，请在原工具中处理。')
        for k, v in self.config.items():
            if k.endswith('.helper') and k.startswith('credential') and v and v.split()[0] not in {'manager', 'manager-core', 'store', 'cache', 'wincred'}:
                fail('unsupported_repo', '自定义认证程序无法保证不弹窗，请在原工具中处理。')
        if self.config.get(f'remote.{name}.mirror', '').lower() in {'true', '1', 'yes'}:
            fail('unsupported_repo', '镜像远端暂不支持网页上传。')
        return {'GIT_SSH_COMMAND': 'ssh -o BatchMode=yes -o StrictHostKeyChecking=yes', 'GIT_SSH_VARIANT': 'ssh'}


def status(repo):
    worktrees = repo.worktrees()
    capabilities = repo.capabilities(worktrees)
    options = repo.worktree_options(worktrees)
    head, refs = repo.head(), repo.refs()
    remotes = []
    for name in repo.git('remote').stdout.splitlines():
        remotes.append({'remote_id': digest([repo.binding, name]), 'name': name,
                        'url': scrub_url(repo.git('remote', 'get-url', name).stdout.strip()),
                        'branches': [r[len('refs/remotes/' + name + '/'): ] for r in refs if r.startswith('refs/remotes/' + name + '/') and not r.endswith('/HEAD')]})
    upstream = None
    if head['branch']:
        remote = repo.config.get('branch.' + head['branch'] + '.remote')
        target = repo.config.get('branch.' + head['branch'] + '.merge', '')
        if remote and target.startswith('refs/heads/'):
            tracked = f'refs/remotes/{remote}/{target[11:]}'
            upstream = {'remote_id': digest([repo.binding, remote]), 'branch': target[11:], 'ahead': None, 'behind': None}
            if tracked in refs and head['oid']:
                counts = repo.git('rev-list', '--left-right', '--count', f'{head["oid"]}...{refs[tracked]}').stdout.split()
                upstream.update(ahead=int(counts[0]), behind=int(counts[1]))
    cached = repo.read_state('fetch') or {}
    branches = []
    for ref, oid in refs.items():
        if ref.startswith('refs/heads/'):
            name = ref[11:]
            reason = repo.switch_reason(name, worktrees)
            owner = next((w for w in options if w['branch'] == name and not w['current']), None)
            branches.append({'name': name, 'oid': oid, 'current': name == head['branch'],
                             'switchable': not reason, 'switch_reason': reason,
                             'worktree_id': owner['id'] if owner and owner['available'] else None,
                             'worktree_path': owner['path'] if owner else None})
    return {'ok': True, 'capabilities': capabilities, 'head': head,
            'worktree': {'id': repo.worktree_token(repo.root), 'path': str(repo.root),
                         'linked': repo.gitdir != repo.common, 'count': len(worktrees)},
            'worktrees': options,
            'branches': branches,
            'remotes': remotes, 'upstream': upstream, 'files': repo.files() if capabilities['write'] else [], 'ongoing': repo.ongoing(),
            'last_fetch_at': cached.get('last_fetch_at')}


def tree_files(repo, base, oid):
    if base:
        args = ['diff', '--no-ext-diff', '--no-textconv', '--name-status', '-z', '--find-renames', base, oid, '--']
    else:
        args = ['diff-tree', '--no-ext-diff', '--no-textconv', '--root', '--no-commit-id', '-r', '--name-status', '-z', '--find-renames', oid, '--']
    parts = repo.git(*args, binary=True).stdout.split(b'\0')
    result, i = [], 0
    while i < len(parts) and parts[i]:
        code = parts[i].decode('ascii')
        i += 1
        first = parts[i].decode('utf-8')
        i += 1
        old = None
        if code[0] in 'RC':
            old, first = first, parts[i].decode('utf-8')
            i += 1
        result.append(repo.file(first, old, code))
    return result


def commit_file_stats(repo, base, oid):
    """Read exact line counts without filters; -z preserves rename and unusual paths."""
    args = (['diff', base, oid] if base else
            ['diff-tree', '--root', '--no-commit-id', '-r', oid])
    raw = repo.git(*args, '--no-ext-diff', '--no-textconv', '--numstat', '-z',
                   '--find-renames', '--', binary=True).stdout
    parts, result, i = raw.split(b'\0'), {}, 0
    while i < len(parts) and parts[i]:
        added, removed, path = parts[i].split(b'\t', 2)
        i += 1
        if not path:  # With -z, a rename is followed by old and new paths.
            path = parts[i + 1]
            i += 2
        binary = added == b'-' or removed == b'-'
        result[path.decode('utf-8')] = {
            'additions': None if binary else int(added),
            'deletions': None if binary else int(removed), 'binary': binary,
        }
    return result


def commit(repo, oid, *, include_stats=True):
    oid = repo.oid(oid)
    raw = repo.git('show', '-s', '--format=%H%x00%P%x00%an%x00%ae%x00%aI%x00%s%x00%B', oid).stdout.split('\0', 6)
    parents = raw[1].split()
    base = parents[0] if parents else None
    files = tree_files(repo, base, oid)
    if include_stats:
        counts = commit_file_stats(repo, base, oid)
        for file in files:
            file.update(counts.get(file['path'], {}))
    return {'ok': True, 'oid': oid, 'repository_path': str(repo.root), 'parents': parents, 'author_name': raw[2], 'author_email': raw[3],
            'committed_at': raw[4], 'subject': raw[5], 'message': raw[6], 'base_oid': parents[0] if parents else None,
            'files': files}


def get_preview(repo, preview_id, consume=False):
    if not isinstance(preview_id, str):
        fail('invalid_request', '缺少预览编号。', 400)
    with PREVIEW_LOCK:
        item = PREVIEWS.get(preview_id)
        if not item or item['binding'] != repo.binding or item['deadline'] < time.monotonic():
            fail('preview_expired', '确认已失效，请重新预览。')
        if consume:
            del PREVIEWS[preview_id]
        return item


def compare(repo, item, files):
    snap = repo.snapshot(files)
    old = item['snapshot']
    if any(snap[k] != old[k] for k in ('head', 'refs', 'index', 'config', 'worktrees')) or any(old['files'].get(k) != v for k, v in snap['files'].items()):
        fail('state_changed', '确认后仓库或所选文件已改变，请重新预览；输入不会被丢弃。')


def preview(repo, payload):
    fields(payload, ('action', 'params'))
    action, params = payload['action'], payload['params']
    if not isinstance(action, str) or action not in ACTIONS:
        fail('invalid_request', '不支持此操作。', 400)
    fields(params, ACTIONS[action])
    repo.guard(action)
    head = repo.head()
    files = repo.files() if action == 'save' else []
    start = repo.snapshot(files)
    initial = {k: start[k] for k in ('head', 'refs', 'index', 'config', 'worktrees')}
    summary, target = '', None
    if action == 'save':
        if not files:
            fail('invalid_request', '没有需要保存的文件。', 400)
        summary = '保存完整选中文件（含未暂存部分），本次仅保存在本地。'
    elif action == 'create_branch':
        target = repo.oid(params['target_oid'])
        summary = '从该版本创建本地分支，不切换当前工作区。'
    elif action in {'switch_branch', 'merge', 'restore', 'pull_apply'}:
        repo.clean()
        if action in {'switch_branch', 'merge'}:
            name = repo.branch(params['branch' if action == 'switch_branch' else 'source_branch'], exists=True)
            if action == 'switch_branch':
                repo.check_switch(name)
            target = repo.refs()['refs/heads/' + name]
        elif action == 'restore':
            target = repo.oid(params['target_oid'])
        else:
            name, _ = repo.remote(params['remote_id'])
            branch = repo.branch(params['branch'])
            cached = repo.read_state('fetch') or {}
            target = cached.get('fetched_oid')
            if not target or any(cached.get(k) != params[k] for k in ('remote_id', 'branch', 'fetched_oid')) or repo.refs().get(f'refs/remotes/{name}/{branch}') != target:
                fail('state_changed', '远端检查结果已改变，请重新检查并确认。')
        repo.target_safe(target)
        if action != 'switch_branch':
            repo.identity()
            if not head['oid']:
                fail('unsupported_repo', '请先保存首个本地版本。')
        files = tree_files(repo, head['oid'], target)
        start = repo.snapshot(files)
        summary = {'switch_branch': '切换本地分支，不自动暂存或丢弃文件。', 'merge': '将来源分支合入当前分支；发生冲突时逐文件处理。',
                   'restore': '恢复整个受跟踪文件树并新建版本，保留此前历史。', 'pull_apply': '应用刚检查的固定远端版本；分叉时正常合并。'}[action]
    elif action == 'push':
        name, _ = repo.remote(params['remote_id'])
        repo.remote_env(name, push=True)
        repo.branch(params['target_branch'])
        if not head['oid']:
            fail('invalid_request', '还没有可上传的本地版本。', 400)
        cached_oid = repo.refs().get(f'refs/remotes/{name}/{params["target_branch"]}')
        outgoing = repo.git('log', '--format=%H%x00%s', head['oid'], *(['^' + cached_oid] if cached_oid else [])).stdout
        summary = {
            'message': '仅上传当前分支已保存版本，不含未提交文件；远端最终检查是否可快进。',
            'source_branch': head['branch'], 'source_oid': head['oid'],
            'remote_name': name, 'remote_url': scrub_url(repo.git('remote', 'get-url', '--push', name).stdout.strip()),
            'target_branch': params['target_branch'], 'cached': True,
            'creates_remote_branch': False if cached_oid else '缓存中未见；若远端不存在，将新建目标分支',
            'outgoing_commits': [{'oid': row.split('\0', 1)[0], 'subject': row.split('\0', 1)[1]} for row in outgoing.splitlines() if '\0' in row],
            'last_fetch_at': (repo.read_state('fetch') or {}).get('last_fetch_at'),
        }
    if repo.snapshot(files) != start or any(start[k] != initial[k] for k in initial):
        fail('state_changed', '读取期间仓库发生变化，请重新预览。')
    pid = uuid.uuid4().hex
    item = {'binding': repo.binding, 'action': action, 'params': params, 'snapshot': start,
            'files': files, 'target': target, 'deadline': time.monotonic() + 300}
    with PREVIEW_LOCK:
        for key in [k for k, v in PREVIEWS.items() if v['deadline'] < time.monotonic()]:
            del PREVIEWS[key]
        PREVIEWS[pid] = item
    return {'ok': True, 'preview_id': pid, 'expires_at': datetime.fromtimestamp(time.time() + 300, timezone.utc).isoformat(),
            'summary': summary, 'files': files, 'can_execute': True, 'reason': None}


def diff(repo, query):
    kind = query.get('kind')
    if kind == 'commit':
        meta = commit(repo, query.get('oid'), include_stats=False)
        files = meta['files']
    elif kind in {'worktree', 'restore'}:
        item = get_preview(repo, query.get('preview_id'))
        if (kind == 'worktree' and item['action'] != 'save') or (kind == 'restore' and item['action'] != 'restore'):
            fail('invalid_request', '差异类型与预览不符。', 400)
        files = item['files']
    else:
        fail('invalid_request', '差异类型无效。', 400)
    file = next((f for f in files if f['file_id'] == query.get('file_id')), None)
    if not file:
        fail('invalid_request', '文件不属于此预览。', 400)
    paths = [p for p in (file.get('old_path'), file['path']) if p]
    if kind == 'worktree':
        compare(repo, item, [file])
        p = repo.path(file['path'])
        if p.is_symlink():
            return {'ok': True, 'text': '符号链接：' + os.readlink(p), 'binary': False, 'supported': False, 'truncated': False}
        if file['status'] == '??' or not item['snapshot']['head']['oid']:
            raw = p.read_bytes() if p.is_file() else b''
        else:
            raw = repo.git('diff', '--no-ext-diff', '--no-textconv', '--no-color', item['snapshot']['head']['oid'], '--', *paths, binary=True, env={'GIT_LITERAL_PATHSPECS': '1'}).stdout
    else:
        if kind == 'restore':
            compare(repo, item, [file])
            args = ['diff', item['snapshot']['head']['oid'], item['target']]
        elif meta['base_oid']:
            args = ['diff', meta['base_oid'], meta['oid']]
        else:
            args = ['show', '--format=', meta['oid']]
        raw = repo.git(*args, '--no-ext-diff', '--no-textconv', '--no-color', '--', *paths, binary=True, env={'GIT_LITERAL_PATHSPECS': '1'}).stdout
    binary = b'\0' in raw or b'Binary files ' in raw
    try:
        content = raw.decode('utf-8')
    except UnicodeDecodeError:
        content, binary = '', True
    if binary:
        return {'ok': True, 'text': '二进制或非 UTF-8 文件，不能显示文本差异。', 'binary': True, 'supported': False, 'truncated': False}
    truncated = len(raw) > LIMIT or content.count('\n') > 5000
    content = '\n'.join(raw[:LIMIT].decode('utf-8', 'ignore').split('\n')[:5000])
    return {'ok': True, 'text': content, 'binary': False, 'supported': True, 'truncated': truncated}


def receipt(repo, action, before, outcome='done', message='操作已完成。', **extra):
    return {'ok': True, 'action': action, 'outcome': outcome, 'head_before': before,
            'head_after': repo.head()['oid'], 'message': message, **extra}


def execute(repo, payload):
    fields(payload, ('preview_id',), ('file_ids', 'message', 'name'))
    item = get_preview(repo, payload['preview_id'], consume=True)
    action, params = item['action'], item['params']
    if (set(payload) - {'preview_id'}) - ({'file_ids', 'message'} if action == 'save' else {'name'} if action == 'create_branch' else set()):
        fail('invalid_request', '此操作不接受额外字段。', 400)
    with repo.lock():
        repo.guard(action)
        files = item['files']
        if action == 'save':
            ids = payload.get('file_ids')
            if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids) or set(ids) - {f['file_id'] for f in files}:
                fail('invalid_request', '请勾选此预览中的文件。', 400)
            files = [f for f in files if f['file_id'] in ids]
        if action == 'switch_branch':
            repo.check_switch(params['branch'])
        compare(repo, item, files)
        before = item['snapshot']['head']['oid']
        if action == 'save':
            repo.identity()
            message = text(payload.get('message'), 4000).strip()
            paths = list(dict.fromkeys(p for f in files for p in (f.get('old_path'), f['path']) if p))
            try:
                oid = commit_selected_files(repo.root, repo.exe, paths, message)
            except (subprocess.SubprocessError, GitError):
                return receipt(repo, action, before, 'partial', '版本尚未保存；所选文件可能已暂存，请查看后重新保存，不会回滚其他修改。')
            return receipt(repo, action, before, commit_oid=oid, message='已保存到本地，尚未上传。')
        if action == 'create_branch':
            name = repo.branch(payload.get('name'))
            create_branch(repo.root, repo.exe, name, item['target'])
            return receipt(repo, action, before, message='本地分支已创建，当前分支未切换。')
        if action == 'switch_branch':
            repo.clean()
            repo.target_safe(item['target'])
            repo.check_switch(params['branch'])
            repo.git('switch', '--no-guess', params['branch'])
            if repo.head()['branch'] != params['branch']:
                fail('state_changed', '分支结果已变化，请刷新。')
            return receipt(repo, action, before)
        if action in {'merge', 'pull_apply'}:
            repo.clean()
            repo.identity()
            repo.target_safe(item['target'])
            if action == 'pull_apply':
                cached = repo.read_state('fetch') or {}
                if any(cached.get(k) != params[k] for k in ('remote_id', 'branch', 'fetched_oid')):
                    fail('state_changed', '检查结果已变化，请重新检查。')
            return start_merge(repo, item)
        if action == 'restore':
            repo.clean()
            repo.identity()
            repo.target_safe(item['target'])
            return receipt(repo, action, before, **restore_tree_as_commit(repo.root, repo.exe, item['target']))
        if action == 'push':
            name, _ = repo.remote(params['remote_id'])
            env = repo.remote_env(name, push=True)
            try:
                repo.git('-c', 'push.followTags=false', 'push', '--porcelain', '--no-follow-tags', '--recurse-submodules=no', name,
                         before + ':refs/heads/' + params['target_branch'], timeout=90, env=env)
            except subprocess.TimeoutExpired:
                return receipt(repo, action, before, 'partial', '上传回执不明确，可能已送达。请点击拉取检查，不会自动重试。', code='result_uncertain')
            except subprocess.CalledProcessError as exc:
                raw = exc.stderr or ''
                if any(x in raw.lower() for x in ('unexpected disconnect', 'remote end hung up', 'connection reset')):
                    return receipt(repo, action, before, 'partial', '上传结果暂不能确认，请检查远端，不会自动重试。', code='result_uncertain')
                git_failure(exc, network=True)
            branch = repo.head()['branch']
            if not repo.config.get('branch.' + branch + '.remote'):
                repo.git('config', '--local', 'branch.' + branch + '.remote', name)
                repo.git('config', '--local', 'branch.' + branch + '.merge', 'refs/heads/' + params['target_branch'])
            return receipt(repo, action, before, message='指定分支已上传；未提交文件没有上传。')
    fail('invalid_request', '未知操作。', 400)


def fetch(repo, payload):
    fields(payload, ('remote_id', 'branch'))
    with repo.lock():
        repo.guard('fetch')
        name, _ = repo.remote(payload['remote_id'])
        branch = repo.branch(payload['branch'])
        env = repo.remote_env(name)
        ref = f'refs/remotes/{name}/{branch}'
        repo.git('-c', 'fetch.prune=false', '-c', 'fetch.pruneTags=false', '-c', f'remote.{name}.prune=false',
                 '-c', f'remote.{name}.pruneTags=false', 'fetch', '--no-tags', '--no-prune', '--no-write-fetch-head',
                 '--no-auto-maintenance', '--recurse-submodules=no', '--refmap=', name,
                 f'+refs/heads/{branch}:{ref}', timeout=90, env=env)
        oid = repo.git('rev-parse', '--verify', ref).stdout.strip()
        head = repo.head()['oid']
        counts = repo.git('rev-list', '--left-right', '--count', f'{head}...{oid}').stdout.split() if head else ['0', '0']
        ahead, behind = map(int, counts)
        result = {'ok': True, 'remote_id': payload['remote_id'], 'branch': branch, 'fetched_oid': oid,
                  'ahead': ahead, 'behind': behind, 'relation': 'diverged' if ahead and behind else 'behind' if behind else 'ahead' if ahead else 'equal',
                  'last_fetch_at': now()}
        repo.write_state('fetch', result)
        return result


def merge_view(repo):
    state = repo.read_state('merge')
    if not state or not repo.owned(state):
        return {'ok': True, 'external': bool(repo.ongoing()), 'operation_id': None, 'files': [],
                'message': '请在原 Git 工具完成进行中的操作。' if repo.ongoing() else '没有进行中的合并。'}
    files = []
    for f in state['files']:
        entry = dict(f)
        p = repo.path(f['path'])
        entry['revision'] = digest([repo.file_hash(f['path']), state['revision'], f['resolved']])
        if f['supported']:
            try:
                if p.is_symlink() or not p.is_file():
                    raise OSError('conflict path is no longer a regular file')
                content = p.read_bytes()
                entry['content'] = content.decode('utf-8') if len(content) <= LIMIT and b'\0' not in content else ''
            except (OSError, UnicodeDecodeError):
                entry['content'] = ''
        files.append(entry)
    return {'ok': True, 'operation_id': state['operation_id'], 'expected_revision': state['revision'],
            'source_branch': state['source_branch'], 'target_branch': state['target_branch'],
            'external': False, 'files': files}


def tracked_snapshot(repo):
    paths = (repo.git('ls-files', '-z', binary=True).stdout + repo.git('ls-tree', '-r', '--name-only', '-z', 'HEAD', binary=True).stdout).split(b'\0')
    files = [repo.file(p.decode('utf-8')) for p in set(paths) if p]
    return repo.snapshot(files)


def start_merge(repo, item):
    head, target, action = repo.head(), item['target'], item['action']
    before = head['oid']
    source = item['params'].get('source_branch') or item['params'].get('branch')
    result = merge_fixed_commit(repo.root, repo.exe, target, f'合并 {source} 到 {head["branch"]}')
    if result is None:
        return receipt(repo, action, before, 'no_change', '当前分支已包含该版本。')
    if result.returncode == 0:
        return receipt(repo, action, before, commit_oid=repo.head()['oid'], message='分支已合并。')
    if not (repo.gitdir / 'MERGE_HEAD').exists():
        fail('git_failed', '合并未完成，请检查仓库状态；未自动丢弃文件。', 500)
    unmerged = repo.git('ls-files', '--unmerged', '-z', binary=True).stdout
    stages = {}
    for line in unmerged.split(b'\0'):
        if not line:
            continue
        info, path = line.split(b'\t', 1)
        mode, oid, stage = info.decode('ascii').split()
        stages.setdefault(path.decode('utf-8'), {})[stage] = (mode, oid)
    by_path = {f['path']: f for f in repo.files()}
    files = []
    for path, entries in stages.items():
        file = by_path[path]
        file.update(supported=False, resolved=False, current='', incoming='', base='')
        if file['status'] == 'UU' and set(entries) == {'1', '2', '3'} and all(mode in {'100644', '100755'} for mode, _ in entries.values()):
            try:
                for stage, label in [('1', 'base'), ('2', 'current'), ('3', 'incoming')]:
                    if int(repo.git('cat-file', '-s', entries[stage][1]).stdout) > LIMIT:
                        raise ValueError('large')
                    raw = repo.git('cat-file', 'blob', entries[stage][1], binary=True).stdout
                    if b'\0' in raw:
                        raise ValueError('binary')
                    file[label] = raw.decode('utf-8')
                file['supported'] = True
            except (ValueError, UnicodeDecodeError):
                file.update(current='', incoming='', base='')
        files.append(file)
    state = {'project_id': repo.pid, 'root': str(repo.root), 'operation_id': uuid.uuid4().hex,
             'head_before': before, 'source_oid': target, 'source_branch': source, 'target_branch': head['branch'],
             'files': files, 'revision': uuid.uuid4().hex, 'snapshot': tracked_snapshot(repo)}
    repo.write_state('merge', state)
    return receipt(repo, action, before, 'conflicts', '合并需要逐文件确认；不支持的冲突可取消后在原工具处理。', operation_id=state['operation_id'])


def marker_check(repo, path, content):
    attrs = repo.git('check-attr', '-z', 'conflict-marker-size', '--', path, binary=True).stdout.split(b'\0')
    try:
        n = int(attrs[2])
        if n <= 0 or n > 10000:
            n = 7
    except (ValueError, IndexError):
        n = 7
    pattern = r'^(?:' + '|'.join(re.escape(c) + '{' + str(n) + '}' for c in '<=>|') + r')(?=\s|$)'
    if re.search(pattern, content, re.MULTILINE):
        fail('unsupported_conflict', '最终内容仍有 Git 冲突标记，请先处理。', 422)


def merge_action(repo, action, payload):
    required = ('operation_id', 'expected_revision')
    if action == 'resolve':
        fields(payload, (*required, 'file_id', 'resolution'), ('content',))
    else:
        fields(payload, required)
    with repo.lock():
        repo.guard('merge', allow_merge=True)
        state = repo.read_state('merge')
        if not state or payload['operation_id'] != state['operation_id'] or not repo.owned(state):
            fail('operation_in_progress', '此合并不属于当前网页操作，请在原工具处理。')
        if tracked_snapshot(repo) != state['snapshot']:
            fail('state_changed', '合并期间有外部索引或文件修改，请先在原工具检查；不会覆盖这些修改。')
        if action == 'resolve':
            file = next((f for f in state['files'] if f['file_id'] == payload['file_id']), None)
            if not file or not file['supported']:
                fail('unsupported_conflict', '此冲突不支持网页编辑，请取消后在原工具处理。', 422)
            revision = digest([repo.file_hash(file['path']), state['revision'], file['resolved']])
            if payload['expected_revision'] != revision:
                fail('state_changed', '此文件已改变，请保留输入并刷新后确认。')
            resolution = payload['resolution']
            content = file['current'] if resolution == 'current' else file['incoming'] if resolution == 'incoming' else payload.get('content') if resolution == 'manual' else None
            if not isinstance(content, str) or '\0' in content or len(content.encode('utf-8')) > LIMIT:
                fail('invalid_request', '最终内容必须是至多 1MiB 的 UTF-8 文本。', 400)
            marker_check(repo, file['path'], content)
            p = repo.path(file['path'])
            if p.is_symlink():
                fail('unsupported_conflict', '符号链接不能作为文本编辑。', 422)
            p.write_bytes(content.encode('utf-8'))
            repo.git('add', '--', file['path'], env={'GIT_LITERAL_PATHSPECS': '1'})
            file['resolved'] = True
            state['revision'] = uuid.uuid4().hex
            state['snapshot'] = tracked_snapshot(repo)
            repo.write_state('merge', state)
            return merge_view(repo)
        if payload['expected_revision'] != state['revision']:
            fail('state_changed', '合并状态已改变，请刷新后再确认。')
        if action == 'complete':
            repo.identity()
            if repo.git('ls-files', '--unmerged', '-z', binary=True).stdout or any(not f['resolved'] for f in state['files']):
                fail('unsupported_conflict', '请先解决所有冲突文件。', 422)
            for f in state['files']:
                marker_check(repo, f['path'], repo.path(f['path']).read_bytes().decode('utf-8'))
            repo.git('commit', '-m', f'合并 {state["source_branch"]} 到 {state["target_branch"]}')
            return receipt(repo, 'merge', state['head_before'], message='冲突已解决，合并版本已保存。', commit_oid=repo.head()['oid'])
        if action == 'abort':
            repo.git('merge', '--abort')
            if repo.head()['oid'] != state['head_before'] or repo.git('diff', '--name-only', 'HEAD').stdout or repo.git('diff', '--cached', '--name-only').stdout:
                return receipt(repo, 'merge', state['head_before'], 'partial', '取消后的仓库状态需检查；没有强制回退。')
            return receipt(repo, 'merge', state['head_before'], message='已取消本次合并，额外未跟踪文件保留。')
    fail('invalid_request', '未知合并操作。', 400)


def dispatch(home, project_id, method, endpoint, data=None, *, worktree_id=None):
    """Return (JSON, HTTP status), never raw stderr or a client-supplied path."""
    try:
        data = {} if data is None else data
        if method == 'GET':
            data = dict(data)
            worktree_id = data.pop('worktree', worktree_id)
        repo = Repo(home, project_id, worktree_id)
        if method == 'GET':
            if endpoint == 'status':
                result = status(repo)
            elif endpoint == 'graph':
                page = int(data.get('page', 0))
                if page < 0 or int(data.get('page_size', 100)) != 100:
                    fail('invalid_request', '页码无效，每页固定 100 个版本。', 400)
                if page and not data.get('snapshot_id'):
                    fail('state_changed', '请携带同一历史快照编号。')
                result = build_graph(repo.root, repo.exe, page=page, page_size=100, snapshot_id=data.get('snapshot_id'))
            elif endpoint.startswith('commit/'):
                result = commit(repo, endpoint[7:])
            elif endpoint == 'diff':
                result = diff(repo, data)
            elif endpoint == 'merge':
                result = merge_view(repo)
            else:
                fail('invalid_request', '接口不存在。', 400)
        elif method == 'POST':
            if endpoint == 'preview':
                result = preview(repo, data)
            elif endpoint == 'execute':
                result = execute(repo, data)
            elif endpoint == 'fetch':
                result = fetch(repo, data)
            elif endpoint.startswith('merge/') and endpoint[6:] in {'resolve', 'complete', 'abort'}:
                result = merge_action(repo, endpoint[6:], data)
            elif endpoint == 'identity':
                fields(data, ('name', 'email', 'scope'))
                name, email = text(data['name'], 200), text(data['email'], 254)
                if data['scope'] != 'local' or any(c in name + email for c in '\r\n<>') or '@' not in email:
                    fail('invalid_request', '请填写有效姓名和邮箱；只允许本仓库配置。', 400)
                with repo.lock():
                    repo.guard('identity')
                    set_author(repo.root, repo.exe, name, email, scope='local')
                result = {'ok': True, 'message': '已保存本仓库身份，请重新确认操作。'}
            else:
                fail('invalid_request', '接口不存在。', 400)
        else:
            fail('invalid_request', '不支持此请求方法。', 400)
        return result, 200
    except WebGitError as exc:
        return {'ok': False, 'code': exc.code, 'message': str(exc)}, exc.status
    except GraphSnapshotExpiredError:
        return {'ok': False, 'code': 'state_changed', 'message': '分支历史已变化，请重新加载第一页。'}, 409
    except GitLockError:
        return {'ok': False, 'code': 'repo_busy', 'message': '仓库正忙，请稍后重试。'}, 409
    except subprocess.TimeoutExpired:
        return {'ok': False, 'code': 'git_timeout', 'message': 'Git 操作超时，请检查当前状态；没有自动重试。'}, 504
    except subprocess.CalledProcessError as exc:
        try:
            git_failure(exc, endpoint in {'fetch', 'execute'})
        except WebGitError as error:
            return {'ok': False, 'code': error.code, 'message': str(error)}, error.status
    except (GitError, LLMWikiError, OSError, ValueError, TypeError, KeyError):
        return {'ok': False, 'code': 'git_failed', 'message': '仓库不可用或请求无效，请刷新检查；不会自动修改或回退。'}, 400
