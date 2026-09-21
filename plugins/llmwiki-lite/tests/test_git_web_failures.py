"""故障注入只替换失败的 commit/push，成功路径仍执行真实临时 Git。"""
import subprocess
from unittest.mock import patch

from test_git_web import GitWebFixture
import git_operations
import git_revert_restore
import git_web


class GitWebFailureTests(GitWebFixture):
    def fail_commit(self, original):
        def command(exe, args, **kwargs):
            if args and args[0] == 'commit':
                raise subprocess.CalledProcessError(1, [exe, *args], stderr='injected failure')
            return original(exe, args, **kwargs)
        return command

    def test_restore_commit_failure_keeps_files_and_can_be_saved_later(self):
        target = self.commit({'tracked.txt': 'old\n', 'restored.txt': 'restore\n'})
        before = self.commit({'tracked.txt': 'new\n', 'restored.txt': None})
        preview = self.preview('restore', {'target_oid': target})
        original = git_revert_restore._run_git_command
        with patch.object(git_revert_restore, '_run_git_command', self.fail_commit(original)):
            result = self.execute(preview)
        self.assertEqual(result['outcome'], 'partial', result)
        self.assertEqual(self.head(), before)
        self.assertEqual((self.repo / 'tracked.txt').read_bytes(), b'old\n')
        self.assertEqual((self.repo / 'restored.txt').read_bytes(), b'restore\n')
        self.assertEqual(self.git('write-tree'), self.git('rev-parse', target + '^{tree}'))
        save = self.preview('save')
        result = self.api('execute', self.save_data(save, 'tracked.txt', 'restored.txt'))
        self.assertEqual(result['outcome'], 'done', result)
        self.assertEqual(self.git('show', '-s', '--format=%P', 'HEAD').strip(), before)
        self.assertEqual(self.git('rev-parse', 'HEAD^{tree}'), self.git('rev-parse', target + '^{tree}'))

    def test_save_commit_failure_preserves_worktree_and_unselected_staging(self):
        before = self.commit({'a.txt': 'base\n', 'b.txt': 'base\n'})
        self.write('a.txt', 'selected\n')
        self.write('b.txt', 'unselected staged\n')
        self.git('add', 'b.txt')
        unselected = self.git('ls-files', '--stage', 'b.txt')
        preview = self.preview('save')
        with patch.object(git_operations, '_run_git_command', self.fail_commit(git_operations._run_git_command)):
            result = self.api('execute', self.save_data(preview, 'a.txt'))
        self.assertEqual(result['outcome'], 'partial', result)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.git('ls-files', '--stage', 'b.txt'), unselected)
        self.assertEqual((self.repo / 'a.txt').read_bytes(), b'selected\n')
        self.assertEqual(self.git('show', ':a.txt'), 'selected\n')

    def test_push_timeout_is_uncertain_without_retry_or_upstream_change(self):
        before, bare, _, remote = self.remote_fixture()
        self.commit({'new.txt': 'local only\n'})
        preview = self.preview('push', {'remote_id': remote, 'target_branch': 'main'})
        original = git_web.Repo.git
        calls = []

        def timeout(repo, *args, **kwargs):
            if 'push' in args:
                calls.append(args)
                raise subprocess.TimeoutExpired(args, 90)
            return original(repo, *args, **kwargs)

        with patch.object(git_web.Repo, 'git', timeout):
            result = self.execute(preview)
        self.assertEqual(result['outcome'], 'partial', result)
        self.assertEqual(result['code'], 'result_uncertain')
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(self.git('config', '--get', 'branch.main.remote', check=False).returncode, 0)
        self.assertEqual(self.git('rev-parse', 'main', repo=bare).strip(), before)
        self.api('execute', {'preview_id': preview['preview_id']}, status=409, code='preview_expired')

    def test_ignored_nested_attributes_are_browse_only_without_running_filter(self):
        self.commit({'nested/data.txt': 'base\n', '.gitignore': '.gitattributes\n'})
        self.write('nested/.gitattributes', '*.txt filter=probe\n')
        sentinel = self.root / 'filter-ran'
        self.git('config', 'filter.probe.clean', 'echo called > "' + sentinel.as_posix() + '"')
        status = self.api('status', method='GET')
        self.assertFalse(status['capabilities']['write'])
        self.assertEqual(status['files'], [])
        self.preview('save', status=409, code='unsupported_repo')
        self.assertFalse(sentinel.exists())

    def test_target_attributes_block_switch_before_checkout(self):
        self.commit({'tracked.txt': 'base\n'})
        self.git('switch', '-c', 'filtered')
        self.commit({'.gitattributes': '*.txt filter=probe\n'})
        self.git('switch', 'main')
        before = self.head()
        sentinel = self.root / 'smudge-ran'
        self.git('config', 'filter.probe.smudge', 'echo called > "' + sentinel.as_posix() + '"')
        self.preview('switch_branch', {'branch': 'filtered'}, status=409, code='unsupported_repo')
        self.assertEqual(self.head(), before)
        self.assertFalse((self.repo / '.gitattributes').exists())
        self.assertFalse(sentinel.exists())
