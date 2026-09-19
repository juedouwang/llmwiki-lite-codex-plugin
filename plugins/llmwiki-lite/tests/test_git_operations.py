"""T-08 基础 Git 操作的验收测试（标准库 unittest 实现）。

AT-32: Repository initialization
AT-33: Staging and unstaging files
AT-34: Creating commits
AT-35: Branch operations
AT-36: Tag operations
AT-37: Remote operations
AT-41: Concurrency lock protection
AT-42: Hook safety verification
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from git_service import (
    detect_git_executable,
    get_git_version,
    get_head_info,
    get_status,
    compute_state_token,
    create_plan,
    RepositoryLock,
    generate_repo_id,
    get_repository_paths
)
from git_recovery import (
    create_recovery_point,
    get_recovery_root
)
from git_operations import (
    init_repository,
    clone_repository,
    stage_files,
    unstage_files,
    commit_changes,
    create_branch,
    switch_branch,
    delete_branch,
    create_tag,
    delete_tag,
    add_remote,
    remove_remote,
    fetch_remote,
    push_to_remote,
    set_author,
    GitOperationError,
    GitBranchExistsError,
    GitDirtyWorktreeError,
    GitAuthRequiredError
)


class GitOperationsTests(unittest.TestCase):
    """AT-32 ~ AT-42：仓库初始化、暂存、提交、分支、标签、远端、锁与钩子安全。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llmwiki-git-ops-")
        self.tmp_path = Path(self.tmp.name)

        # git_exe fixture: Get Git executable.
        self.git_exe = detect_git_executable()

        # temp_repo fixture: Create a temporary repository for testing.
        repo_dir = self.tmp_path / "test_repo"
        repo_dir.mkdir()

        # Initialize
        result = init_repository(
            repo_dir,
            self.git_exe,
            initial_branch="main",
            author_name="Test User",
            author_email="test@example.com"
        )

        self.temp_repo = {
            "path": repo_dir,
            "repo_id": result["repo_id"],
            "git_exe": self.git_exe
        }

        # state_root fixture: Create a state root directory.
        state_dir = self.tmp_path / "state"
        state_dir.mkdir()
        self.state_root = state_dir

        # lock_dir fixture: Create a lock directory.
        lock_dir = self.tmp_path / "locks"
        lock_dir.mkdir()
        self.lock_dir = lock_dir

    def tearDown(self):
        self.tmp.cleanup()


    # ========================================================================
    # AT-32: Repository Initialization
    # ========================================================================

    def test_at32_init_repository(self):
        """AT-32: Verify repository initialization with custom initial branch."""
        repo_dir = self.tmp_path / "new_repo"
        repo_dir.mkdir()

        result = init_repository(
            repo_dir,
            self.git_exe,
            initial_branch="develop",
            author_name="Test User",
            author_email="test@example.com"
        )

        assert "repo_id" in result
        assert result["initial_branch"] == "develop"

        # Verify Git repository was created
        assert (repo_dir / ".git").exists()

        # Verify initial branch
        head = get_head_info(repo_dir, self.git_exe)
        assert head.branch == "develop"
        assert head.unborn is True

        # Verify author config (local only)
        result = subprocess.run(
            [self.git_exe, "config", "--local", "--get", "user.name"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True
        )
        assert result.stdout.strip() == "Test User"

    def test_at32_init_already_exists(self):
        """AT-32: Verify error when initializing in existing repository."""
        repo_dir = self.tmp_path / "existing_repo"
        repo_dir.mkdir()

        # First init
        init_repository(repo_dir, self.git_exe)

        # Second init should fail
        with self.assertRaisesRegex(GitOperationError, "already a Git repository"):
            init_repository(repo_dir, self.git_exe)

    def test_at32_clone_repository(self):
        """AT-32: Verify repository cloning."""
        # Create source repository
        source_dir = self.tmp_path / "source"
        source_dir.mkdir()
        init_repository(source_dir, self.git_exe, author_name="Test", author_email="test@example.com")

        # Create initial commit
        test_file = source_dir / "file.txt"
        test_file.write_text("content")
        subprocess.run([self.git_exe, "add", "file.txt"], cwd=source_dir, check=True)
        subprocess.run([self.git_exe, "commit", "-m", "Initial"], cwd=source_dir, check=True)

        # Clone
        target_dir = self.tmp_path / "cloned"
        result = clone_repository(
            str(source_dir),
            target_dir,
            self.git_exe
        )

        assert "repo_id" in result
        assert result["cloned_branch"] == "main"
        assert target_dir.exists()
        assert (target_dir / "file.txt").exists()


    # ========================================================================
    # AT-33: Staging and Unstaging
    # ========================================================================

    def test_at33_stage_files(self):
        """AT-33: Verify staging files."""
        repo_path = self.temp_repo["path"]
        repo_id = self.temp_repo["repo_id"]
        git_exe = self.temp_repo["git_exe"]

        # Create test files
        (repo_path / "file1.txt").write_text("content1")
        (repo_path / "file2.txt").write_text("content2")

        # Get current state
        head, files, _ = get_status(repo_path, git_exe)
        state_token = compute_state_token(head, files, strength="full")

        # Create plan
        plan = create_plan(
            action="stage",
            repo_id=repo_id,
            worktree_id=repo_id,
            params={"files": ["file1.txt"]},
            state_token=state_token
        )

        # Stage file
        receipt = stage_files(
            repo_path,
            self.state_root,
            git_exe,
            repo_id,
            repo_id,
            ["file1.txt"],
            plan,
            self.lock_dir
        )

        assert receipt.action == "stage"
        assert "file1.txt" in receipt.result["staged_files"]

        # Verify staging
        head_after, files_after, _ = get_status(repo_path, git_exe)
        staged_files = [f for f in files_after if f.index_status in ('A', 'M')]
        assert len(staged_files) == 1
        assert staged_files[0].path == "file1.txt"

    def test_at33_unstage_files(self):
        """AT-33: Verify unstaging files."""
        repo_path = self.temp_repo["path"]
        repo_id = self.temp_repo["repo_id"]
        git_exe = self.temp_repo["git_exe"]

        # Create and stage file
        test_file = repo_path / "file.txt"
        test_file.write_text("content")
        subprocess.run([git_exe, "add", "file.txt"], cwd=repo_path, check=True)

        # Get current state
        head, files, _ = get_status(repo_path, git_exe)
        state_token = compute_state_token(head, files, strength="full")

        # Create plan
        plan = create_plan(
            action="unstage",
            repo_id=repo_id,
            worktree_id=repo_id,
            params={"files": ["file.txt"]},
            state_token=state_token
        )

        # Unstage file
        receipt = unstage_files(
            repo_path,
            self.state_root,
            git_exe,
            repo_id,
            repo_id,
            ["file.txt"],
            plan,
            self.lock_dir
        )

        assert receipt.action == "unstage"
        assert "file.txt" in receipt.result["unstaged_files"]

        # Verify unstaging (should be untracked now)
        head_after, files_after, _ = get_status(repo_path, git_exe)
        untracked = [f for f in files_after if f.worktree_status == '?']
        assert len(untracked) == 1
        assert untracked[0].path == "file.txt"


    # ========================================================================
    # AT-34: Creating Commits
    # ========================================================================

    def test_at34_commit_changes(self):
        """AT-34: Verify creating commits with message and author."""
        repo_path = self.temp_repo["path"]
        repo_id = self.temp_repo["repo_id"]
        git_exe = self.temp_repo["git_exe"]

        # Create and stage file
        test_file = repo_path / "file.txt"
        test_file.write_text("test content")
        subprocess.run([git_exe, "add", "file.txt"], cwd=repo_path, check=True)

        # Commit
        receipt = commit_changes(
            repo_path,
            self.state_root,
            git_exe,
            repo_id,
            repo_id,
            message="Test commit message",
            author_name="Custom Author",
            author_email="custom@example.com"
        )

        assert receipt.action == "commit"
        assert "commit_oid" in receipt.result
        assert receipt.result["branch"] == "main"
        assert receipt.result["message"] == "Test commit message"

        # Verify commit
        head = get_head_info(repo_path, git_exe)
        assert head.oid == receipt.result["commit_oid"]
        assert head.unborn is False

        # Verify commit message
        result = subprocess.run(
            [git_exe, "log", "-1", "--format=%s"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        assert result.stdout.strip() == "Test commit message"

    def test_at34_commit_validation(self):
        """AT-34: Verify commit message validation."""
        repo_path = self.temp_repo["path"]
        repo_id = self.temp_repo["repo_id"]
        git_exe = self.temp_repo["git_exe"]

        # Try to commit with empty message
        with self.assertRaisesRegex(GitOperationError, "must be 1-4000 characters"):
            commit_changes(
                repo_path,
                self.state_root,
                git_exe,
                repo_id,
                repo_id,
                message=""
            )

        # Try to commit with message too long
        with self.assertRaisesRegex(GitOperationError, "must be 1-4000 characters"):
            commit_changes(
                repo_path,
                self.state_root,
                git_exe,
                repo_id,
                repo_id,
                message="x" * 4001
            )


    # ========================================================================
    # AT-35: Branch Operations
    # ========================================================================

    def test_at35_create_branch(self):
        """AT-35: Verify creating branches."""
        repo_path = self.temp_repo["path"]
        git_exe = self.temp_repo["git_exe"]

        # Create initial commit
        test_file = repo_path / "file.txt"
        test_file.write_text("content")
        subprocess.run([git_exe, "add", "file.txt"], cwd=repo_path, check=True)
        subprocess.run([git_exe, "commit", "-m", "Initial"], cwd=repo_path, check=True)

        # Create branch
        result = create_branch(repo_path, git_exe, "feature")

        assert result["branch_name"] == "feature"
        assert "start_oid" in result

        # Verify branch exists
        branches_result = subprocess.run(
            [git_exe, "branch", "--list", "feature"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        assert "feature" in branches_result.stdout

    def test_at35_create_branch_already_exists(self):
        """AT-35: Verify error when creating existing branch."""
        repo_path = self.temp_repo["path"]
        git_exe = self.temp_repo["git_exe"]

        # Create initial commit
        test_file = repo_path / "file.txt"
        test_file.write_text("content")
        subprocess.run([git_exe, "add", "file.txt"], cwd=repo_path, check=True)
        subprocess.run([git_exe, "commit", "-m", "Initial"], cwd=repo_path, check=True)

        # Create branch
        create_branch(repo_path, git_exe, "feature")

        # Try to create again
        with self.assertRaisesRegex(GitBranchExistsError, "already exists"):
            create_branch(repo_path, git_exe, "feature")

    def test_at35_switch_branch_clean(self):
        """AT-35: Verify switching branches with clean worktree."""
        repo_path = self.temp_repo["path"]
        repo_id = self.temp_repo["repo_id"]
        git_exe = self.temp_repo["git_exe"]

        # Create initial commit
        test_file = repo_path / "file.txt"
        test_file.write_text("content")
        subprocess.run([git_exe, "add", "file.txt"], cwd=repo_path, check=True)
        subprocess.run([git_exe, "commit", "-m", "Initial"], cwd=repo_path, check=True)

        # Create and switch to feature branch
        create_branch(repo_path, git_exe, "feature")

        # Get current state
        head, files, _ = get_status(repo_path, git_exe)
        state_token = compute_state_token(head, files, strength="full")

        # Create plan
        plan = create_plan(
            action="switch_branch",
            repo_id=repo_id,
            worktree_id=repo_id,
            params={"branch": "feature"},
            state_token=state_token
        )

        # Switch branch
        receipt = switch_branch(
            repo_path,
            self.state_root,
            git_exe,
            repo_id,
            repo_id,
            "feature",
            plan,
            self.lock_dir
        )

        assert receipt.action == "switch_branch"
        assert receipt.result["branch"] == "feature"

        # Verify switch
        head_after = get_head_info(repo_path, git_exe)
        assert head_after.branch == "feature"

    def test_at35_switch_branch_dirty_fails(self):
        """AT-35: Verify switching branches fails with dirty worktree."""
        repo_path = self.temp_repo["path"]
        repo_id = self.temp_repo["repo_id"]
        git_exe = self.temp_repo["git_exe"]

        # Create initial commit
        test_file = repo_path / "file.txt"
        test_file.write_text("content")
        subprocess.run([git_exe, "add", "file.txt"], cwd=repo_path, check=True)
        subprocess.run([git_exe, "commit", "-m", "Initial"], cwd=repo_path, check=True)

        # Create feature branch
        create_branch(repo_path, git_exe, "feature")

        # Make uncommitted changes
        test_file.write_text("modified content")

        # Get current state
        head, files, _ = get_status(repo_path, git_exe)
        state_token = compute_state_token(head, files, strength="full")

        # Create plan
        plan = create_plan(
            action="switch_branch",
            repo_id=repo_id,
            worktree_id=repo_id,
            params={"branch": "feature"},
            state_token=state_token
        )

        # Switch should fail
        with self.assertRaisesRegex(GitDirtyWorktreeError, "uncommitted changes"):
            switch_branch(
                repo_path,
                self.state_root,
                git_exe,
                repo_id,
                repo_id,
                "feature",
                plan,
                self.lock_dir
            )

    def test_at35_delete_branch(self):
        """AT-35: Verify deleting branches."""
        repo_path = self.temp_repo["path"]
        git_exe = self.temp_repo["git_exe"]

        # Create initial commit
        test_file = repo_path / "file.txt"
        test_file.write_text("content")
        subprocess.run([git_exe, "add", "file.txt"], cwd=repo_path, check=True)
        subprocess.run([git_exe, "commit", "-m", "Initial"], cwd=repo_path, check=True)

        # Create and delete branch
        create_branch(repo_path, git_exe, "temp-branch")
        result = delete_branch(repo_path, git_exe, "temp-branch")

        assert result["deleted_branch"] == "temp-branch"
        assert "last_oid" in result

        # Verify deletion
        branches_result = subprocess.run(
            [git_exe, "branch", "--list", "temp-branch"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        assert "temp-branch" not in branches_result.stdout


    # ========================================================================
    # AT-36: Tag Operations
    # ========================================================================

    def test_at36_create_tag(self):
        """AT-36: Verify creating tags."""
        repo_path = self.temp_repo["path"]
        git_exe = self.temp_repo["git_exe"]

        # Create initial commit
        test_file = repo_path / "file.txt"
        test_file.write_text("content")
        subprocess.run([git_exe, "add", "file.txt"], cwd=repo_path, check=True)
        subprocess.run([git_exe, "commit", "-m", "Initial"], cwd=repo_path, check=True)

        # Create tag
        result = create_tag(repo_path, git_exe, "v1.0.0")

        assert result["tag_name"] == "v1.0.0"
        assert "target_oid" in result
        assert result["annotated"] is False

        # Verify tag exists
        tags_result = subprocess.run(
            [git_exe, "tag", "--list", "v1.0.0"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        assert "v1.0.0" in tags_result.stdout

    def test_at36_create_annotated_tag(self):
        """AT-36: Verify creating annotated tags."""
        repo_path = self.temp_repo["path"]
        git_exe = self.temp_repo["git_exe"]

        # Create initial commit
        test_file = repo_path / "file.txt"
        test_file.write_text("content")
        subprocess.run([git_exe, "add", "file.txt"], cwd=repo_path, check=True)
        subprocess.run([git_exe, "commit", "-m", "Initial"], cwd=repo_path, check=True)

        # Create annotated tag
        result = create_tag(repo_path, git_exe, "v2.0.0", message="Release v2.0.0")

        assert result["tag_name"] == "v2.0.0"
        assert result["annotated"] is True

    def test_at36_delete_tag(self):
        """AT-36: Verify deleting tags."""
        repo_path = self.temp_repo["path"]
        git_exe = self.temp_repo["git_exe"]

        # Create initial commit
        test_file = repo_path / "file.txt"
        test_file.write_text("content")
        subprocess.run([git_exe, "add", "file.txt"], cwd=repo_path, check=True)
        subprocess.run([git_exe, "commit", "-m", "Initial"], cwd=repo_path, check=True)

        # Create and delete tag
        create_tag(repo_path, git_exe, "temp-tag")
        result = delete_tag(repo_path, git_exe, "temp-tag")

        assert result["deleted_tag"] == "temp-tag"

        # Verify deletion
        tags_result = subprocess.run(
            [git_exe, "tag", "--list", "temp-tag"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        assert "temp-tag" not in tags_result.stdout


    # ========================================================================
    # AT-37: Remote Operations
    # ========================================================================

    def test_at37_add_remote(self):
        """AT-37: Verify adding remotes."""
        repo_path = self.temp_repo["path"]
        git_exe = self.temp_repo["git_exe"]

        result = add_remote(
            repo_path,
            git_exe,
            "origin",
            "https://github.com/example/repo.git"
        )

        assert result["remote_name"] == "origin"
        assert result["remote_url"] == "https://github.com/example/repo.git"

        # Verify remote
        remotes_result = subprocess.run(
            [git_exe, "remote", "-v"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        assert "origin" in remotes_result.stdout

    def test_at37_add_remote_rejects_credentials(self):
        """AT-37: Verify remote URLs with embedded credentials are rejected."""
        repo_path = self.temp_repo["path"]
        git_exe = self.temp_repo["git_exe"]

        with self.assertRaisesRegex(GitOperationError, "embedded credentials"):
            add_remote(
                repo_path,
                git_exe,
                "unsafe",
                "https://user:pass@github.com/example/repo.git"
            )

    def test_at37_remove_remote(self):
        """AT-37: Verify removing remotes."""
        repo_path = self.temp_repo["path"]
        git_exe = self.temp_repo["git_exe"]

        # Add remote
        add_remote(repo_path, git_exe, "origin", "https://github.com/example/repo.git")

        # Remove remote
        result = remove_remote(repo_path, git_exe, "origin")

        assert result["removed_remote"] == "origin"

        # Verify removal
        remotes_result = subprocess.run(
            [git_exe, "remote", "-v"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        assert "origin" not in remotes_result.stdout

    def test_at37_set_author(self):
        """AT-37: Verify setting author configuration."""
        repo_path = self.temp_repo["path"]
        git_exe = self.temp_repo["git_exe"]

        result = set_author(
            repo_path,
            git_exe,
            "New Author",
            "new@example.com",
            scope="local"
        )

        assert result["author_name"] == "New Author"
        assert result["author_email"] == "new@example.com"
        assert result["scope"] == "local"

        # Verify config
        name_result = subprocess.run(
            [git_exe, "config", "--local", "--get", "user.name"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        assert name_result.stdout.strip() == "New Author"


    # ========================================================================
    # AT-41: Concurrency Lock Protection
    # ========================================================================

    def test_at41_repository_lock(self):
        """AT-41: Verify repository lock prevents concurrent access."""
        lock_dir = self.tmp_path / "locks"
        lock_dir.mkdir(exist_ok=True)

        repo_id = "test_repo_id"

        # Acquire lock
        lock1 = RepositoryLock(lock_dir, repo_id, timeout=1.0)
        lock1.acquire()

        # Try to acquire again (should timeout)
        lock2 = RepositoryLock(lock_dir, repo_id, timeout=0.5)

        from git_service import GitLockError
        with self.assertRaisesRegex(GitLockError, "Could not acquire lock"):
            lock2.acquire()

        # Release first lock
        lock1.release()

        # Now second lock should succeed
        lock2.acquire()
        lock2.release()

    def test_at41_lock_context_manager(self):
        """AT-41: Verify lock works as context manager."""
        lock_dir = self.tmp_path / "locks"
        lock_dir.mkdir(exist_ok=True)

        repo_id = "test_repo_id"

        with RepositoryLock(lock_dir, repo_id):
            # Lock is held
            lock_file = lock_dir / f"{repo_id}.lock"
            assert lock_file.exists()

        # Lock is released
        assert not lock_file.exists()

    # ========================================================================
    # AT-42: Hook Safety Verification
    # ========================================================================

    def test_at42_non_interactive_execution(self):
        """AT-42: Verify Git commands run non-interactively."""
        repo_path = self.temp_repo["path"]
        git_exe = self.temp_repo["git_exe"]

        # This test verifies that GIT_TERMINAL_PROMPT=0 is set
        # by attempting an operation that would normally prompt

        # Add a remote that doesn't exist
        add_remote(repo_path, git_exe, "missing", "https://github.com/nonexistent/repo.git")

        # Try to fetch (should fail immediately without prompting)
        with self.assertRaises((GitOperationError, GitAuthRequiredError)):
            fetch_remote(repo_path, git_exe, "missing")

        # If this test completes quickly (< 5 seconds), it means no prompt appeared


if __name__ == "__main__":
    unittest.main()
