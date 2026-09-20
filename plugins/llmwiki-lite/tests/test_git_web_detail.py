"""Read-only real-Git counts for the prototype-aligned commit inspector."""
from types import SimpleNamespace
from unittest.mock import patch

from test_git_web import GitWebFixture
import git_web


class CommitDetailTests(GitWebFixture):
    def detail(self, oid):
        return self.api('commit/' + oid, method='GET')

    def test_initial_modified_deleted_and_binary_counts(self):
        first = self.commit({'参数 文件.txt': 'one\ntwo\n', 'gone.txt': 'gone\n',
                             'image.bin': b'first\x00binary'})
        detail = self.detail(first)
        self.assertEqual(detail['repository_path'], str(self.repo.resolve()))
        files = {f['path']: f for f in detail['files']}
        self.assertEqual((files['参数 文件.txt']['additions'], files['参数 文件.txt']['deletions']), (2, 0))
        self.assertTrue(files['image.bin']['binary'])
        self.assertIsNone(files['image.bin']['additions'])
        oid = self.commit({'参数 文件.txt': 'one\nupdated\nextra\n', 'gone.txt': None})
        files = {f['path']: f for f in self.detail(oid)['files']}
        self.assertEqual((files['参数 文件.txt']['additions'], files['参数 文件.txt']['deletions']), (2, 1))
        self.assertEqual((files['gone.txt']['additions'], files['gone.txt']['deletions']), (0, 1))
        self.assert_clean()

    def test_rename_counts_and_no_duplicate_stats_during_diff(self):
        self.commit({'旧名称.txt': 'stable\n' * 20})
        self.git('mv', '旧名称.txt', '新 名称.txt')
        oid = self.commit({'新 名称.txt': 'stable\n' * 20 + 'added\n'})
        file = self.detail(oid)['files'][0]
        self.assertEqual(file['old_path'], '旧名称.txt')
        self.assertEqual((file['additions'], file['deletions']), (1, 0))
        with patch('git_web.commit_file_stats', side_effect=AssertionError('diff must not recompute counts')):
            data = self.api('diff', {'kind': 'commit', 'oid': oid, 'file_id': file['file_id']}, method='GET')
        self.assertIn('+added', data['text'])
        self.assert_clean()

    def test_merge_counts_use_first_parent_without_external_diff(self):
        self.commit({'base.txt': 'base\n'})
        self.git('switch', '-c', 'feature')
        self.commit({'feature.txt': 'one\ntwo\n'})
        self.git('switch', 'main')
        self.commit({'main.txt': 'main only\n'})
        self.git('merge', '--no-ff', 'feature', '-m', 'merge fixture')
        sentinel = self.root / 'external-ran'
        self.git('config', 'diff.external', 'echo unsafe > "' + sentinel.as_posix() + '"')
        detail = self.detail(self.head())
        self.assertEqual(len(detail['parents']), 2)
        self.assertEqual([f['path'] for f in detail['files']], ['feature.txt'])
        self.assertEqual(detail['files'][0]['additions'], 2)
        self.assertFalse(sentinel.exists())

    def test_nul_parser_preserves_tabs_newlines_and_rename_destination(self):
        raw = b'2\t1\tname\twith\nnewline\0' + b'0\t0\t\0old\tname\0new\nname\0'
        repo = SimpleNamespace(git=lambda *args, **kwargs: SimpleNamespace(stdout=raw))
        counts = git_web.commit_file_stats(repo, 'base', 'target')
        self.assertEqual(counts['name\twith\nnewline']['additions'], 2)
        self.assertEqual(counts['new\nname']['deletions'], 0)
