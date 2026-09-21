"""Read-path process budgets without bypassing repository safety checks."""
from unittest.mock import patch

from test_git_web import GitWebFixture
import git_service
import git_web


class GitWebReadPerformanceTests(GitWebFixture):
    def test_head_branch_detached_unborn_and_sha256(self):
        exe = git_web.checked_git()
        for object_format in ('sha1', 'sha256'):
            with self.subTest(object_format=object_format):
                repo = self.root / object_format
                repo.mkdir()
                self.git('init', '-b', 'main', '--object-format=' + object_format, repo=repo)
                self.configure(repo)
                with patch('git_service._run_git_command', wraps=git_service._run_git_command) as run:
                    head = git_service.get_head_info(repo, exe)
                self.assertEqual((head.oid, head.branch, head.unborn, head.detached), (None, 'main', True, False))
                self.assertEqual(run.call_count, 2)
                oid = self.commit({'file.txt': 'fixture\n'}, repo=repo)
                with patch('git_service._run_git_command', wraps=git_service._run_git_command) as run:
                    head = git_service.get_head_info(repo, exe)
                self.assertEqual((head.oid, head.branch, head.unborn, head.detached), (oid, 'main', False, False))
                self.assertEqual(run.call_count, 1)
                self.git('checkout', '--detach', repo=repo)
                with patch('git_service._run_git_command', wraps=git_service._run_git_command) as run:
                    head = git_service.get_head_info(repo, exe)
                self.assertEqual((head.oid, head.branch, head.unborn, head.detached), (oid, None, False, True))
                self.assertEqual(run.call_count, 1)

    def test_metadata_single_command_and_config_fresh_per_request(self):
        git_web.checked_git()
        with patch('git_web._run_git_command', wraps=git_web._run_git_command) as run:
            repo = git_web.Repo(str(self.home), self.pid)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(repo.root, self.repo)
            self.assertEqual(repo.gitdir, self.repo / '.git')
            self.assertEqual(repo.common, repo.gitdir)
            self.assertEqual(repo.oid_length, 40)
            self.assertEqual(repo.superproject, '')
            self.assertEqual(repo.config['user.name'], 'API Fixture')
            self.assertEqual(run.call_count, 2)
            self.assertEqual(repo.config['user.name'], 'API Fixture')
            self.assertEqual(run.call_count, 2)
        self.git('config', 'user.name', 'Changed externally')
        self.git('config', 'core.sparseCheckout', 'true')
        fresh = git_web.Repo(str(self.home), self.pid)
        self.assertEqual(fresh.config['user.name'], 'Changed externally')
        self.assertFalse(fresh.capabilities()['write'])

    def test_attributes_check_once_per_directory_including_ignored_ancestors(self):
        changes = {f'nested/{folder}/file-{i}.txt': 'fixture\n'
                   for folder in ('a', 'b') for i in range(60)}
        changes['.gitignore'] = '.gitattributes\n'
        self.commit(changes)
        repo = git_web.Repo(str(self.home), self.pid)
        with patch.object(repo, 'path', wraps=repo.path) as resolve:
            self.assertTrue(repo.capabilities()['write'])
        self.assertEqual(resolve.call_count, 3)
        self.write('nested/.gitattributes', '*.txt filter=probe\n')
        sentinel = self.root / 'filter-ran'
        self.git('config', 'filter.probe.clean', 'echo called > "' + sentinel.as_posix() + '"')
        fresh = git_web.Repo(str(self.home), self.pid)
        with patch.object(fresh, 'path', wraps=fresh.path) as resolve:
            self.assertFalse(fresh.capabilities()['write'])
        self.assertEqual(resolve.call_count, 3)
        self.preview('save', status=409, code='unsupported_repo')
        self.assertFalse(sentinel.exists())

    def test_combined_metadata_preserves_submodule_read_only_boundary(self):
        child, _ = self.new_project('child-source')
        self.commit({'module.txt': 'module\n'}, repo=child)
        self.commit({'root.txt': 'parent\n'})
        self.git('submodule', 'add', '--', str(child), 'nested/module')
        self.git('commit', '-m', 'fixture submodule')
        from llmwiki_registry import register_project
        project = register_project(
            str(self.repo / 'nested/module'), name='module', home=str(self.home),
            state_root=str(self.root / 'module-state'), wiki_root=str(self.root / 'module-wiki'),
            select=False)['project']
        repo = git_web.Repo(str(self.home), project['id'])
        self.assertEqual(repo.root, self.repo / 'nested/module')
        self.assertEqual(repo.superproject.replace('\\', '/'), self.repo.as_posix())
        self.assertFalse(repo.capabilities()['write'])
        self.preview('save', pid=project['id'], status=409, code='unsupported_repo')
        self.assertFalse(git_web.Repo(str(self.home), self.pid).capabilities()['write'])
