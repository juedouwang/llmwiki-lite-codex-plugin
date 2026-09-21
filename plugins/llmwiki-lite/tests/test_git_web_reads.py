"""真实仓库根目录、只读差异及身份前置检查。"""
from test_git_web import GitWebFixture
from llmwiki_registry import register_project


class GitWebReadBoundaryTests(GitWebFixture):
    def register(self, root, name, state=None):
        return register_project(str(root), name=name, home=str(self.home), select=False,
                                wiki_root=str(self.root / (name + '-wiki')),
                                state_root=str(state or self.root / (name + '-state')))['project']['id']

    def test_non_repo_child_of_repo_and_bare_never_bind_parent(self):
        before = self.commit({'base.txt': 'base\n'})
        plain = self.root / 'plain'
        plain.mkdir()
        child = self.repo / 'child'
        child.mkdir()
        bare = self.root / 'bare.git'
        self.git('init', '--bare', str(bare))
        for name, root in [('plain', plain), ('child', child), ('bare', bare)]:
            with self.subTest(name=name):
                pid = self.register(root, name)
                self.api('status', method='GET', pid=pid, status=409, code='unsupported_repo')
                self.api('preview', {'action': 'save', 'params': {}}, pid=pid,
                         status=409, code='unsupported_repo')
        self.assertEqual(self.head(), before)
        self.assertFalse((plain / '.git').exists())
        self.assertFalse((child / '.git').exists())

    def test_state_inside_unignored_repo_is_browse_only(self):
        self.commit({'base.txt': 'base\n'})
        pid = self.register(self.repo, 'primary', self.repo / 'machine-state')
        status = self.api('status', method='GET', pid=pid)
        self.assertFalse(status['capabilities']['write'])
        self.assertIn('机器状态目录', status['capabilities']['reason'])
        self.assertFalse((self.repo / '.gitignore').exists())

    def test_initial_commit_binary_encoding_and_truncated_diffs(self):
        oid = self.commit({'binary.bin': b'zero\0one\n', 'legacy.txt': b'\xff\xfe\n',
                           'large.txt': 'line\n' * 5005,
                           'unsafe.txt': '<script>window.injected=true</script>\n'})
        self.write('unsafe.txt', 'worktree content must not leak\n')
        meta = self.api('commit/' + oid, method='GET')
        self.assertIsNone(meta['base_oid'])
        ids = {f['path']: f['file_id'] for f in meta['files']}
        def diff(name):
            return self.api('diff', {'kind': 'commit', 'oid': oid, 'file_id': ids[name]}, method='GET')
        for name in ('binary.bin', 'legacy.txt'):
            value = diff(name)
            self.assertTrue(value['binary'])
            self.assertFalse(value['supported'])
        value = diff('large.txt')
        self.assertTrue(value['truncated'])
        self.assertLessEqual(len(value['text'].splitlines()), 5000)
        self.assertIn('<script>', diff('unsafe.txt')['text'])
        self.assertNotIn('worktree content', diff('unsafe.txt')['text'])

    def test_restore_without_identity_leaves_tree_and_index_unchanged(self):
        target = self.commit({'base.txt': 'old\n'})
        before = self.commit({'base.txt': 'new\n'})
        self.git('config', '--unset', 'user.name')
        self.git('config', '--unset', 'user.email')
        index = (self.repo / '.git/index').read_bytes()
        self.preview('restore', {'target_oid': target}, status=422, code='identity_required')
        self.assertEqual(self.head(), before)
        self.assertEqual((self.repo / '.git/index').read_bytes(), index)
        self.assertEqual((self.repo / 'base.txt').read_bytes(), b'new\n')
