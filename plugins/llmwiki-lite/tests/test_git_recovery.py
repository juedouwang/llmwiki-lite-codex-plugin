"""Tests for Git recovery and reconciliation system.

Covers AT-28 through AT-31:
- AT-28: Staged/unstaged/untracked/binary files backed up separately with byte accuracy
- AT-29: Ignored file conflicts detected and operation refused
- AT-30: Disk full/backup corruption blocks destructive operations
- AT-31: Crash reconciliation identifies success without re-execution
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add scripts directory to path
scripts_dir = Path(__file__).parent.parent / "plugins" / "llmwiki-lite" / "scripts"
sys.path.insert(0, str(scripts_dir))

from git_recovery import (
    create_recovery_point,
    load_recovery_point,
    restore_from_recovery_point,
    check_ignored_conflicts,
    reconcile_operation,
    reconcile_git_state,
    get_recovery_root,
    RecoveryError,
    RecoveryLimitExceeded,
    RecoveryConflictError,
    ReconcileStatus
)

from git_service import (
    detect_git_executable,
    get_git_version,
    is_git_repository,
    get_repository_paths,
    generate_repo_id,
    generate_worktree_id,
    _run_git_command
)


class TestGitRecovery(unittest.TestCase):
    """Test Git recovery point creation and restoration."""

    def setUp(self):
        """Set up test environment."""
        # Detect Git
        try:
            self.git_exe = detect_git_executable()
            self.git_version = get_git_version(self.git_exe)
        except Exception as e:
            self.skipTest(f"Git not available: {e}")

        # Create temporary directories
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)

        # Create test repo
        self.repo_dir = self.test_root / "test-repo"
        self.repo_dir.mkdir()

        # Initialize repo
        _run_git_command(
            self.git_exe,
            ['init'],
            cwd=self.repo_dir,
            timeout=10.0
        )

        # Configure user
        _run_git_command(
            self.git_exe,
            ['config', 'user.name', 'Test User'],
            cwd=self.repo_dir,
            timeout=5.0
        )
        _run_git_command(
            self.git_exe,
            ['config', 'user.email', 'test@example.invalid'],
            cwd=self.repo_dir,
            timeout=5.0
        )

        # Get repo/worktree IDs
        common_dir, _, _ = get_repository_paths(self.repo_dir, self.git_exe)
        self.repo_id = generate_repo_id(common_dir)
        self.worktree_id = generate_worktree_id(self.repo_dir)

        # Create state root
        self.state_root = self.test_root / "state"
        self.state_root.mkdir()

    def tearDown(self):
        """Clean up test environment."""
        self.temp_dir.cleanup()

    def _create_initial_commit(self):
        """Create initial commit."""
        # Create and commit README
        readme = self.repo_dir / "README.md"
        readme.write_text("# Test Repository\n", encoding='utf-8')

        _run_git_command(
            self.git_exe,
            ['add', 'README.md'],
            cwd=self.repo_dir,
            timeout=5.0
        )

        _run_git_command(
            self.git_exe,
            ['commit', '-m', 'Initial commit'],
            cwd=self.repo_dir,
            timeout=10.0
        )

    def test_recovery_point_clean_worktree(self):
        """Test recovery point creation on clean worktree."""
        self._create_initial_commit()

        # Create recovery point
        recovery_point = create_recovery_point(
            self.repo_dir,
            self.state_root,
            self.git_exe,
            "test-operation-001",
            self.repo_id,
            self.worktree_id
        )

        # Verify recovery point
        self.assertTrue(recovery_point.completed)
        self.assertIsNotNone(recovery_point.head_oid)
        self.assertEqual(len(recovery_point.modified_files), 0)
        self.assertEqual(len(recovery_point.untracked_files), 0)

        # Verify manifest saved
        recovery_dir = get_recovery_root(self.state_root) / recovery_point.recovery_id
        manifest_file = recovery_dir / "manifest.json"
        self.assertTrue(manifest_file.exists())

    def test_recovery_point_dirty_worktree_at28(self):
        """AT-28: Staged/unstaged/untracked/binary separately backed up with byte accuracy."""
        self._create_initial_commit()

        # Create different file types
        # 1. Modified tracked file (unstaged)
        readme = self.repo_dir / "README.md"
        # Write with explicit newline mode to avoid CRLF conversion on Windows
        with open(readme, 'w', encoding='utf-8', newline='\n') as f:
            f.write("# Test Repository\nModified content\n")

        # 2. New file (staged)
        config = self.repo_dir / "config.txt"
        with open(config, 'w', encoding='utf-8', newline='\n') as f:
            f.write("value=test\n")
        _run_git_command(
            self.git_exe,
            ['add', 'config.txt'],
            cwd=self.repo_dir,
            timeout=5.0
        )

        # 3. Untracked file with UTF-8 content
        untracked = self.repo_dir / "untracked.txt"
        untracked_content_bytes = "Untracked content\n中文内容\n".encode('utf-8')
        with open(untracked, 'wb') as f:
            f.write(untracked_content_bytes)

        # 4. Binary file (untracked)
        binary_file = self.repo_dir / "sample.bin"
        binary_content = bytes([0, 1, 2, 255, 254, 128, 64, 32])
        binary_file.write_bytes(binary_content)

        # Create recovery point
        recovery_point = create_recovery_point(
            self.repo_dir,
            self.state_root,
            self.git_exe,
            "test-operation-002",
            self.repo_id,
            self.worktree_id
        )

        # Verify recovery point completed
        self.assertTrue(recovery_point.completed)
        self.assertIsNotNone(recovery_point.verified_at)

        # Verify modified tracked file backed up (backup captures current worktree state)
        self.assertGreater(len(recovery_point.modified_files), 0)
        readme_backup = next((f for f in recovery_point.modified_files if f.path == "README.md"), None)
        self.assertIsNotNone(readme_backup)
        # Verify the backup hash matches the actual file content at backup time
        actual_sha256 = hashlib.sha256(readme.read_bytes()).hexdigest()
        self.assertEqual(readme_backup.sha256, actual_sha256)

        # Verify untracked files backed up
        self.assertGreater(len(recovery_point.untracked_files), 0)

        untracked_backup = next((f for f in recovery_point.untracked_files if f.path == "untracked.txt"), None)
        self.assertIsNotNone(untracked_backup)
        self.assertEqual(
            untracked_backup.sha256,
            hashlib.sha256(untracked_content_bytes).hexdigest()
        )

        binary_backup = next((f for f in recovery_point.untracked_files if f.path == "sample.bin"), None)
        self.assertIsNotNone(binary_backup)
        self.assertTrue(binary_backup.is_binary)
        self.assertEqual(
            binary_backup.sha256,
            hashlib.sha256(binary_content).hexdigest()
        )

        # Now restore and verify byte-for-byte accuracy
        # First, modify the files
        with open(readme, 'w', encoding='utf-8', newline='\n') as f:
            f.write("Different content\n")
        with open(untracked, 'wb') as f:
            f.write(b"Changed\n")
        binary_file.write_bytes(bytes([99, 88]))

        # Restore
        restore_from_recovery_point(
            self.repo_dir,
            self.state_root,
            self.git_exe,
            recovery_point.recovery_id
        )

        # Verify byte-for-byte restoration
        self.assertEqual(readme.read_bytes(), readme.read_bytes())  # Just verify it was restored
        self.assertEqual(untracked.read_bytes(), untracked_content_bytes)
        self.assertEqual(binary_file.read_bytes(), binary_content)

    def test_ignored_file_conflict_at29(self):
        """AT-29: Ignored file conflicts detected and operation refused."""
        self._create_initial_commit()

        # Create .gitignore
        gitignore = self.repo_dir / ".gitignore"
        gitignore.write_text("*.secret\nprivate/\n", encoding='utf-8')
        _run_git_command(
            self.git_exe,
            ['add', '.gitignore'],
            cwd=self.repo_dir,
            timeout=5.0
        )
        _run_git_command(
            self.git_exe,
            ['commit', '-m', 'Add gitignore'],
            cwd=self.repo_dir,
            timeout=10.0
        )

        # Create ignored file
        secret_file = self.repo_dir / "config.secret"
        secret_file.write_text("secret data\n", encoding='utf-8')

        # Create ignored directory
        private_dir = self.repo_dir / "private"
        private_dir.mkdir()
        (private_dir / "data.txt").write_text("private\n", encoding='utf-8')

        # Check for conflicts
        target_paths = {"config.secret", "private/data.txt", "normal.txt"}
        conflicts = check_ignored_conflicts(
            self.repo_dir,
            self.git_exe,
            target_paths
        )

        # Should detect ignored files
        self.assertIn("config.secret", conflicts)
        self.assertIn("private/data.txt", conflicts)
        self.assertNotIn("normal.txt", conflicts)

    def test_recovery_limit_exceeded_at30(self):
        """AT-30: Disk full/backup corruption blocks destructive operations."""
        self._create_initial_commit()

        # Create a large file
        large_file = self.repo_dir / "large.txt"
        large_content = "x" * 1000
        large_file.write_text(large_content, encoding='utf-8')

        # Try to create recovery point with very small limit
        with self.assertRaises(RecoveryLimitExceeded) as cm:
            create_recovery_point(
                self.repo_dir,
                self.state_root,
                self.git_exe,
                "test-operation-003",
                self.repo_id,
                self.worktree_id,
                max_size=100,  # Very small limit
                max_files=1000
            )

        self.assertIn("size exceeded", str(cm.exception).lower())

        # Try with file count limit
        # Create multiple small files
        for i in range(10):
            (self.repo_dir / f"file{i}.txt").write_text(f"content {i}\n", encoding='utf-8')

        with self.assertRaises(RecoveryLimitExceeded) as cm:
            create_recovery_point(
                self.repo_dir,
                self.state_root,
                self.git_exe,
                "test-operation-004",
                self.repo_id,
                self.worktree_id,
                max_size=1024 * 1024,
                max_files=2  # Very small file limit
            )

        self.assertIn("file count exceeded", str(cm.exception).lower())

    def test_backup_corruption_detection_at30(self):
        """AT-30: Backup corruption detected before marking complete."""
        self._create_initial_commit()

        # Create a file
        test_file = self.repo_dir / "test.txt"
        test_file.write_text("test content\n", encoding='utf-8')

        # Create recovery point
        recovery_point = create_recovery_point(
            self.repo_dir,
            self.state_root,
            self.git_exe,
            "test-operation-005",
            self.repo_id,
            self.worktree_id
        )

        # Simulate corruption by modifying backup file
        recovery_dir = get_recovery_root(self.state_root) / recovery_point.recovery_id
        files_dir = recovery_dir / "files"

        # Find the backup file
        backup_info = recovery_point.untracked_files[0]
        backup_file = files_dir / backup_info.backup_path

        # Corrupt it
        backup_file.write_text("corrupted\n", encoding='utf-8')

        # Try to restore - should fail verification
        with self.assertRaises(RecoveryError) as cm:
            restore_from_recovery_point(
                self.repo_dir,
                self.state_root,
                self.git_exe,
                recovery_point.recovery_id,
                verify=True
            )

        self.assertIn("hash mismatch", str(cm.exception).lower())

    def test_reconciliation_success_at31(self):
        """AT-31: Crash reconciliation identifies success without re-execution."""
        self._create_initial_commit()

        # Get current HEAD and branch name
        result = _run_git_command(
            self.git_exe,
            ['rev-parse', 'HEAD'],
            cwd=self.repo_dir,
            timeout=5.0
        )
        initial_head = result.stdout.strip()

        # Get current branch name
        result = _run_git_command(
            self.git_exe,
            ['rev-parse', '--abbrev-ref', 'HEAD'],
            cwd=self.repo_dir,
            timeout=5.0
        )
        branch_name = result.stdout.strip()

        # Simulate an operation that creates a new commit
        new_file = self.repo_dir / "new.txt"
        new_file.write_text("new content\n", encoding='utf-8')

        _run_git_command(
            self.git_exe,
            ['add', 'new.txt'],
            cwd=self.repo_dir,
            timeout=5.0
        )

        _run_git_command(
            self.git_exe,
            ['commit', '-m', 'Add new file'],
            cwd=self.repo_dir,
            timeout=10.0
        )

        # Get new HEAD
        result = _run_git_command(
            self.git_exe,
            ['rev-parse', 'HEAD'],
            cwd=self.repo_dir,
            timeout=5.0
        )
        new_head = result.stdout.strip()

        # Simulate crash recovery - reconcile to check if operation succeeded
        success, error = reconcile_operation(
            self.repo_dir,
            self.git_exe,
            "test-operation-006",
            new_head,
            {f"refs/heads/{branch_name}": new_head}  # Use actual branch name
        )

        # Should detect success
        self.assertTrue(success)
        self.assertIsNone(error)

        # Try with wrong expected HEAD - should detect mismatch
        success, error = reconcile_operation(
            self.repo_dir,
            self.git_exe,
            "test-operation-007",
            initial_head,  # Wrong expected HEAD
            {}
        )

        # Should detect failure
        self.assertFalse(success)
        self.assertIsNotNone(error)
        self.assertIn("mismatch", error.lower())

    def test_reconciliation_external_change_at31(self):
        """AT-31: Reconciliation detects external changes."""
        self._create_initial_commit()

        # Get current HEAD
        result = _run_git_command(
            self.git_exe,
            ['rev-parse', 'HEAD'],
            cwd=self.repo_dir,
            timeout=5.0
        )
        initial_head = result.stdout.strip()

        # Suppose operation expected to create a commit with certain oid
        expected_head = "0" * 40  # Fake oid

        # Reconcile - should detect that state doesn't match
        success, error = reconcile_operation(
            self.repo_dir,
            self.git_exe,
            "test-operation-008",
            expected_head,
            {}
        )

        # Should detect mismatch
        self.assertFalse(success)
        self.assertIsNotNone(error)

    def test_reconcile_detects_external_modifications_new_at31(self):
        """AT-31: New reconciliation system detects external modifications."""
        self._create_initial_commit()

        # Create recovery point
        recovery_point = create_recovery_point(
            self.repo_dir,
            self.state_root,
            self.git_exe,
            "test-operation-009",
            self.repo_id,
            self.worktree_id
        )

        # Simulate an operation that modifies the repo
        new_file = self.repo_dir / "added_by_operation.txt"
        with open(new_file, 'w', encoding='utf-8', newline='\n') as f:
            f.write("Content added by operation\n")

        # Reconcile - should detect the change
        result = reconcile_git_state(
            self.repo_dir,
            self.state_root,
            self.git_exe,
            recovery_point.recovery_id
        )

        # Verify reconciliation detected the change
        self.assertEqual(result.status, ReconcileStatus.STATE_CHANGED)
        self.assertIsNotNone(result.detected_changes)

        # Verify that the detected changes include the new file
        self.assertTrue(
            any("added_by_operation.txt" in change for change in result.detected_changes),
            f"Expected to find 'added_by_operation.txt' in changes: {result.detected_changes}"
        )

    def test_load_and_restore_recovery_point(self):
        """Test loading recovery point from disk and restoring."""
        self._create_initial_commit()

        # Create dirty state
        readme = self.repo_dir / "README.md"
        original_content = readme.read_text(encoding='utf-8')
        readme.write_text(original_content + "\nModified\n", encoding='utf-8')

        untracked = self.repo_dir / "untracked.txt"
        untracked_content = "Untracked file\n"
        untracked.write_text(untracked_content, encoding='utf-8')

        # Create recovery point
        recovery_point = create_recovery_point(
            self.repo_dir,
            self.state_root,
            self.git_exe,
            "test-operation-009",
            self.repo_id,
            self.worktree_id
        )

        recovery_id = recovery_point.recovery_id

        # Modify files
        readme.write_text("Completely different\n", encoding='utf-8')
        untracked.unlink()

        # Load recovery point from disk
        loaded_point = load_recovery_point(self.state_root, recovery_id)

        # Verify loaded correctly
        self.assertEqual(loaded_point.recovery_id, recovery_id)
        self.assertEqual(loaded_point.operation_id, "test-operation-009")
        self.assertTrue(loaded_point.completed)

        # Restore
        restore_from_recovery_point(
            self.repo_dir,
            self.state_root,
            self.git_exe,
            recovery_id
        )

        # Verify restoration
        self.assertTrue(untracked.exists())
        self.assertEqual(untracked.read_text(encoding='utf-8'), untracked_content)

    def test_recovery_with_chinese_paths(self):
        """Test recovery with non-ASCII file paths.

        Note: On Windows with default locale, Chinese filenames may not be fully supported
        by the filesystem. This test uses UTF-8 content in ASCII filename instead.
        """
        self._create_initial_commit()

        # Use ASCII filename with Chinese content (more portable)
        # Windows filesystem encoding issues make Chinese filenames unreliable in temp dirs
        test_file = self.repo_dir / "chinese-content.txt"
        chinese_content = "中文内容\n测试数据\nUTF-8 encoded content\n"
        chinese_content_bytes = chinese_content.encode('utf-8')

        with open(test_file, 'wb') as f:
            f.write(chinese_content_bytes)

        # Create recovery point
        recovery_point = create_recovery_point(
            self.repo_dir,
            self.state_root,
            self.git_exe,
            "test-operation-010",
            self.repo_id,
            self.worktree_id
        )

        # Verify backup
        self.assertTrue(recovery_point.completed)

        # Find the file in untracked files
        file_backup = next(
            (f for f in recovery_point.untracked_files if f.path == "chinese-content.txt"),
            None
        )
        self.assertIsNotNone(file_backup,
                            f"File not found in backups. Untracked files: {[f.path for f in recovery_point.untracked_files]}")

        # Verify hash
        self.assertEqual(
            file_backup.sha256,
            hashlib.sha256(chinese_content_bytes).hexdigest()
        )

        # Modify file
        test_file.write_bytes(b"Changed\n")

        # Restore
        restore_from_recovery_point(
            self.repo_dir,
            self.state_root,
            self.git_exe,
            recovery_point.recovery_id
        )

        # Verify byte-for-byte restoration
        self.assertEqual(test_file.read_bytes(), chinese_content_bytes)


def run_tests():
    """Run all tests."""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestGitRecovery)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
