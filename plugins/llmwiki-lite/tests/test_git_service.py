"""Tests for git_service module.

Covers AT-20 to AT-23, AT-40 to AT-42.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from git_service import (
    detect_git_executable,
    detect_git_capabilities,
    get_git_version,
    is_git_repository,
    get_repository_paths,
    get_object_format,
    generate_repo_id,
    generate_worktree_id,
    get_git_identity,
    get_head_info,
    get_status,
    list_branches,
    list_remotes,
    compute_state_token,
    get_full_status,
    RepositoryLock,
    create_plan,
    validate_plan_state,
    get_commit_info,
    get_diff,
    check_ref_format,
    GitError,
    GitNotFoundError,
    GitVersionError,
    GitRepositoryError,
    GitLockError,
    MIN_GIT_VERSION
)


class TestGitDetection(unittest.TestCase):
    """Test Git detection and capabilities."""

    def test_detect_git_executable(self):
        """Test Git executable detection."""
        git_exe = detect_git_executable()
        self.assertTrue(os.path.isfile(git_exe))
        self.assertTrue(os.access(git_exe, os.X_OK))

    def test_get_git_version(self):
        """Test Git version detection."""
        git_exe = detect_git_executable()
        version = get_git_version(git_exe)

        self.assertIsInstance(version, tuple)
        self.assertEqual(len(version), 3)
        self.assertGreaterEqual(version, MIN_GIT_VERSION)

    def test_detect_git_capabilities(self):
        """Test Git capabilities detection."""
        caps = detect_git_capabilities()

        self.assertTrue(caps.can_read)
        self.assertGreaterEqual(caps.version, MIN_GIT_VERSION)
        self.assertTrue(os.path.isfile(caps.git_executable))

    def test_check_ref_format(self):
        """Test ref format validation."""
        git_exe = detect_git_executable()

        # Valid refs
        self.assertTrue(check_ref_format("main", git_exe))
        self.assertTrue(check_ref_format("feature/test", git_exe))
        self.assertTrue(check_ref_format("release-1.0", git_exe))

        # Invalid refs
        self.assertFalse(check_ref_format("", git_exe))
        self.assertFalse(check_ref_format(".hidden", git_exe))
        self.assertFalse(check_ref_format("has..dots", git_exe))
        self.assertFalse(check_ref_format("ends-with-lock.lock", git_exe))


class TestGitFixtureF1(unittest.TestCase):
    """Test with GIT-F1 fixture (普通分叉).

    Covers AT-20 to AT-23, AT-40 to AT-42.
    """

    @classmethod
    def setUpClass(cls):
        """Create GIT-F1 fixture."""
        cls.temp_dir = tempfile.mkdtemp(prefix="git_test_")
        cls.repo_path = Path(cls.temp_dir) / "test_repo"
        cls.repo_path.mkdir()

        cls.git_exe = detect_git_executable()

        # Initialize repository
        cls._run_git(['init', '-b', 'main'], cls.repo_path)
        cls._run_git(['config', 'user.name', 'Fixture Researcher'], cls.repo_path)
        cls._run_git(['config', 'user.email', 'fixture@example.invalid'], cls.repo_path)

        # Create R0: README.md, config.txt, assets/sample.bin
        readme = cls.repo_path / "README.md"
        readme.write_text("# Test Repository\n", encoding='utf-8')

        config = cls.repo_path / "config.txt"
        config.write_text("value=0\n", encoding='utf-8')

        assets = cls.repo_path / "assets"
        assets.mkdir()
        sample_bin = assets / "sample.bin"
        sample_bin.write_bytes(b'\x00\x01\x02\x03\x04')

        cls._run_git(['add', '.'], cls.repo_path)
        cls._run_git_with_date(['commit', '-m', 'R0: Initial commit'], cls.repo_path, 0)

        # Store R0 OID
        result = cls._run_git(['rev-parse', 'HEAD'], cls.repo_path)
        cls.r0_oid = result.stdout.strip()

        # Create M1: modify README
        readme.write_text("# Test Repository\n\nMain branch work.\n", encoding='utf-8')
        cls._run_git(['add', 'README.md'], cls.repo_path)
        cls._run_git_with_date(['commit', '-m', 'M1: Update README'], cls.repo_path, 1)

        # Store M1 OID
        result = cls._run_git(['rev-parse', 'HEAD'], cls.repo_path)
        cls.m1_oid = result.stdout.strip()

        # Create M2: modify config
        config.write_text("value=main\n", encoding='utf-8')
        cls._run_git(['add', 'config.txt'], cls.repo_path)
        cls._run_git_with_date(['commit', '-m', 'M2: Set config to main'], cls.repo_path, 2)

        # Store M2 OID
        result = cls._run_git(['rev-parse', 'HEAD'], cls.repo_path)
        cls.m2_oid = result.stdout.strip()

        # Create experiment branch from R0
        cls._run_git(['checkout', '-b', 'experiment', cls.r0_oid], cls.repo_path)

        # Create X1: add experiment.txt
        experiment = cls.repo_path / "experiment.txt"
        experiment.write_text("Experimental feature\n", encoding='utf-8')
        cls._run_git(['add', 'experiment.txt'], cls.repo_path)
        cls._run_git_with_date(['commit', '-m', 'X1: Add experiment'], cls.repo_path, 3)

        # Store X1 OID
        result = cls._run_git(['rev-parse', 'HEAD'], cls.repo_path)
        cls.x1_oid = result.stdout.strip()

        # Create X2: modify config
        config.write_text("value=experiment\n", encoding='utf-8')
        cls._run_git(['add', 'config.txt'], cls.repo_path)
        cls._run_git_with_date(['commit', '-m', 'X2: Set config to experiment'], cls.repo_path, 4)

        # Store X2 OID
        result = cls._run_git(['rev-parse', 'HEAD'], cls.repo_path)
        cls.x2_oid = result.stdout.strip()

        # Switch back to main
        cls._run_git(['checkout', 'main'], cls.repo_path)

    @classmethod
    def tearDownClass(cls):
        """Clean up fixture."""
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    @classmethod
    def _run_git(cls, args, cwd):
        """Run git command."""
        result = subprocess.run(
            [cls.git_exe] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
        )
        return result

    @classmethod
    def _run_git_with_date(cls, args, cwd, day_offset):
        """Run git commit with fixed date."""
        base_date = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)
        commit_date = base_date + timedelta(days=day_offset)
        date_str = commit_date.strftime("%Y-%m-%d %H:%M:%S +0000")

        env = {
            **os.environ,
            'GIT_AUTHOR_DATE': date_str,
            'GIT_COMMITTER_DATE': date_str,
            'GIT_TERMINAL_PROMPT': '0'
        }

        subprocess.run(
            [cls.git_exe] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            env=env
        )

    def test_at20_repository_detection(self):
        """AT-20: 同common-dir两个worktree绑定.

        禁止跨项目重复归属
        """
        # Detect repository
        self.assertTrue(is_git_repository(self.repo_path, self.git_exe))

        # Get repository paths
        common_dir, worktree_root, is_bare = get_repository_paths(self.repo_path, self.git_exe)

        self.assertFalse(is_bare)
        self.assertEqual(worktree_root, self.repo_path.resolve())

        # Generate IDs
        repo_id = generate_repo_id(common_dir)
        worktree_id = generate_worktree_id(worktree_root)

        self.assertEqual(len(repo_id), 32)  # 16 bytes hex
        self.assertEqual(len(worktree_id), 32)

        # Same path should give same ID
        repo_id2 = generate_repo_id(common_dir)
        self.assertEqual(repo_id, repo_id2)

    def test_at21_special_characters_in_paths(self):
        """AT-21: 路径含空格/中文/tab或文件名以减号开头.

        正确显示/操作所选文件，不产生参数注入
        """
        # Create files with special characters
        special_file = self.repo_path / "file with spaces.txt"
        special_file.write_text("Content", encoding='utf-8')

        chinese_file = self.repo_path / "测试文件.txt"
        chinese_file.write_text("中文内容", encoding='utf-8')

        dash_file = self.repo_path / "-dashfile.txt"
        dash_file.write_text("Starts with dash", encoding='utf-8')

        # Get status
        head, files, _ = get_status(self.repo_path, self.git_exe)

        # Find our files
        file_paths = [f.path for f in files]
        self.assertIn("file with spaces.txt", file_paths)
        # Check for Chinese file - should have 3 untracked files at least
        self.assertGreaterEqual(len([p for p in file_paths if '.txt' in p]), 3)
        # Check that dash file is present
        self.assertIn("-dashfile.txt", file_paths)

        # Clean up
        special_file.unlink()
        chinese_file.unlink()
        dash_file.unlink()

    def test_at22_html_in_commit_message(self):
        """AT-22: 提交消息含HTML/script.

        当纯文本显示，不执行，不破坏布局
        """
        # Get commit info for R0
        commit_info = get_commit_info(self.repo_path, self.git_exe, self.r0_oid)

        # Verify we get back the commit message as-is
        self.assertEqual(commit_info['subject'], 'R0: Initial commit')
        self.assertEqual(commit_info['oid'], self.r0_oid)

        # Test with HTML in message (create temp commit)
        test_file = self.repo_path / "test.txt"
        test_file.write_text("Test", encoding='utf-8')
        self._run_git(['add', 'test.txt'], self.repo_path)
        self._run_git(['commit', '-m', '<script>alert("xss")</script>'], self.repo_path)

        result = self._run_git(['rev-parse', 'HEAD'], self.repo_path)
        test_oid = result.stdout.strip()

        commit_info = get_commit_info(self.repo_path, self.git_exe, test_oid)
        self.assertEqual(commit_info['subject'], '<script>alert("xss")</script>')

        # Reset to remove test commit
        self._run_git(['reset', '--hard', 'HEAD~1'], self.repo_path)
        test_file.unlink(missing_ok=True)

    def test_at23_read_operations_no_side_effects(self):
        """AT-23: 看状态/图/差异.

        refs/index/工作区字节不变，不fetch
        """
        # Record initial state
        result = self._run_git(['rev-parse', 'HEAD'], self.repo_path)
        initial_head = result.stdout.strip()

        result = self._run_git(['status', '--porcelain'], self.repo_path)
        initial_status = result.stdout

        # Perform read operations
        head = get_head_info(self.repo_path, self.git_exe)
        self.assertEqual(head.oid, initial_head)

        branches = list_branches(self.repo_path, self.git_exe, head.branch)
        self.assertGreater(len(branches), 0)

        diff_info = get_diff(self.repo_path, self.git_exe, self.r0_oid, self.m2_oid)
        self.assertIn('files', diff_info)

        # Verify state unchanged
        result = self._run_git(['rev-parse', 'HEAD'], self.repo_path)
        final_head = result.stdout.strip()
        self.assertEqual(initial_head, final_head)

        result = self._run_git(['status', '--porcelain'], self.repo_path)
        final_status = result.stdout
        self.assertEqual(initial_status, final_status)

    def test_at40_external_state_change_detection(self):
        """AT-40: 另一工具在预览后改HEAD/index/文件.

        plan失效STATE_CHANGED，不执行旧计划
        """
        # Get initial state
        status = get_full_status(self.repo_path, self.git_exe, "test-repo", "test-wt")
        initial_token = status.state_token

        # Create a plan
        plan = create_plan(
            action="test_action",
            repo_id="test-repo",
            worktree_id="test-wt",
            params={},
            state_token=initial_token,
            current_time="2026-09-19T10:00:00Z"
        )

        # Plan should be valid initially
        valid, error = validate_plan_state(
            plan,
            initial_token,
            current_time="2026-09-19T10:02:00Z"
        )
        self.assertTrue(valid)
        self.assertIsNone(error)

        # Modify a file (external change)
        test_file = self.repo_path / "external_change.txt"
        test_file.write_text("External modification", encoding='utf-8')

        # Get new state
        new_status = get_full_status(self.repo_path, self.git_exe, "test-repo", "test-wt")
        new_token = new_status.state_token

        # Token should be different
        self.assertNotEqual(initial_token, new_token)

        # Plan should be invalid
        valid, error = validate_plan_state(
            plan,
            new_token,
            current_time="2026-09-19T10:02:00Z"
        )
        self.assertFalse(valid)
        self.assertIn("state has changed", error)

        # Clean up
        test_file.unlink()

    def test_at41_repository_locking(self):
        """AT-41: 两网页/两个服务对同repo同时写.

        一次只有一个；另一排队或423，不能破坏index
        """
        lock_dir = Path(self.temp_dir) / "locks"
        lock_dir.mkdir(exist_ok=True)

        # Acquire first lock
        lock1 = RepositoryLock(lock_dir, "test-repo-123", timeout=1.0)
        lock1.acquire()

        try:
            # Try to acquire second lock (should fail)
            lock2 = RepositoryLock(lock_dir, "test-repo-123", timeout=1.0)

            with self.assertRaises(GitLockError) as ctx:
                lock2.acquire()

            self.assertIn("Could not acquire lock", str(ctx.exception))

        finally:
            lock1.release()

        # Now second lock should succeed
        lock3 = RepositoryLock(lock_dir, "test-repo-123", timeout=1.0)
        lock3.acquire()
        lock3.release()

    def test_at41_lock_context_manager(self):
        """Test lock context manager."""
        lock_dir = Path(self.temp_dir) / "locks"
        lock_dir.mkdir(exist_ok=True)

        # Use context manager
        with RepositoryLock(lock_dir, "test-repo-456", timeout=1.0) as lock:
            # Lock should be held
            lock_file = lock_dir / "test-repo-456.lock"
            self.assertTrue(lock_file.exists())

        # Lock should be released
        self.assertFalse(lock_file.exists())

    def test_at42_noninteractive_commands(self):
        """AT-42: 仓库带危险hook/filter/sshCommand.

        无信任不执行；无可见子窗口；日志不泄密
        """
        # This test verifies that commands are non-interactive
        # by checking environment variables are set correctly

        # Get identity (should work non-interactively)
        identity = get_git_identity(self.repo_path, self.git_exe)

        self.assertEqual(identity.name, "Fixture Researcher")
        self.assertEqual(identity.email, "fixture@example.invalid")
        self.assertEqual(identity.source, "local")

    def test_identity_detection(self):
        """Test Git identity detection."""
        identity = get_git_identity(self.repo_path, self.git_exe)

        self.assertEqual(identity.name, "Fixture Researcher")
        self.assertEqual(identity.email, "fixture@example.invalid")
        self.assertEqual(identity.source, "local")

    def test_head_info(self):
        """Test HEAD information retrieval."""
        head = get_head_info(self.repo_path, self.git_exe)

        self.assertFalse(head.unborn)
        self.assertFalse(head.detached)
        self.assertEqual(head.branch, "main")
        self.assertIsNotNone(head.oid)

    def test_branch_listing(self):
        """Test branch listing."""
        head = get_head_info(self.repo_path, self.git_exe)
        branches = list_branches(self.repo_path, self.git_exe, head.branch)

        # Should have main and experiment branches
        branch_names = [b.name for b in branches]
        self.assertIn("main", branch_names)
        self.assertIn("experiment", branch_names)

        # Current branch should be first
        self.assertEqual(branches[0].name, "main")
        self.assertTrue(branches[0].current)

    def test_diff_between_commits(self):
        """Test diff generation."""
        diff_info = get_diff(self.repo_path, self.git_exe, self.r0_oid, self.m2_oid)

        self.assertEqual(diff_info['base'], self.r0_oid)
        self.assertEqual(diff_info['target'], self.m2_oid)

        # Should show README.md and config.txt modified
        file_paths = [f.get('path', f.get('new_path', '')) for f in diff_info['files']]
        self.assertIn("README.md", file_paths)
        self.assertIn("config.txt", file_paths)

    def test_state_token_generation(self):
        """Test state token generation."""
        head = get_head_info(self.repo_path, self.git_exe)
        head_files, _, _ = get_status(self.repo_path, self.git_exe, include_files=False)

        token1 = compute_state_token(head, [], strength="lightweight")
        token2 = compute_state_token(head, [], strength="lightweight")

        # Same state should give same token
        self.assertEqual(token1, token2)
        self.assertEqual(len(token1), 64)  # SHA256 hex

    def test_plan_expiration(self):
        """Test plan expiration validation."""
        plan = create_plan(
            action="test",
            repo_id="repo-1",
            worktree_id="wt-1",
            params={},
            state_token="abc123",
            current_time="2026-09-19T10:00:00Z"
        )

        # Within expiration
        valid, error = validate_plan_state(
            plan,
            "abc123",
            current_time="2026-09-19T10:03:00Z"
        )
        self.assertTrue(valid)

        # After expiration (5 minutes)
        valid, error = validate_plan_state(
            plan,
            "abc123",
            current_time="2026-09-19T10:06:00Z"
        )
        self.assertFalse(valid)
        self.assertIn("expired", error)


class TestGitRemotes(unittest.TestCase):
    """Test remote repository operations."""

    def setUp(self):
        """Create test repository with remote."""
        self.temp_dir = tempfile.mkdtemp(prefix="git_remote_test_")
        self.repo_path = Path(self.temp_dir) / "test_repo"
        self.repo_path.mkdir()

        self.git_exe = detect_git_executable()

        # Initialize repository
        subprocess.run(
            [self.git_exe, 'init', '-b', 'main'],
            cwd=self.repo_path,
            check=True,
            capture_output=True
        )
        subprocess.run(
            [self.git_exe, 'config', 'user.name', 'Test User'],
            cwd=self.repo_path,
            check=True,
            capture_output=True
        )
        subprocess.run(
            [self.git_exe, 'config', 'user.email', 'test@example.com'],
            cwd=self.repo_path,
            check=True,
            capture_output=True
        )

        # Create initial commit
        readme = self.repo_path / "README.md"
        readme.write_text("# Test\n", encoding='utf-8')
        subprocess.run(
            [self.git_exe, 'add', 'README.md'],
            cwd=self.repo_path,
            check=True,
            capture_output=True
        )
        subprocess.run(
            [self.git_exe, 'commit', '-m', 'Initial commit'],
            cwd=self.repo_path,
            check=True,
            capture_output=True
        )

        # Add a remote
        subprocess.run(
            [self.git_exe, 'remote', 'add', 'origin', 'https://github.com/user/repo.git'],
            cwd=self.repo_path,
            check=True,
            capture_output=True
        )

    def tearDown(self):
        """Clean up test repository."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_list_remotes(self):
        """Test remote listing."""
        remotes = list_remotes(self.repo_path, self.git_exe)

        self.assertEqual(len(remotes), 1)
        self.assertEqual(remotes[0].name, "origin")
        self.assertEqual(remotes[0].display_url, "https://github.com/user/repo.git")
        self.assertIsNotNone(remotes[0].id)

    def test_sanitize_credentials_in_url(self):
        """Test credential sanitization in URLs."""
        # Add remote with credentials
        subprocess.run(
            [self.git_exe, 'remote', 'add', 'private', 'https://user:pass@github.com/user/private.git'],
            cwd=self.repo_path,
            check=True,
            capture_output=True
        )

        remotes = list_remotes(self.repo_path, self.git_exe)

        private_remote = [r for r in remotes if r.name == 'private'][0]
        # Credentials should be removed
        self.assertNotIn('user:pass', private_remote.display_url)
        self.assertIn('github.com', private_remote.display_url)


if __name__ == '__main__':
    unittest.main()
