"""Git recovery point creation and restoration.

Security requirements (S-21):
- All destructive operations must create verifiable recovery points first
- Backup must include index, staged, unstaged, and untracked files
- Must detect and refuse operations that would overwrite ignored files
- Recovery points must be verifiable before marking as complete
- Reconciliation must detect external changes and not re-execute completed operations
"""

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Import from git_service
from git_service import (
    GitError,
    GitRepository,
    GitWorktree,
    GitHead,
    _run_git_command,
    get_head_info,
    get_repository_paths,
    generate_repo_id
)


# Default limits
DEFAULT_MAX_BACKUP_SIZE = 1024 * 1024 * 1024  # 1 GiB
DEFAULT_MAX_BACKUP_FILES = 20000


class RecoveryError(GitError):
    """Recovery operation error."""
    pass


class RecoveryLimitExceeded(RecoveryError):
    """Recovery backup size or file count limit exceeded."""
    pass


class RecoveryConflictError(RecoveryError):
    """Operation would conflict with existing files."""
    pass


@dataclass
class FileBackup:
    """Backed up file information."""
    path: str
    sha256: str
    size_bytes: int
    is_binary: bool
    backup_path: Optional[str] = None  # Relative to recovery root


@dataclass
class RecoveryPoint:
    """Git recovery point."""
    recovery_id: str
    operation_id: str
    repo_id: str
    worktree_id: str
    created_at: str

    # Git state
    head_oid: Optional[str] = None
    head_ref: Optional[str] = None
    head_unborn: bool = False
    git_version: Tuple[int, int, int] = (0, 0, 0)

    # Backed up content
    index_sha256: Optional[str] = None
    index_backup: Optional[str] = None
    staged_files: List[FileBackup] = field(default_factory=list)
    modified_files: List[FileBackup] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    untracked_files: List[FileBackup] = field(default_factory=list)

    # Refs
    recovery_ref: Optional[str] = None
    before_refs: Dict[str, str] = field(default_factory=dict)

    # Verification
    completed: bool = False
    verified_at: Optional[str] = None

    # Statistics
    total_size_bytes: int = 0
    total_file_count: int = 0


def generate_recovery_id(operation_id: str) -> str:
    """Generate recovery point ID.

    Args:
        operation_id: Operation ID

    Returns:
        Recovery ID (hex string)
    """
    # Use operation ID + timestamp for uniqueness
    content = f"{operation_id}\x00{time.time()}"
    hash_bytes = hashlib.sha256(content.encode('utf-8')).digest()
    return hash_bytes[:16].hex()


def get_recovery_root(state_root: Path) -> Path:
    """Get recovery points storage root.

    Args:
        state_root: Project state root

    Returns:
        Recovery root directory
    """
    return state_root / "workbench" / "git-recovery"


def get_recovery_path(state_root: Path, recovery_id: str) -> Path:
    """Get recovery point directory.

    Args:
        state_root: Project state root
        recovery_id: Recovery ID

    Returns:
        Recovery point directory path
    """
    return get_recovery_root(state_root) / recovery_id


def _compute_file_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of file.

    Args:
        file_path: File to hash

    Returns:
        SHA256 hex digest
    """
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def _is_binary_file(file_path: Path, sample_size: int = 8192) -> bool:
    """Check if file appears to be binary.

    Args:
        file_path: File to check
        sample_size: Number of bytes to sample

    Returns:
        True if file appears binary
    """
    try:
        with open(file_path, 'rb') as f:
            sample = f.read(sample_size)
            # Check for null bytes
            return b'\x00' in sample
    except (OSError, IOError):
        return True  # Treat unreadable as binary


def _backup_file(
    source_path: Path,
    backup_dir: Path,
    relative_path: str,
    limits: Dict[str, int]
) -> FileBackup:
    """Backup a single file.

    Args:
        source_path: Source file absolute path
        backup_dir: Backup directory root
        relative_path: Relative path for organizing backup
        limits: Limits dict with 'remaining_size' and 'remaining_files'

    Returns:
        FileBackup object

    Raises:
        RecoveryLimitExceeded: If limits exceeded
        RecoveryError: If backup fails
    """
    # Check file exists and get size
    try:
        stat = source_path.stat()
        file_size = stat.st_size
    except (OSError, IOError) as e:
        raise RecoveryError(f"Cannot read file {source_path}: {e}")

    # Check limits
    if limits['remaining_files'] <= 0:
        raise RecoveryLimitExceeded("Maximum file count exceeded")

    if limits['remaining_size'] < file_size:
        raise RecoveryLimitExceeded(f"Maximum backup size exceeded (need {file_size} bytes)")

    # Compute hash
    sha256 = _compute_file_sha256(source_path)
    is_binary = _is_binary_file(source_path)

    # Create backup path
    # Use SHA256 prefix to avoid path length issues and allow deduplication
    backup_subdir = backup_dir / sha256[:2]
    backup_subdir.mkdir(parents=True, exist_ok=True)
    backup_file_path = backup_subdir / sha256

    # Copy file if not already backed up
    if not backup_file_path.exists():
        try:
            shutil.copy2(source_path, backup_file_path)
        except (OSError, IOError) as e:
            raise RecoveryError(f"Failed to backup file {source_path}: {e}")

    # Update limits
    limits['remaining_size'] -= file_size
    limits['remaining_files'] -= 1

    # Return backup info with relative path from backup root
    backup_rel = str(backup_file_path.relative_to(backup_dir))

    return FileBackup(
        path=relative_path,
        sha256=sha256,
        size_bytes=file_size,
        is_binary=is_binary,
        backup_path=backup_rel
    )


def create_recovery_point(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    operation_id: str,
    repo_id: str,
    worktree_id: str,
    max_size: int = DEFAULT_MAX_BACKUP_SIZE,
    max_files: int = DEFAULT_MAX_BACKUP_FILES
) -> RecoveryPoint:
    """Create a recovery point before destructive operation.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable path
        operation_id: Operation ID
        repo_id: Repository ID
        worktree_id: Worktree ID
        max_size: Maximum backup size in bytes
        max_files: Maximum number of files to backup

    Returns:
        RecoveryPoint object

    Raises:
        RecoveryError: If recovery point creation fails
        RecoveryLimitExceeded: If backup limits exceeded
    """
    recovery_id = generate_recovery_id(operation_id)
    now = datetime.now(timezone.utc)
    created_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Get Git version
    try:
        from git_service import get_git_version
        git_version = get_git_version(git_exe)
    except Exception:
        git_version = (0, 0, 0)

    # Get HEAD info
    head = get_head_info(worktree_root, git_exe)

    # Create recovery directory
    recovery_dir = get_recovery_path(state_root, recovery_id)
    recovery_dir.mkdir(parents=True, exist_ok=True)

    # Create backup subdirectories
    files_dir = recovery_dir / "files"
    files_dir.mkdir(exist_ok=True)

    recovery_point = RecoveryPoint(
        recovery_id=recovery_id,
        operation_id=operation_id,
        repo_id=repo_id,
        worktree_id=worktree_id,
        created_at=created_at,
        head_oid=head.oid,
        head_ref=head.branch,
        head_unborn=head.unborn,
        git_version=git_version
    )

    # Tracking limits
    limits = {
        'remaining_size': max_size,
        'remaining_files': max_files
    }

    try:
        # 1. Backup index
        if not head.unborn:
            _backup_index(worktree_root, git_exe, recovery_dir, recovery_point)

        # 2. Get status and backup files
        _backup_worktree_files(
            worktree_root,
            git_exe,
            files_dir,
            recovery_point,
            limits
        )

        # 3. Create recovery ref (if HEAD exists)
        if head.oid and not head.unborn:
            ref_name = f"refs/llmwiki/recovery/{recovery_id}"
            try:
                _run_git_command(
                    git_exe,
                    ['update-ref', ref_name, head.oid],
                    cwd=worktree_root,
                    timeout=5.0
                )
                recovery_point.recovery_ref = ref_name
            except subprocess.SubprocessError as e:
                # Non-fatal, continue
                pass

        # 4. Save manifest (not yet complete)
        _save_recovery_manifest(recovery_dir, recovery_point, completed=False)

        # 5. Verify all backups
        _verify_recovery_point(recovery_dir, files_dir, recovery_point)

        # 6. Mark as completed
        recovery_point.completed = True
        recovery_point.verified_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _save_recovery_manifest(recovery_dir, recovery_point, completed=True)

    except Exception as e:
        # Cleanup on failure
        try:
            shutil.rmtree(recovery_dir, ignore_errors=True)
        except Exception:
            pass
        raise

    return recovery_point


def _backup_index(
    worktree_root: Path,
    git_exe: str,
    recovery_dir: Path,
    recovery_point: RecoveryPoint
) -> None:
    """Backup Git index file.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable
        recovery_dir: Recovery directory
        recovery_point: Recovery point to update

    Raises:
        RecoveryError: If index backup fails
    """
    # Get index path
    try:
        result = _run_git_command(
            git_exe,
            ['rev-parse', '--git-dir'],
            cwd=worktree_root,
            timeout=5.0
        )
        git_dir = Path(result.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = worktree_root / git_dir

        index_file = git_dir / "index"

        if not index_file.exists():
            return  # No index yet (unborn or empty)

        # Compute hash
        index_sha256 = _compute_file_sha256(index_file)

        # Backup index
        index_backup = recovery_dir / "index"
        shutil.copy2(index_file, index_backup)

        # Verify backup
        backup_sha256 = _compute_file_sha256(index_backup)
        if backup_sha256 != index_sha256:
            raise RecoveryError("Index backup verification failed")

        recovery_point.index_sha256 = index_sha256
        recovery_point.index_backup = "index"

    except subprocess.SubprocessError as e:
        raise RecoveryError(f"Failed to backup index: {e}")


def _backup_worktree_files(
    worktree_root: Path,
    git_exe: str,
    files_dir: Path,
    recovery_point: RecoveryPoint,
    limits: Dict[str, int]
) -> None:
    """Backup modified and untracked files.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable
        files_dir: Files backup directory
        recovery_point: Recovery point to update
        limits: Limits dict

    Raises:
        RecoveryError: If backup fails
        RecoveryLimitExceeded: If limits exceeded
    """
    # Get status
    try:
        result = _run_git_command(
            git_exe,
            [
                'status',
                '--porcelain=v2',
                '-z',
                '--untracked-files=all'
            ],
            cwd=worktree_root,
            timeout=30.0
        )
    except subprocess.SubprocessError as e:
        raise RecoveryError(f"Failed to get status: {e}")

    # Parse status
    entries = result.stdout.split('\0')

    for entry in entries:
        if not entry or entry.startswith('#'):
            continue

        # Parse ordinary changed entries
        # Format: 1 XY sub <mH> <mI> <mW> <hH> <hI> <path>
        if entry.startswith('1 '):
            parts = entry.split(' ')
            if len(parts) >= 9:
                xy = parts[1]
                path = ' '.join(parts[8:])

                index_status = xy[0] if xy[0] != '.' else ''
                worktree_status = xy[1] if xy[1] != '.' else ''

                file_path = worktree_root / path

                # Backup modified tracked files (worktree status not clean)
                if worktree_status in ('M', 'D', 'T'):
                    if worktree_status == 'D':
                        # File deleted in worktree
                        recovery_point.deleted_files.append(path)
                    else:
                        # File modified in worktree
                        if file_path.exists():
                            backup = _backup_file(file_path, files_dir, path, limits)
                            recovery_point.modified_files.append(backup)
                            recovery_point.total_size_bytes += backup.size_bytes
                            recovery_point.total_file_count += 1

        # Parse untracked entries
        # Format: ? <path>
        elif entry.startswith('? '):
            path = entry[2:]
            file_path = worktree_root / path

            if file_path.exists() and file_path.is_file():
                backup = _backup_file(file_path, files_dir, path, limits)
                recovery_point.untracked_files.append(backup)
                recovery_point.total_size_bytes += backup.size_bytes
                recovery_point.total_file_count += 1


def _verify_recovery_point(
    recovery_dir: Path,
    files_dir: Path,
    recovery_point: RecoveryPoint
) -> None:
    """Verify all backed up files.

    Args:
        recovery_dir: Recovery directory
        files_dir: Files backup directory
        recovery_point: Recovery point to verify

    Raises:
        RecoveryError: If verification fails
    """
    # Verify index backup
    if recovery_point.index_backup:
        index_path = recovery_dir / recovery_point.index_backup
        if not index_path.exists():
            raise RecoveryError("Index backup file missing")

        actual_sha256 = _compute_file_sha256(index_path)
        if actual_sha256 != recovery_point.index_sha256:
            raise RecoveryError("Index backup hash mismatch")

    # Verify all file backups
    all_backups = (
        recovery_point.staged_files +
        recovery_point.modified_files +
        recovery_point.untracked_files
    )

    for backup in all_backups:
        if not backup.backup_path:
            raise RecoveryError(f"File {backup.path} missing backup path")

        backup_file = files_dir / backup.backup_path
        if not backup_file.exists():
            raise RecoveryError(f"Backup file missing: {backup.path}")

        actual_sha256 = _compute_file_sha256(backup_file)
        if actual_sha256 != backup.sha256:
            raise RecoveryError(f"Backup hash mismatch: {backup.path}")


def _save_recovery_manifest(
    recovery_dir: Path,
    recovery_point: RecoveryPoint,
    completed: bool
) -> None:
    """Save recovery manifest.

    Args:
        recovery_dir: Recovery directory
        recovery_point: Recovery point
        completed: Whether backup is completed and verified
    """
    manifest = {
        "schema_version": 1,
        "recovery_id": recovery_point.recovery_id,
        "operation_id": recovery_point.operation_id,
        "repo_id": recovery_point.repo_id,
        "worktree_id": recovery_point.worktree_id,
        "created_at": recovery_point.created_at,
        "head_oid": recovery_point.head_oid,
        "head_ref": recovery_point.head_ref,
        "head_unborn": recovery_point.head_unborn,
        "git_version": list(recovery_point.git_version),
        "index_sha256": recovery_point.index_sha256,
        "index_backup": recovery_point.index_backup,
        "recovery_ref": recovery_point.recovery_ref,
        "staged_files": [
            {
                "path": f.path,
                "sha256": f.sha256,
                "size_bytes": f.size_bytes,
                "is_binary": f.is_binary,
                "backup_path": f.backup_path
            }
            for f in recovery_point.staged_files
        ],
        "modified_files": [
            {
                "path": f.path,
                "sha256": f.sha256,
                "size_bytes": f.size_bytes,
                "is_binary": f.is_binary,
                "backup_path": f.backup_path
            }
            for f in recovery_point.modified_files
        ],
        "deleted_files": recovery_point.deleted_files,
        "untracked_files": [
            {
                "path": f.path,
                "sha256": f.sha256,
                "size_bytes": f.size_bytes,
                "is_binary": f.is_binary,
                "backup_path": f.backup_path
            }
            for f in recovery_point.untracked_files
        ],
        "total_size_bytes": recovery_point.total_size_bytes,
        "total_file_count": recovery_point.total_file_count,
        "completed": completed,
        "verified_at": recovery_point.verified_at
    }

    # Write manifest atomically
    manifest_path = recovery_dir / "manifest.json"
    temp_path = recovery_dir / "manifest.json.tmp"

    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())

    temp_path.replace(manifest_path)


def check_ignored_conflicts(
    worktree_root: Path,
    git_exe: str,
    target_paths: Set[str]
) -> List[str]:
    """Check if operation would overwrite ignored files.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable
        target_paths: Set of paths that operation would write to

    Returns:
        List of ignored files that would be overwritten

    Raises:
        RecoveryError: If check fails
    """
    if not target_paths:
        return []

    conflicts = []

    for path in target_paths:
        file_path = worktree_root / path

        # Check if file exists and is ignored
        if file_path.exists():
            try:
                result = _run_git_command(
                    git_exe,
                    ['check-ignore', '--', path],
                    cwd=worktree_root,
                    timeout=5.0,
                    check=False
                )

                # Exit code 0 means file is ignored
                if result.returncode == 0:
                    conflicts.append(path)

            except subprocess.SubprocessError:
                # On error, treat as potential conflict (conservative)
                if file_path.exists():
                    conflicts.append(path)

    return conflicts


def reconcile_operation(
    worktree_root: Path,
    git_exe: str,
    operation_id: str,
    expected_head_oid: Optional[str],
    expected_refs: Dict[str, str]
) -> Tuple[bool, Optional[str]]:
    """Reconcile operation after crash or external change.

    Checks if operation completed successfully by comparing current state
    with expected state. Does not re-execute the operation.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable
        operation_id: Operation ID to reconcile
        expected_head_oid: Expected HEAD oid after operation
        expected_refs: Expected refs after operation

    Returns:
        Tuple of (success, error_message)
        - (True, None) if operation succeeded
        - (False, reason) if operation did not complete or state doesn't match
    """
    # Get current HEAD
    current_head = get_head_info(worktree_root, git_exe)

    # Check HEAD matches expected
    if expected_head_oid is not None:
        if current_head.oid != expected_head_oid:
            return False, f"HEAD mismatch: expected {expected_head_oid}, got {current_head.oid}"

    # Check refs match expected
    for ref_name, expected_oid in expected_refs.items():
        try:
            result = _run_git_command(
                git_exe,
                ['rev-parse', '--verify', ref_name],
                cwd=worktree_root,
                timeout=5.0,
                check=False
            )

            if result.returncode != 0:
                return False, f"Expected ref {ref_name} not found"

            current_oid = result.stdout.strip()
            if current_oid != expected_oid:
                return False, f"Ref {ref_name} mismatch: expected {expected_oid}, got {current_oid}"

        except subprocess.SubprocessError as e:
            return False, f"Failed to check ref {ref_name}: {e}"

    return True, None


class ReconcileStatus(Enum):
    """Status of reconciliation check."""
    NO_CHANGE = "no_change"  # State matches recovery point
    STATE_CHANGED = "state_changed"  # State differs from recovery point


@dataclass
class ReconcileResult:
    """Result of reconciliation check."""
    status: ReconcileStatus
    detected_changes: Optional[List[str]] = None


def reconcile_git_state(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    recovery_id: str
) -> ReconcileResult:
    """Reconcile current Git state against a recovery point.

    Compares current state with the recovery point to detect if any changes
    have been made since the recovery point was created.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable
        recovery_id: Recovery ID to reconcile against

    Returns:
        ReconcileResult with status and detected changes
    """
    # Load recovery point
    recovery_point = load_recovery_point(state_root, recovery_id)

    # Get current Git status
    try:
        result = _run_git_command(
            git_exe,
            ['status', '--porcelain=v2', '-z', '--ignored=no'],
            cwd=worktree_root,
            timeout=30.0
        )
    except subprocess.SubprocessError as e:
        raise RecoveryError(f"Failed to get status: {e}")

    # Parse current status
    entries = result.stdout.split('\0')
    current_modified = []
    current_untracked = []

    for entry in entries:
        if not entry or entry.startswith('#'):
            continue

        if entry.startswith('1 '):
            parts = entry.split(' ')
            if len(parts) >= 9:
                xy = parts[1]
                path = ' '.join(parts[8:])
                worktree_status = xy[1] if xy[1] != '.' else ''

                if worktree_status in ('M', 'D', 'T'):
                    current_modified.append(path)

        elif entry.startswith('? '):
            path = entry[2:]
            current_untracked.append(path)

    # Compare with recovery point
    changes = []

    # Check if modified files changed
    recovery_modified = set(f.path for f in recovery_point.modified_files)
    current_modified_set = set(current_modified)

    if recovery_modified != current_modified_set:
        added = current_modified_set - recovery_modified
        removed = recovery_modified - current_modified_set
        if added:
            changes.append(f"New modified files: {', '.join(sorted(added))}")
        if removed:
            changes.append(f"Modified files removed: {', '.join(sorted(removed))}")

    # Check if untracked files changed
    recovery_untracked = set(f.path for f in recovery_point.untracked_files)
    current_untracked_set = set(current_untracked)

    if recovery_untracked != current_untracked_set:
        added = current_untracked_set - recovery_untracked
        removed = recovery_untracked - current_untracked_set
        if added:
            changes.append(f"New untracked files: {', '.join(sorted(added))}")
        if removed:
            changes.append(f"Untracked files removed: {', '.join(sorted(removed))}")

    # Check HEAD
    current_head = get_head_info(worktree_root, git_exe)
    if current_head.oid != recovery_point.head_oid:
        changes.append(f"HEAD changed: {recovery_point.head_oid[:8] if recovery_point.head_oid else 'None'} -> {current_head.oid[:8] if current_head.oid else 'None'}")

    if changes:
        return ReconcileResult(
            status=ReconcileStatus.STATE_CHANGED,
            detected_changes=changes
        )
    else:
        return ReconcileResult(
            status=ReconcileStatus.NO_CHANGE,
            detected_changes=None
        )


def load_recovery_point(
    state_root: Path,
    recovery_id: str
) -> RecoveryPoint:
    """Load recovery point from disk.

    Args:
        state_root: Project state root
        recovery_id: Recovery ID

    Returns:
        RecoveryPoint object

    Raises:
        RecoveryError: If recovery point cannot be loaded
    """
    recovery_dir = get_recovery_path(state_root, recovery_id)
    manifest_path = recovery_dir / "manifest.json"

    if not manifest_path.exists():
        raise RecoveryError(f"Recovery point not found: {recovery_id}")

    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise RecoveryError(f"Failed to load recovery manifest: {e}")

    # Reconstruct recovery point
    recovery_point = RecoveryPoint(
        recovery_id=manifest["recovery_id"],
        operation_id=manifest["operation_id"],
        repo_id=manifest["repo_id"],
        worktree_id=manifest["worktree_id"],
        created_at=manifest["created_at"],
        head_oid=manifest.get("head_oid"),
        head_ref=manifest.get("head_ref"),
        head_unborn=manifest.get("head_unborn", False),
        git_version=tuple(manifest.get("git_version", [0, 0, 0])),
        index_sha256=manifest.get("index_sha256"),
        index_backup=manifest.get("index_backup"),
        recovery_ref=manifest.get("recovery_ref"),
        deleted_files=manifest.get("deleted_files", []),
        total_size_bytes=manifest.get("total_size_bytes", 0),
        total_file_count=manifest.get("total_file_count", 0),
        completed=manifest.get("completed", False),
        verified_at=manifest.get("verified_at")
    )

    # Load file backups
    for f in manifest.get("staged_files", []):
        recovery_point.staged_files.append(FileBackup(
            path=f["path"],
            sha256=f["sha256"],
            size_bytes=f["size_bytes"],
            is_binary=f["is_binary"],
            backup_path=f.get("backup_path")
        ))

    for f in manifest.get("modified_files", []):
        recovery_point.modified_files.append(FileBackup(
            path=f["path"],
            sha256=f["sha256"],
            size_bytes=f["size_bytes"],
            is_binary=f["is_binary"],
            backup_path=f.get("backup_path")
        ))

    for f in manifest.get("untracked_files", []):
        recovery_point.untracked_files.append(FileBackup(
            path=f["path"],
            sha256=f["sha256"],
            size_bytes=f["size_bytes"],
            is_binary=f["is_binary"],
            backup_path=f.get("backup_path")
        ))

    return recovery_point


def restore_from_recovery_point(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    recovery_id: str,
    verify: bool = True
) -> None:
    """Restore worktree from recovery point.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable
        recovery_id: Recovery ID to restore from
        verify: Whether to verify restored files

    Raises:
        RecoveryError: If restoration fails
    """
    # Load recovery point
    recovery_point = load_recovery_point(state_root, recovery_id)

    if not recovery_point.completed:
        raise RecoveryError("Recovery point not completed, cannot restore")

    recovery_dir = get_recovery_path(state_root, recovery_id)
    files_dir = recovery_dir / "files"

    # Restore index
    if recovery_point.index_backup:
        _restore_index(worktree_root, git_exe, recovery_dir, recovery_point, verify)

    # Restore modified files
    for backup in recovery_point.modified_files:
        _restore_file(worktree_root, files_dir, backup, verify)

    # Restore untracked files
    for backup in recovery_point.untracked_files:
        _restore_file(worktree_root, files_dir, backup, verify)

    # Remove files that were deleted
    # (Currently we don't delete files on restore, only restore content that existed)


def _restore_index(
    worktree_root: Path,
    git_exe: str,
    recovery_dir: Path,
    recovery_point: RecoveryPoint,
    verify: bool
) -> None:
    """Restore Git index.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable
        recovery_dir: Recovery directory
        recovery_point: Recovery point
        verify: Whether to verify

    Raises:
        RecoveryError: If restore fails
    """
    # Get index path
    try:
        result = _run_git_command(
            git_exe,
            ['rev-parse', '--git-dir'],
            cwd=worktree_root,
            timeout=5.0
        )
        git_dir = Path(result.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = worktree_root / git_dir

        index_file = git_dir / "index"
        backup_file = recovery_dir / recovery_point.index_backup

        if not backup_file.exists():
            raise RecoveryError("Index backup file not found")

        # Copy back
        shutil.copy2(backup_file, index_file)

        # Verify
        if verify:
            restored_sha256 = _compute_file_sha256(index_file)
            if restored_sha256 != recovery_point.index_sha256:
                raise RecoveryError("Restored index hash mismatch")

    except subprocess.SubprocessError as e:
        raise RecoveryError(f"Failed to restore index: {e}")


def _restore_file(
    worktree_root: Path,
    files_dir: Path,
    backup: FileBackup,
    verify: bool
) -> None:
    """Restore a single file.

    Args:
        worktree_root: Git worktree root
        files_dir: Files backup directory
        backup: File backup info
        verify: Whether to verify

    Raises:
        RecoveryError: If restore fails
    """
    if not backup.backup_path:
        raise RecoveryError(f"File {backup.path} missing backup path")

    backup_file = files_dir / backup.backup_path
    if not backup_file.exists():
        raise RecoveryError(f"Backup file not found: {backup.path}")

    target_file = worktree_root / backup.path

    # Create parent directory
    target_file.parent.mkdir(parents=True, exist_ok=True)

    # Copy file
    shutil.copy2(backup_file, target_file)

    # Verify
    if verify:
        restored_sha256 = _compute_file_sha256(target_file)
        if restored_sha256 != backup.sha256:
            raise RecoveryError(f"Restored file hash mismatch: {backup.path}")
