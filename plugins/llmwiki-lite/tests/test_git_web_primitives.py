"""Real temporary-repository coverage for the Git web command/graph primitives."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import git_graph
from git_graph import GraphSnapshotExpiredError, assign_lanes, build_graph, CommitNode
from git_service import GitRepositoryError, _run_git_command, detect_git_executable, get_status


class GitPrimitiveCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='git_web_primitives_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Never consume the user's global hooks, identities or signing settings.
        env = patch.dict(os.environ, {
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
        })
        env.start()
        self.addCleanup(env.stop)
        self.git = detect_git_executable()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.run_git('init', '-b', 'main')
        self.run_git('config', 'user.name', 'Fixture Author')
        self.run_git('config', 'user.email', 'fixture@example.invalid')

    def run_git(self, *args, **kwargs):
        return _run_git_command(self.git, list(args), cwd=self.repo, **kwargs)

    def commit_file(self, name='tracked.txt', data='fixture\n'):
        (self.repo / name).write_text(data, encoding='utf-8')
        self.run_git('add', '--', name)
        self.run_git('commit', '-m', 'fixture commit')
        return self.run_git('rev-parse', 'HEAD').stdout.strip()


class TestGitCommandPrimitives(GitPrimitiveCase):
    def test_command_arguments_and_environment(self):
        payload = '中文 name\0-leading\0'
        # This unit assertion complements real Git tests; it is not desktop QA.
        with patch('git_service.subprocess.run') as run, \
             patch('git_service.platform.system', return_value='Windows'), \
             patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True):
            _run_git_command(
                self.git, ['status', '--porcelain=v1', '-z'], self.repo,
                env_override={'GIT_AUTHOR_NAME': 'fixture', 'GIT_OPTIONAL_LOCKS': '1',
                              'GIT_SSH_COMMAND': 'custom-ssh --policy'},
                input_data=payload
            )
        args, kwargs = run.call_args
        self.assertEqual(args[0], [self.git, '-c', 'diff.autoRefreshIndex=false', 'status', '--porcelain=v1', '-z'])
        self.assertIs(kwargs['shell'], False)
        self.assertIs(kwargs['text'], True)
        self.assertEqual(kwargs['timeout'], 30.0)
        self.assertEqual(kwargs['creationflags'], 0x08000000)
        self.assertEqual(kwargs['encoding'], 'utf-8')
        self.assertEqual(kwargs['errors'], 'surrogateescape')
        self.assertEqual(kwargs['input'], payload)
        self.assertNotIn('stdin', kwargs)
        for key, value in {
            'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0',
            'GCM_INTERACTIVE': 'never', 'GCM_GUI_PROMPT': '0',
            'GIT_ASKPASS': 'false', 'SSH_ASKPASS': 'false',
            'GIT_PAGER': 'cat', 'GIT_EDITOR': 'true',
            'GIT_SEQUENCE_EDITOR': 'true', 'GIT_MERGE_AUTOEDIT': 'no',
            'GIT_SSH_COMMAND': 'custom-ssh --policy', 'GIT_AUTHOR_NAME': 'fixture',
        }.items():
            self.assertEqual(kwargs['env'][key], value)
        self.assertEqual(kwargs['env'].get('GIT_CONFIG_COUNT'), os.environ.get('GIT_CONFIG_COUNT'))
        with patch('git_service.subprocess.run') as run:
            _run_git_command(self.git, ['cat-file', '--batch'], self.repo,
                             90.0, False, None, b'HEAD\0', False)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs['timeout'], 90.0)
        self.assertIs(kwargs['check'], False)
        self.assertIs(kwargs['text'], False)
        self.assertEqual(kwargs['input'], b'HEAD\0')
        self.assertNotIn('encoding', kwargs)
        self.assertNotIn('errors', kwargs)
        with patch('git_service.subprocess.run') as run:
            _run_git_command(self.git, ['status'])
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_real_nul_stdin_and_binary_roundtrip(self):
        names = ['中文 空格.txt', '-leading.txt']
        if os.name != 'nt':
            names.append('line\nbreak.txt')
        for name in names:
            (self.repo / name).write_text('test\n', encoding='utf-8')
        self.run_git('add', '--pathspec-from-file=-', '--pathspec-file-nul',
                     input_data='\0'.join(names) + '\0')
        paths = self.run_git('ls-files', '-z').stdout.split('\0')[:-1]
        self.assertEqual(set(paths), set(names))
        data = b'\x00\xff\xfe\r\n\x80\x00'
        oid = self.run_git('hash-object', '-w', '--stdin', input_data=data,
                           text=False).stdout.strip().decode('ascii')
        output = self.run_git('cat-file', 'blob', oid, text=False).stdout
        self.assertEqual(output, data)
        text_output = self.run_git('cat-file', 'blob', oid).stdout
        # subprocess text mode intentionally normalizes CRLF; binary never does.
        self.assertEqual(text_output.encode('utf-8', 'surrogateescape'),
                         data.replace(b'\r\n', b'\n'))

    def test_check_false_and_timeout(self):
        result = self.run_git('rev-parse', '--verify', 'HEAD', check=False)
        self.assertNotEqual(result.returncode, 0)
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_git('rev-parse', '--verify', 'HEAD')
        with patch('git_service.subprocess.run', side_effect=subprocess.TimeoutExpired('git', 90)):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_git('fetch', timeout=90.0)

    def test_reads_do_not_rewrite_index_worktree_or_refs(self):
        oid = self.commit_file()
        tracked = self.repo / 'tracked.txt'
        stat = tracked.stat()
        # Force a stat-cache refresh opportunity without changing the content.
        os.utime(tracked, ns=(stat.st_atime_ns, stat.st_mtime_ns + 3_000_000_000))
        index = self.repo / '.git' / 'index'
        before = (index.read_bytes(), index.stat().st_mtime_ns)
        content_before = tracked.read_bytes()
        refs = self.run_git('for-each-ref', '--format=%(refname) %(objectname)').stdout
        files = {p.relative_to(self.repo).as_posix() for p in self.repo.rglob('*')}
        get_status(self.repo, self.git)
        self.run_git('diff', '--no-ext-diff', '--no-textconv', 'HEAD', '--')
        graph = build_graph(self.repo, self.git)
        self.assertEqual(graph['head_oid'], oid)
        self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns), before)
        self.assertEqual(tracked.read_bytes(), content_before)
        self.assertEqual(refs, self.run_git('for-each-ref', '--format=%(refname) %(objectname)').stdout)
        self.assertEqual(files, {p.relative_to(self.repo).as_posix() for p in self.repo.rglob('*')})

    def test_repository_hooks_are_not_bypassed(self):
        self.commit_file()
        hooks = self.root / 'custom-hooks'
        hooks.mkdir()
        hook = hooks / 'pre-commit'
        hook.write_text('#!/bin/sh\nexit 41\n', encoding='utf-8')
        hook.chmod(0o755)
        self.run_git('config', 'core.hooksPath', hooks.as_posix())
        result = self.run_git('commit', '--allow-empty', '-m', 'must fail', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.run_git('rev-list', '--count', 'HEAD').stdout.strip(), '1')

    def test_signing_configuration_is_not_bypassed(self):
        self.commit_file()
        self.run_git('config', 'commit.gpgsign', 'true')
        self.run_git('config', 'gpg.program', (self.root / 'nonexistent-signer').as_posix())
        result = self.run_git('commit', '--allow-empty', '-m', 'must fail', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.run_git('rev-list', '--count', 'HEAD').stdout.strip(), '1')


class TestSmallGraphPrimitives(GitPrimitiveCase):
    def test_empty_repository_and_page_validation(self):
        empty = build_graph(self.repo, self.git)
        self.assertEqual(empty['nodes'], [])
        self.assertFalse(empty['has_more'])
        self.assertEqual(empty['head_branch'], 'main')
        for page, size in [(-1, 100), (0, 0), (0, -1)]:
            with self.assertRaises(ValueError):
                build_graph(self.repo, self.git, page=page, page_size=size)

    def test_unique_abbreviations_sha1_and_sha256(self):
        for object_format in ('sha1', 'sha256'):
            with self.subTest(object_format=object_format):
                self.repo = self.root / object_format
                self.repo.mkdir()
                self.run_git('init', '-b', 'main', '--object-format=' + object_format)
                self.run_git('config', 'user.name', 'Fixture Author')
                self.run_git('config', 'user.email', 'fixture@example.invalid')
                oid = self.commit_file('中文.txt', '中文提交\n')
                self.run_git('config', 'core.abbrev', '4')
                # Collide two valid commit objects in-memory, and install only
                # the pair, avoiding 100k subprocesses or persistent test files.
                tree = self.run_git('rev-parse', 'HEAD^{tree}').stdout.strip()
                prefixes = {}
                pair = None
                for number in range(150000):
                    body = (f'tree {tree}\nparent {oid}\n'
                            'author Fixture <fixture@example.invalid> 1700000000 +0800\n'
                            'committer Fixture <fixture@example.invalid> 1700000000 +0800\n'
                            f'\ncollision {number}\n').encode('utf-8')
                    digest = hashlib.new(object_format, b'commit ' + str(len(body)).encode() + b'\0' + body).hexdigest()
                    if digest[:7] in prefixes:
                        pair = (prefixes[digest[:7]], body)
                        break
                    prefixes[digest[:7]] = body
                self.assertIsNotNone(pair, 'deterministic collision fixture must find a pair')
                for index, body in enumerate(pair):
                    commit = self.run_git('hash-object', '-t', 'commit', '-w', '--stdin',
                                          input_data=body, text=False).stdout.strip().decode('ascii')
                    self.run_git('update-ref', f'refs/heads/collision-{index}', commit)
                graph = build_graph(self.repo, self.git)
                self.assertEqual(len(graph['nodes']), 3)
                length = 40 if object_format == 'sha1' else 64
                collided = []
                for node in graph['nodes']:
                    self.assertEqual(len(node['oid']), length)
                    self.assertEqual(node['short_oid'], self.run_git('rev-parse', '--short=7', node['oid']).stdout.strip())
                    self.assertGreaterEqual(len(node['short_oid']), 7)
                    if node['oid'] != oid:
                        self.assertEqual(node['parents'], [oid])
                        self.assertTrue(node['committed_at_iso'].endswith('+08:00'))
                        collided.append(node['short_oid'])
                self.assertTrue(all(len(short) > 7 for short in collided))
                self.assertEqual(len(set(collided)), 2)

    def test_symbolic_head_switch_on_same_commit_invalidates_snapshot(self):
        self.commit_file()
        self.run_git('branch', 'same-tip')
        graph = build_graph(self.repo, self.git)
        self.run_git('symbolic-ref', 'HEAD', 'refs/heads/same-tip')
        with self.assertRaises(GraphSnapshotExpiredError):
            build_graph(self.repo, self.git, snapshot_id=graph['snapshot_id'])

    def test_shallow_boundary_keeps_real_object_parents(self):
        root = self.commit_file()
        self.run_git('commit', '--allow-empty', '-m', 'child')
        tip = self.run_git('rev-parse', 'HEAD').stdout.strip()
        (self.repo / '.git' / 'shallow').write_text(tip + '\n', encoding='ascii')
        graph = build_graph(self.repo, self.git)
        self.assertEqual(len(graph['nodes']), 1)
        node = graph['nodes'][0]
        self.assertEqual(node['parents'], [root])
        self.assertEqual(node['boundary_parents'], [{'oid': root, 'lane': 0}])
        self.assertEqual(graph['boundary_parents'], node['boundary_parents'])

    def test_replace_refs_do_not_rewrite_real_graph(self):
        root = self.commit_file()
        self.run_git('commit', '--allow-empty', '-m', 'child')
        tip = self.run_git('rev-parse', 'HEAD').stdout.strip()
        before = build_graph(self.repo, self.git)
        self.run_git('replace', tip, root)
        after = build_graph(self.repo, self.git, snapshot_id=before['snapshot_id'])
        self.assertEqual(before, after)
        self.assertEqual(after['nodes'][0]['parents'], [root])

    def test_invalid_repository_preserves_repository_error_contract(self):
        with self.assertRaises(GitRepositoryError):
            build_graph(self.root, self.git)
        with self.assertRaises(GitRepositoryError):
            git_graph.load_commit_graph(self.root, self.git)

    def test_linear_lane_stays_zero(self):
        nodes = [CommitNode(str(n), [str(n - 1)] if n else []) for n in range(300, -1, -1)]
        assign_lanes(nodes)
        self.assertEqual({node.lane for node in nodes}, {0})
        self.assertEqual([node.row for node in nodes], list(range(301)))


class TestLargeGraphPrimitives(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='git_web_large_graph_')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.env = patch.dict(os.environ, {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull})
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        cls.repo = Path(cls.temp.name) / 'repo'
        cls.repo.mkdir()
        cls.git = detect_git_executable()
        cls.run_git('init', '-b', 'main')
        cls.run_git('config', 'user.name', 'Fixture Author')
        cls.run_git('config', 'user.email', 'fixture@example.invalid')
        stream = []
        mark = 0

        def commit(ref, parent=None, merge=None):
            nonlocal mark
            mark += 1
            message = f'fixture commit {mark} 中文\n'.encode('utf-8')
            stream.extend([
                f'commit {ref}\nmark :{mark}\n'.encode(),
                f'author Fixture <fixture@example.invalid> {1700000000 + mark} +0800\n'.encode(),
                f'committer Fixture <fixture@example.invalid> {1700000000 + mark} +0800\n'.encode(),
                f'data {len(message)}\n'.encode() + message,
            ])
            if parent:
                stream.append(f'from :{parent}\n'.encode())
            if merge:
                stream.append(f'merge :{merge}\n'.encode())
            stream.append(b'\n')
            return mark

        root = commit('refs/heads/main')
        trunk = root
        for _ in range(70):
            trunk = commit('refs/heads/main', trunk)
        main = trunk
        for _ in range(110):
            main = commit('refs/heads/main', main)
        side = trunk
        for _ in range(125):
            side = commit('refs/heads/experiment', side)
        commit('refs/heads/main', main, side)
        remote = root
        for _ in range(3):
            remote = commit('refs/remotes/origin/remote-only', remote)
        commit('refs/llmwiki/recovery/hidden', root)
        commit('refs/tags/tag-only-history', root)
        cls.run_git('fast-import', '--quiet', input_data=b''.join(stream), text=False)
        cls.run_git('tag', '-a', 'release', '-m', 'annotated', 'main')
        cls.run_git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/remote-only')
        cls.run_git('update-ref', 'refs/heads/ancient',
                    cls.run_git('rev-list', '--max-parents=0', 'main').stdout.strip())
        cls.main_oid = cls.run_git('rev-parse', 'main').stdout.strip()
        cls.side_oid = cls.run_git('rev-parse', 'experiment').stdout.strip()
        cls.private_oid = cls.run_git('rev-parse', 'refs/llmwiki/recovery/hidden').stdout.strip()
        cls.tag_only_oid = cls.run_git('rev-parse', 'refs/tags/tag-only-history').stdout.strip()

    @classmethod
    def run_git(cls, *args, **kwargs):
        return _run_git_command(cls.git, list(args), cwd=cls.repo, **kwargs)

    def test_pages_match_one_shot_topology_and_global_lanes(self):
        full = build_graph(self.repo, self.git, page_size=1000)
        self.assertGreater(len(full['nodes']), 300)
        expected = self.run_git('rev-list', '--topo-order', '--branches', '--remotes', 'HEAD').stdout.splitlines()
        self.assertEqual([n['oid'] for n in full['nodes']], expected)
        nodes_by_oid = {n['oid']: n for n in full['nodes']}
        parents = {}
        for line in self.run_git('rev-list', '--parents', '--branches', '--remotes', 'HEAD').stdout.splitlines():
            oid, *actual = line.split()
            parents[oid] = actual
        assembled = []
        pages = []
        for page in range(4):
            result = build_graph(self.repo, self.git, page=page, snapshot_id=full['snapshot_id'])
            pages.append(result)
            self.assertEqual(result['snapshot_id'], full['snapshot_id'])
            self.assertEqual(len(result['nodes']), min(100, len(expected) - page * 100))
            self.assertEqual(result['has_more'], page < 3)
            for node in result['nodes']:
                self.assertEqual(node['parents'], parents[node['oid']])
                for parent, lane in zip(node['parents'], node['parent_lanes']):
                    self.assertEqual(lane, nodes_by_oid[parent]['lane'])
                # Boundary flags are necessarily relative to the loaded prefix.
                assembled.append({k: v for k, v in node.items() if k != 'boundary_parents'})
        self.assertEqual(assembled, [{k: v for k, v in node.items() if k != 'boundary_parents'} for node in full['nodes']])
        self.assertLess(max(node['lane'] for node in full['nodes']), 5)
        self.assertTrue(pages[0]['boundary_parents'])
        for result in pages:
            stop = (result['page'] + 1) * 100
            loaded = set(expected[:stop])
            for boundary in result['boundary_parents']:
                self.assertNotIn(boundary['oid'], loaded)
                self.assertEqual(boundary['lane'], nodes_by_oid[boundary['oid']]['lane'])
            for node in result['nodes']:
                self.assertEqual({p['oid'] for p in node['boundary_parents']}, set(node['parents']) - loaded)
        final = build_graph(self.repo, self.git, page=4, snapshot_id=full['snapshot_id'])
        self.assertEqual(final['nodes'], [])
        self.assertFalse(final['has_more'])
        exact = build_graph(self.repo, self.git, page_size=len(expected))
        self.assertFalse(exact['has_more'])

    def test_visible_remote_labels_and_private_ref_exclusion(self):
        graph = build_graph(self.repo, self.git, page_size=1000)
        nodes = {node['oid']: node for node in graph['nodes']}
        self.assertNotIn(self.private_oid, nodes)
        self.assertNotIn(self.tag_only_oid, nodes)
        self.assertIn('release', nodes[self.main_oid]['tags'])
        self.assertEqual(len(nodes[self.main_oid]['parents']), 2)
        self.assertIn('中文', nodes[self.main_oid]['subject'])
        remote_oid = self.run_git('rev-parse', 'refs/remotes/origin/remote-only').stdout.strip()
        self.assertIn('origin/remote-only', nodes[remote_oid]['branches'])
        self.assertIn('origin/HEAD', nodes[remote_oid]['branches'])
        branch = next(b for b in graph['branches'] if b['name'] == 'origin/remote-only')
        self.assertTrue(branch['remote'])
        self.assertFalse(branch['current'])
        self.assertFalse(any(r['refname'].startswith('refs/llmwiki/') for r in graph['refs']))
        snapshot = graph['snapshot_id']
        self.run_git('update-ref', 'refs/llmwiki/recovery/second', self.private_oid)
        try:
            self.assertEqual(build_graph(self.repo, self.git, snapshot_id=snapshot)['snapshot_id'], snapshot)
        finally:
            self.run_git('update-ref', '-d', 'refs/llmwiki/recovery/second')

    def test_non_head_local_remote_and_tag_changes_expire_snapshot(self):
        for refname in ['refs/heads/experiment', 'refs/heads/ancient',
                        'refs/remotes/origin/remote-only', 'refs/tags/release']:
            with self.subTest(refname=refname):
                original = self.run_git('rev-parse', refname).stdout.strip()
                first = build_graph(self.repo, self.git)
                if refname == 'refs/heads/ancient':
                    self.assertNotIn(original, {node['oid'] for node in first['nodes']})
                    self.assertIn(refname, {item['refname'] for item in first['refs']})
                self.run_git('update-ref', refname, self.main_oid)
                try:
                    with self.assertRaises(GraphSnapshotExpiredError) as failure:
                        build_graph(self.repo, self.git, page=1, snapshot_id=first['snapshot_id'])
                    self.assertEqual(failure.exception.code, 'snapshot_expired')
                    self.assertNotEqual(failure.exception.current_snapshot_id, first['snapshot_id'])
                    self.assertEqual(self.run_git('rev-parse', 'HEAD').stdout.strip(), self.main_oid)
                finally:
                    self.run_git('update-ref', refname, original)

    def test_renamed_branch_with_same_oid_expires_snapshot(self):
        first = build_graph(self.repo, self.git)
        self.run_git('update-ref', 'refs/heads/new-name', self.side_oid)
        try:
            with self.assertRaises(GraphSnapshotExpiredError):
                build_graph(self.repo, self.git, snapshot_id=first['snapshot_id'])
        finally:
            self.run_git('update-ref', '-d', 'refs/heads/new-name')

    def test_refs_change_during_read_rejected_even_without_incoming_snapshot(self):
        original_loader = git_graph._load_commits

        def change_during_read(*args, **kwargs):
            nodes = original_loader(*args, **kwargs)
            self.run_git('update-ref', 'refs/heads/experiment', self.main_oid)
            return nodes

        try:
            with patch('git_graph._load_commits', side_effect=change_during_read):
                with self.assertRaises(GraphSnapshotExpiredError):
                    build_graph(self.repo, self.git)
        finally:
            self.run_git('update-ref', 'refs/heads/experiment', self.side_oid)

    def test_detached_head_is_a_root_even_without_a_visible_branch(self):
        self.run_git('update-ref', '--no-deref', 'HEAD', self.private_oid)
        try:
            graph = build_graph(self.repo, self.git, page_size=1000)
            self.assertIsNone(graph['head_branch'])
            self.assertIn(self.private_oid, {node['oid'] for node in graph['nodes']})
            self.assertFalse(any(branch['current'] for branch in graph['branches']))
        finally:
            self.run_git('symbolic-ref', 'HEAD', 'refs/heads/main')


if __name__ == '__main__':
    unittest.main()
