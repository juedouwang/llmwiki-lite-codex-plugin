"""T-09 合并与冲突解决验收测试（标准库 unittest 版本）。

AT-38: GIT-F1 冲突解决与合并提交创建
AT-39: 无冲突分叉合并预览
AT-43: 重命名/删除与二进制冲突处理
AT-44: 合并中止与重启恢复
"""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

# Add parent scripts directory to path
import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from git_service import (
    detect_git_executable,
    get_head_info,
    get_status,
    RepositoryLock,
    generate_repo_id,
    _run_git_command
)
from git_recovery import (
    create_recovery_point,
    get_recovery_root
)
from git_operations import (
    init_repository,
    stage_files,
    commit_changes,
    create_branch,
    switch_branch,
    GitOperationError,
    GitDirtyWorktreeError
)
from git_merge import (
    MergeType,
    ConflictType,
    MergeStatus,
    analyze_merge,
    start_merge,
    detect_conflicts,
    resolve_conflict,
    complete_merge,
    abort_merge,
    get_active_merge_state,
    check_all_conflicts_resolved,
    preview_merge_result
)


class GitMergeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="llmwiki-git-merge-")
        self.tmp_path = Path(self._tmp.name)

        # git_exe fixture: Get Git executable.
        self.git_exe = detect_git_executable()

        # state_root fixture: Create a state root directory.
        self.state_root = self.tmp_path / "state"
        self.state_root.mkdir()

        # lock_dir fixture: Create a lock directory.
        self.lock_dir = self.tmp_path / "locks"
        self.lock_dir.mkdir()

        # ====================================================================
        # Fixture: GIT-F1 (Normal Fork with Text Conflict)
        # ====================================================================
        #
        # GIT-F1: Normal fork with text conflict.
        #
        # Initial branch main:
        # - R0: README.md, config.txt=value=0, assets/sample.bin
        # - M1: modify README
        # - M2: config.txt=value=main
        #
        # Branch experiment (from R0):
        # - X1: add experiment.txt
        # - X2: config.txt=value=experiment
        #
        # Merge should produce config.txt text conflict.
        # Manual resolution: value=merged
        # Result: M3 with parents [M2, X2]
        repo_dir = self.tmp_path / "git_f1"
        repo_dir.mkdir()

        # Initialize repository
        init_result = init_repository(
            repo_dir,
            self.git_exe,
            initial_branch="main",
            author_name="Fixture Researcher",
            author_email="fixture@example.invalid"
        )

        repo_id = init_result["repo_id"]
        worktree_id = init_result["worktree_id"]

        # R0: Initial commit
        (repo_dir / "README.md").write_text("# Test Repository\n", encoding='utf-8')
        (repo_dir / "config.txt").write_text("value=0\n", encoding='utf-8')
        (repo_dir / "assets").mkdir()
        (repo_dir / "assets" / "sample.bin").write_bytes(b'\x00\x01\x02\x03\x04')

        subprocess.run(
            [self.git_exe, "add", "."],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )

        subprocess.run(
            [self.git_exe, "commit", "-m", "R0: Initial commit",
             "--date=2026-01-05T00:00:00Z"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_AUTHOR_DATE": "2026-01-05T00:00:00Z",
                 "GIT_COMMITTER_DATE": "2026-01-05T00:00:00Z"}
        )

        # Get R0 OID
        result = subprocess.run(
            [self.git_exe, "rev-parse", "HEAD"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True
        )
        r0_oid = result.stdout.strip()

        # M1: Modify README
        (repo_dir / "README.md").write_text("# Test Repository\n\nMain branch changes.\n", encoding='utf-8')
        subprocess.run([self.git_exe, "add", "README.md"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            [self.git_exe, "commit", "-m", "M1: Update README"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_AUTHOR_DATE": "2026-01-05T01:00:00Z",
                 "GIT_COMMITTER_DATE": "2026-01-05T01:00:00Z"}
        )

        # M2: Modify config.txt to value=main
        (repo_dir / "config.txt").write_text("value=main\n", encoding='utf-8')
        subprocess.run([self.git_exe, "add", "config.txt"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            [self.git_exe, "commit", "-m", "M2: Set config to main"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_AUTHOR_DATE": "2026-01-05T02:00:00Z",
                 "GIT_COMMITTER_DATE": "2026-01-05T02:00:00Z"}
        )

        result = subprocess.run(
            [self.git_exe, "rev-parse", "HEAD"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True
        )
        m2_oid = result.stdout.strip()

        # Create experiment branch from R0
        subprocess.run(
            [self.git_exe, "checkout", "-b", "experiment", r0_oid],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )

        # X1: Add experiment.txt
        (repo_dir / "experiment.txt").write_text("Experiment data\n", encoding='utf-8')
        subprocess.run([self.git_exe, "add", "experiment.txt"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            [self.git_exe, "commit", "-m", "X1: Add experiment file"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_AUTHOR_DATE": "2026-01-05T03:00:00Z",
                 "GIT_COMMITTER_DATE": "2026-01-05T03:00:00Z"}
        )

        # X2: Modify config.txt to value=experiment
        (repo_dir / "config.txt").write_text("value=experiment\n", encoding='utf-8')
        subprocess.run([self.git_exe, "add", "config.txt"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            [self.git_exe, "commit", "-m", "X2: Set config to experiment"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_AUTHOR_DATE": "2026-01-05T04:00:00Z",
                 "GIT_COMMITTER_DATE": "2026-01-05T04:00:00Z"}
        )

        result = subprocess.run(
            [self.git_exe, "rev-parse", "HEAD"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True
        )
        x2_oid = result.stdout.strip()

        # Switch back to main
        subprocess.run([self.git_exe, "checkout", "main"], cwd=repo_dir, check=True, capture_output=True)

        self.git_f1 = {
            "path": repo_dir,
            "repo_id": repo_id,
            "worktree_id": worktree_id,
            "git_exe": self.git_exe,
            "r0_oid": r0_oid,
            "m2_oid": m2_oid,
            "x2_oid": x2_oid
        }


        # ====================================================================
        # Fixture: GIT-F5 (Rename/Delete and Binary Conflicts)
        # ====================================================================
        #
        # GIT-F5: Rename/delete and binary conflicts.
        #
        # Main branch:
        # - R0: file1.txt, file2.txt, binary.dat
        # - M1: rename file1.txt -> file1_renamed.txt
        # - M2: modify binary.dat
        #
        # Branch feature (from R0):
        # - F1: modify file1.txt (will conflict with rename)
        # - F2: delete file2.txt (will conflict if main modifies)
        # - F3: modify binary.dat differently (binary conflict)
        repo_dir = self.tmp_path / "git_f5"
        repo_dir.mkdir()

        init_result = init_repository(
            repo_dir,
            self.git_exe,
            initial_branch="main",
            author_name="Fixture Researcher",
            author_email="fixture@example.invalid"
        )

        repo_id = init_result["repo_id"]
        worktree_id = init_result["worktree_id"]

        # R0: Initial commit
        (repo_dir / "file1.txt").write_text("Content 1\n", encoding='utf-8')
        (repo_dir / "file2.txt").write_text("Content 2\n", encoding='utf-8')
        (repo_dir / "binary.dat").write_bytes(b'\x00\x01\x02\x03')

        subprocess.run([self.git_exe, "add", "."], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            [self.git_exe, "commit", "-m", "R0: Initial commit"],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )

        result = subprocess.run(
            [self.git_exe, "rev-parse", "HEAD"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True
        )
        r0_oid = result.stdout.strip()

        # M1: Rename file1.txt -> file1_renamed.txt
        subprocess.run(
            [self.git_exe, "mv", "file1.txt", "file1_renamed.txt"],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )
        subprocess.run(
            [self.git_exe, "commit", "-m", "M1: Rename file1"],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )

        # M2: Delete file2.txt and modify binary
        (repo_dir / "file2.txt").unlink()
        (repo_dir / "binary.dat").write_bytes(b'\x00\x01\x02\x03\x04\x05')
        subprocess.run([self.git_exe, "add", "-A"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            [self.git_exe, "commit", "-m", "M2: Delete file2 and modify binary"],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )

        result = subprocess.run(
            [self.git_exe, "rev-parse", "HEAD"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True
        )
        m2_oid = result.stdout.strip()

        # Create feature branch from R0
        subprocess.run(
            [self.git_exe, "checkout", "-b", "feature", r0_oid],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )

        # F1: Modify file1.txt (will conflict with rename)
        (repo_dir / "file1.txt").write_text("Content 1 modified\n", encoding='utf-8')
        subprocess.run([self.git_exe, "add", "file1.txt"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            [self.git_exe, "commit", "-m", "F1: Modify file1"],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )

        # F2: Modify file2.txt (will conflict with delete)
        (repo_dir / "file2.txt").write_text("Content 2 modified\n", encoding='utf-8')
        subprocess.run([self.git_exe, "add", "file2.txt"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            [self.git_exe, "commit", "-m", "F2: Modify file2"],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )

        # F3: Modify binary differently
        (repo_dir / "binary.dat").write_bytes(b'\xFF\xFE\xFD\xFC')
        subprocess.run([self.git_exe, "add", "binary.dat"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            [self.git_exe, "commit", "-m", "F3: Modify binary differently"],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )

        result = subprocess.run(
            [self.git_exe, "rev-parse", "HEAD"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True
        )
        f3_oid = result.stdout.strip()

        # Switch back to main
        subprocess.run([self.git_exe, "checkout", "main"], cwd=repo_dir, check=True, capture_output=True)

        self.git_f5 = {
            "path": repo_dir,
            "repo_id": repo_id,
            "worktree_id": worktree_id,
            "git_exe": self.git_exe,
            "r0_oid": r0_oid,
            "m2_oid": m2_oid,
            "f3_oid": f3_oid
        }

    def tearDown(self):
        self._tmp.cleanup()


    # ========================================================================
    # AT-38: GIT-F1 Conflict Resolution
    # ========================================================================

    def test_at38_conflict_merge_complete(self):
        """AT-38: GIT-F1 conflict resolution and merge commit creation.

        Verify:
        - M3 tree content matches manual resolution
        - Parents are accurate [M2, X2]
        - All files are resolved
        """
        repo_dir = self.git_f1["path"]
        repo_id = self.git_f1["repo_id"]
        worktree_id = self.git_f1["worktree_id"]
        git_exe = self.git_f1["git_exe"]
        m2_oid = self.git_f1["m2_oid"]
        x2_oid = self.git_f1["x2_oid"]

        # Analyze merge
        analysis = analyze_merge(repo_dir, git_exe, "main", "experiment")
        assert analysis.merge_type == MergeType.THREE_WAY
        assert analysis.target_oid == m2_oid
        assert analysis.source_oid == x2_oid
        assert analysis.merge_base_oid == self.git_f1["r0_oid"]

        # Start merge
        merge_state, receipt = start_merge(
            repo_dir,
            self.state_root,
            git_exe,
            repo_id,
            worktree_id,
            "experiment",
            self.lock_dir,
            no_ff=True
        )

        assert receipt is None  # Should need user resolution
        assert merge_state.status == MergeStatus.AWAITING_RESOLUTION
        assert len(merge_state.conflicts) == 1

        # Check conflict
        conflict = merge_state.conflicts[0]
        assert conflict.path == "config.txt"
        assert conflict.conflict_type == ConflictType.TEXT
        assert not conflict.is_binary
        assert not conflict.resolved

        # Resolve conflict manually
        resolution_content = b"value=merged\n"
        merge_state = resolve_conflict(
            repo_dir,
            self.state_root,
            git_exe,
            merge_state.operation_id,
            "config.txt",
            "manual",
            resolution_content
        )

        assert merge_state.conflicts[0].resolved
        assert merge_state.conflicts[0].resolution_choice == "manual"
        assert check_all_conflicts_resolved(merge_state)

        # Complete merge
        receipt = complete_merge(
            repo_dir,
            self.state_root,
            git_exe,
            repo_id,
            worktree_id,
            merge_state.operation_id,
            self.lock_dir,
            message="Merge experiment into main"
        )

        assert receipt.result["status"] == "completed"
        m3_oid = receipt.result["merge_commit_oid"]
        parents = receipt.result["parents"]

        # Verify parents
        assert len(parents) == 2
        assert parents[0] == m2_oid
        assert parents[1] == x2_oid

        # Verify tree content
        config_content = (repo_dir / "config.txt").read_bytes()
        assert config_content == resolution_content

        # Verify all files are in tree
        assert (repo_dir / "README.md").exists()
        assert (repo_dir / "config.txt").exists()
        assert (repo_dir / "experiment.txt").exists()
        assert (repo_dir / "assets" / "sample.bin").exists()


    def test_at38_resolve_with_ours(self):
        """AT-38: Resolve conflict by choosing 'ours'."""
        repo_dir = self.git_f1["path"]
        repo_id = self.git_f1["repo_id"]
        worktree_id = self.git_f1["worktree_id"]
        git_exe = self.git_f1["git_exe"]

        merge_state, _ = start_merge(
            repo_dir, self.state_root, git_exe, repo_id, worktree_id,
            "experiment", self.lock_dir, no_ff=True
        )

        # Resolve with ours
        merge_state = resolve_conflict(
            repo_dir, self.state_root, git_exe,
            merge_state.operation_id,
            "config.txt",
            "ours"
        )

        assert merge_state.conflicts[0].resolved
        assert merge_state.conflicts[0].resolution_choice == "ours"

        # Verify content
        content = (repo_dir / "config.txt").read_text(encoding='utf-8')
        assert content == "value=main\n"


    def test_at38_resolve_with_theirs(self):
        """AT-38: Resolve conflict by choosing 'theirs'."""
        repo_dir = self.git_f1["path"]
        repo_id = self.git_f1["repo_id"]
        worktree_id = self.git_f1["worktree_id"]
        git_exe = self.git_f1["git_exe"]

        merge_state, _ = start_merge(
            repo_dir, self.state_root, git_exe, repo_id, worktree_id,
            "experiment", self.lock_dir, no_ff=True
        )

        # Resolve with theirs
        merge_state = resolve_conflict(
            repo_dir, self.state_root, git_exe,
            merge_state.operation_id,
            "config.txt",
            "theirs"
        )

        assert merge_state.conflicts[0].resolved
        assert merge_state.conflicts[0].resolution_choice == "theirs"

        # Verify content
        content = (repo_dir / "config.txt").read_text(encoding='utf-8')
        assert content == "value=experiment\n"


    # ========================================================================
    # AT-39: No-Conflict Divergent Merge Preview
    # ========================================================================

    def test_at39_no_conflict_preview(self):
        """AT-39: No-conflict divergent merge stops at preview.

        Verify:
        - Merge stops at preview (no automatic commit)
        - User must click "complete" to create merge commit
        """
        repo_dir = self.tmp_path / "no_conflict_repo"
        repo_dir.mkdir()

        init_result = init_repository(
            repo_dir, self.git_exe, initial_branch="main",
            author_name="Test User", author_email="test@example.com"
        )

        repo_id = init_result["repo_id"]
        worktree_id = init_result["worktree_id"]

        # Create initial commit
        (repo_dir / "file.txt").write_text("initial\n", encoding='utf-8')
        subprocess.run([self.git_exe, "add", "."], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run([self.git_exe, "commit", "-m", "Initial"], cwd=repo_dir, check=True, capture_output=True)

        result = subprocess.run(
            [self.git_exe, "rev-parse", "HEAD"],
            cwd=repo_dir, check=True, capture_output=True, text=True
        )
        initial_oid = result.stdout.strip()

        # Main: add file1
        (repo_dir / "file1.txt").write_text("main content\n", encoding='utf-8')
        subprocess.run([self.git_exe, "add", "file1.txt"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run([self.git_exe, "commit", "-m", "Add file1"], cwd=repo_dir, check=True, capture_output=True)

        result = subprocess.run(
            [self.git_exe, "rev-parse", "HEAD"],
            cwd=repo_dir, check=True, capture_output=True, text=True
        )
        main_oid = result.stdout.strip()

        # Feature: add file2 (no conflict)
        subprocess.run(
            [self.git_exe, "checkout", "-b", "feature", initial_oid],
            cwd=repo_dir, check=True, capture_output=True
        )
        (repo_dir / "file2.txt").write_text("feature content\n", encoding='utf-8')
        subprocess.run([self.git_exe, "add", "file2.txt"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run([self.git_exe, "commit", "-m", "Add file2"], cwd=repo_dir, check=True, capture_output=True)

        result = subprocess.run(
            [self.git_exe, "rev-parse", "HEAD"],
            cwd=repo_dir, check=True, capture_output=True, text=True
        )
        feature_oid = result.stdout.strip()

        # Switch back to main
        subprocess.run([self.git_exe, "checkout", "main"], cwd=repo_dir, check=True, capture_output=True)

        # Start merge with --no-ff (force merge commit)
        merge_state, receipt = start_merge(
            repo_dir, self.state_root, self.git_exe, repo_id, worktree_id,
            "feature", self.lock_dir, no_ff=True
        )

        # Should stop at preview (no receipt yet)
        assert receipt is None
        assert merge_state.status == MergeStatus.IN_PROGRESS
        assert len(merge_state.conflicts) == 0  # No conflicts

        # Preview result
        preview = preview_merge_result(repo_dir, self.git_exe, merge_state)
        assert preview["conflicts_resolved"] == 0
        assert preview["total_conflicts"] == 0

        # Verify no merge commit yet
        head = get_head_info(repo_dir, self.git_exe)
        assert head.oid == main_oid  # Still at main

        # Complete merge (user action)
        receipt = complete_merge(
            repo_dir, self.state_root, self.git_exe, repo_id, worktree_id,
            merge_state.operation_id, self.lock_dir
        )

        assert receipt.result["status"] == "completed"
        merge_oid = receipt.result["merge_commit_oid"]
        parents = receipt.result["parents"]

        # Verify merge commit created
        assert len(parents) == 2
        assert parents[0] == main_oid
        assert parents[1] == feature_oid

        # Verify both files present
        assert (repo_dir / "file1.txt").exists()
        assert (repo_dir / "file2.txt").exists()


    # ========================================================================
    # AT-43: Rename/Delete and Binary Conflicts
    # ========================================================================

    def test_at43_binary_conflict_explicit_choice(self):
        """AT-43: Binary conflict requires explicit choice.

        Verify:
        - Binary conflicts detected
        - Must explicitly choose ours/theirs/upload
        - No fake "auto-merge success"
        """
        repo_dir = self.git_f5["path"]
        repo_id = self.git_f5["repo_id"]
        worktree_id = self.git_f5["worktree_id"]
        git_exe = self.git_f5["git_exe"]

        # Start merge
        merge_state, receipt = start_merge(
            repo_dir, self.state_root, git_exe, repo_id, worktree_id,
            "feature", self.lock_dir, no_ff=True
        )

        assert receipt is None
        assert merge_state.status == MergeStatus.AWAITING_RESOLUTION
        assert len(merge_state.conflicts) > 0

        # Find binary conflict
        binary_conflicts = [c for c in merge_state.conflicts if c.is_binary]
        assert len(binary_conflicts) > 0

        binary_conflict = binary_conflicts[0]
        assert binary_conflict.path == "binary.dat"
        assert not binary_conflict.resolved

        # Resolve binary conflict with explicit choice (ours)
        merge_state = resolve_conflict(
            repo_dir, self.state_root, git_exe,
            merge_state.operation_id,
            "binary.dat",
            "ours"
        )

        # Check the returned merge_state
        resolved_conflict = next(c for c in merge_state.conflicts if c.path == "binary.dat")
        assert resolved_conflict.resolved

        # Verify content is from ours
        content = (repo_dir / "binary.dat").read_bytes()
        assert content == b'\x00\x01\x02\x03\x04\x05'  # Main version


    def test_at43_delete_modify_conflict(self):
        """AT-43: Delete/modify conflict requires explicit choice."""
        repo_dir = self.git_f5["path"]
        repo_id = self.git_f5["repo_id"]
        worktree_id = self.git_f5["worktree_id"]
        git_exe = self.git_f5["git_exe"]

        merge_state, _ = start_merge(
            repo_dir, self.state_root, git_exe, repo_id, worktree_id,
            "feature", self.lock_dir, no_ff=True
        )

        # Find delete/modify conflict
        delete_conflicts = [
            c for c in merge_state.conflicts
            if c.conflict_type in (ConflictType.MODIFY_DELETE, ConflictType.DELETE_MODIFY)
        ]
        assert len(delete_conflicts) > 0

        delete_conflict = delete_conflicts[0]
        assert delete_conflict.path == "file2.txt"

        # Must explicitly choose to delete or keep
        merge_state = resolve_conflict(
            repo_dir, self.state_root, git_exe,
            merge_state.operation_id,
            "file2.txt",
            "delete"
        )

        # Check the returned merge_state
        resolved_conflict = next(c for c in merge_state.conflicts if c.path == "file2.txt")
        assert resolved_conflict.resolved
        assert not (repo_dir / "file2.txt").exists()


    # ========================================================================
    # AT-44: Abort and Recovery
    # ========================================================================

    def test_at44_abort_merge(self):
        """AT-44: Cancel merge restores HEAD and index.

        Verify:
        - git merge --abort called
        - HEAD restored to pre-merge state
        - Index clean
        - Working tree restored
        """
        repo_dir = self.git_f1["path"]
        repo_id = self.git_f1["repo_id"]
        worktree_id = self.git_f1["worktree_id"]
        git_exe = self.git_f1["git_exe"]
        m2_oid = self.git_f1["m2_oid"]

        # Get pre-merge state
        pre_head = get_head_info(repo_dir, git_exe)
        assert pre_head.oid == m2_oid

        pre_head_status, pre_files, pre_ongoing = get_status(repo_dir, git_exe)
        assert len(pre_files) == 0  # Clean working tree

        # Start merge
        merge_state, _ = start_merge(
            repo_dir, self.state_root, git_exe, repo_id, worktree_id,
            "experiment", self.lock_dir, no_ff=True
        )

        operation_id = merge_state.operation_id

        # Verify merge is in progress
        assert merge_state.status == MergeStatus.AWAITING_RESOLUTION
        assert (repo_dir / ".git" / "MERGE_HEAD").exists()

        # Abort merge
        receipt = abort_merge(
            repo_dir, self.state_root, git_exe, repo_id, worktree_id,
            operation_id, self.lock_dir
        )

        assert receipt.result["status"] == "aborted"
        assert receipt.result["restored_head"] == m2_oid

        # Verify HEAD restored
        post_head = get_head_info(repo_dir, git_exe)
        assert post_head.oid == m2_oid
        assert post_head.branch == "main"

        # Verify no MERGE_HEAD
        assert not (repo_dir / ".git" / "MERGE_HEAD").exists()

        # Verify index clean
        result = subprocess.run(
            [git_exe, "ls-files", "-u"],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        assert not result.stdout.strip()

        # Verify working tree matches HEAD
        post_head_status, post_files, post_ongoing = get_status(repo_dir, git_exe)
        # Should be clean or match pre-merge state
        assert len(post_files) == 0 or len(post_files) == len(pre_files)


    def test_at44_recover_after_restart(self):
        """AT-44: Restart recovers in-progress merge state.

        Verify:
        - Merge state persisted to disk
        - Restart detects in-progress merge
        - Conflicts refreshed
        - Can continue or abort
        """
        repo_dir = self.git_f1["path"]
        repo_id = self.git_f1["repo_id"]
        worktree_id = self.git_f1["worktree_id"]
        git_exe = self.git_f1["git_exe"]

        # Start merge
        merge_state, _ = start_merge(
            repo_dir, self.state_root, git_exe, repo_id, worktree_id,
            "experiment", self.lock_dir, no_ff=True
        )

        operation_id = merge_state.operation_id
        assert merge_state.status == MergeStatus.AWAITING_RESOLUTION

        # Simulate restart - get active merge state
        recovered_state = get_active_merge_state(
            repo_dir, self.state_root, git_exe, repo_id
        )

        assert recovered_state is not None
        assert recovered_state.operation_id == operation_id
        assert recovered_state.status == MergeStatus.AWAITING_RESOLUTION
        assert len(recovered_state.conflicts) == 1

        # Can continue resolving
        resolve_conflict(
            repo_dir, self.state_root, git_exe,
            recovered_state.operation_id,
            "config.txt",
            "manual",
            b"value=resolved\n"
        )

        # Complete merge
        receipt = complete_merge(
            repo_dir, self.state_root, git_exe, repo_id, worktree_id,
            recovered_state.operation_id, self.lock_dir
        )

        assert receipt.result["status"] == "completed"


    def test_at44_fast_forward_no_recovery_needed(self):
        """AT-44: Fast-forward merge creates recovery point but completes immediately."""
        repo_dir = self.tmp_path / "ff_repo"
        repo_dir.mkdir()

        init_result = init_repository(
            repo_dir, self.git_exe, initial_branch="main",
            author_name="Test User", author_email="test@example.com"
        )

        repo_id = init_result["repo_id"]
        worktree_id = init_result["worktree_id"]

        # Create initial commit
        (repo_dir / "file.txt").write_text("initial\n", encoding='utf-8')
        subprocess.run([self.git_exe, "add", "."], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run([self.git_exe, "commit", "-m", "Initial"], cwd=repo_dir, check=True, capture_output=True)

        result = subprocess.run(
            [self.git_exe, "rev-parse", "HEAD"],
            cwd=repo_dir, check=True, capture_output=True, text=True
        )
        initial_oid = result.stdout.strip()

        # Create feature branch with new commit
        subprocess.run([self.git_exe, "checkout", "-b", "feature"], cwd=repo_dir, check=True, capture_output=True)
        (repo_dir / "feature.txt").write_text("feature\n", encoding='utf-8')
        subprocess.run([self.git_exe, "add", "."], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run([self.git_exe, "commit", "-m", "Feature"], cwd=repo_dir, check=True, capture_output=True)

        result = subprocess.run(
            [self.git_exe, "rev-parse", "HEAD"],
            cwd=repo_dir, check=True, capture_output=True, text=True
        )
        feature_oid = result.stdout.strip()

        # Switch back to main
        subprocess.run([self.git_exe, "checkout", "main"], cwd=repo_dir, check=True, capture_output=True)

        # Merge (should fast-forward)
        merge_state, receipt = start_merge(
            repo_dir, self.state_root, self.git_exe, repo_id, worktree_id,
            "feature", self.lock_dir
        )

        # Should complete immediately with fast-forward
        assert receipt is not None
        assert receipt.result["status"] == "fast_forward"
        assert receipt.recovery_id is not None  # Recovery point created
        assert merge_state.status == MergeStatus.COMPLETED

        # Verify HEAD moved
        head = get_head_info(repo_dir, self.git_exe)
        assert head.oid == feature_oid


if __name__ == "__main__":
    unittest.main()
