"""Git revert, restore, and reset operations.

Implementation of T-10: Revert, Restore, and Constrained Reset.

This module implements:
1. Revert operations (regular commits and merge commits with mainline)
2. Restore operations (restore_file and restore_tree)
3. Constrained reset (local unpublished commits only)
4. Published commit detection

Security requirements:
- All operations must create recovery points first
- Use plan → execute → receipt pattern
- Concurrency lock protection
- Non-interactive command execution only
- Block reset of published commits

Acceptance tests: AT-45, AT-46, AT-47, AT-48, AT-49
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Import from git_service
from git_service import (
    RepositoryLock,
    _run_git_command,
    get_head_info,
    get_status,
    generate_operation_id
)

# Import from git_operations
from git_operations import (
    GitOperationError,
    GitDirtyWorktreeError,
    OperationReceipt
)

# Import from git_recovery
from git_recovery import (
    create_recovery_point,
    check_ignored_conflicts
)


class RevertStatus(Enum):
    """Revert operation status."""
    PREVIEWED = "previewed"
    IN_PROGRESS = "in_progress"
    AWAITING_RESOLUTION = "awaiting_resolution"
    COMPLETED = "completed"
    ABORTED = "aborted"


class PublishedCommitError(GitOperationError):
    """Attempt to reset a published commit."""
    pass


class StateExpiredError(GitOperationError):
    """Git state has changed since plan was created."""
    pass


class MainlineRequiredError(GitOperationError):
    """Mainline required for merge commit revert."""
    pass


class GitRefNotFoundError(GitOperationError):
    """Reference not found."""
    pass


@dataclass
class RevertAnalysis:
    """Revert analysis result."""
    target_oid: str
    commit_message: str
    is_merge: bool
    parents: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)
    has_conflicts: bool = False


@dataclass
class RestoreAnalysis:
    """Restore analysis result."""
    target_oid: str
    target_tree_oid: str
    current_tree_oid: str
    files_to_restore: List[Tuple[str, str]] = field(default_factory=list)  # (path, action)
    files_to_remove: List[str] = field(default_factory=list)
    untracked_conflicts: List[str] = field(default_factory=list)
    ignored_conflicts: List[str] = field(default_factory=list)
    is_no_change: bool = False


@dataclass
class ResetAnalysis:
    """Reset analysis result."""
    current_branch: str
    current_oid: str
    target_oid: str
    is_ancestor: bool
    is_published: bool
    commits_to_remove: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)
    remote_refs_check_time: Optional[str] = None
    remote_refs_stale: bool = False


# ============================================================================
# Revert Operations (F-26-A)
# ============================================================================

def analyze_revert(
    worktree_root: Path,
    git_exe: str,
    target_oid: str
) -> RevertAnalysis:
    """Analyze revert operation without modifying worktree.

    Args:
        worktree_root: Worktree root directory
        git_exe: Git executable path
        target_oid: Commit OID to revert

    Returns:
        RevertAnalysis with commit info and conflict detection

    Raises:
        GitRefNotFoundError: Target OID not found
        GitOperationError: Analysis failed
    """
    # Verify target exists
    result = _run_git_command(git_exe, [ "rev-parse", "--verify", f"{target_oid}^{{commit}}"], cwd=worktree_root, timeout=10
    )
    if result.returncode != 0:
        raise GitRefNotFoundError(f"Commit not found: {target_oid}")

    verified_oid = result.stdout.strip()

    # Get commit info
    result = _run_git_command(git_exe, [ "cat-file", "commit", verified_oid], cwd=worktree_root, timeout=10
    )
    if result.returncode != 0:
        raise GitOperationError(f"Failed to read commit: {verified_oid}")

    commit_raw = result.stdout

    # Parse parents
    parents = []
    message_lines = []
    in_message = False

    for line in commit_raw.split("\n"):
        if in_message:
            message_lines.append(line)
        elif line.startswith("parent "):
            parents.append(line[7:].strip())
        elif line == "":
            in_message = True

    commit_message = "\n".join(message_lines).strip()
    is_merge = len(parents) > 1

    # Get affected files using diff
    result = _run_git_command(git_exe, [ "diff", "--name-only", f"{verified_oid}^", verified_oid], cwd=worktree_root, timeout=10
    )
    affected_files = [f for f in result.stdout.strip().split("\n") if f]

    # Check for potential conflicts using merge-tree (dry run)
    has_conflicts = False
    if not is_merge:
        # For regular commits, check if revert would conflict
        result = _run_git_command(git_exe, [ "merge-tree", verified_oid, "HEAD", f"{verified_oid}^"], cwd=worktree_root, timeout=10
        )
        if result.returncode == 0 and "<<<<<<" in result.stdout:
            has_conflicts = True

    return RevertAnalysis(
        target_oid=verified_oid,
        commit_message=commit_message,
        is_merge=is_merge,
        parents=parents,
        affected_files=affected_files,
        has_conflicts=has_conflicts
    )


def start_revert(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str,
    target_oid: str,
    mainline: Optional[int],
    lock_dir: Path,
    message: Optional[str] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[OperationReceipt]]:
    """Start revert operation.

    For regular commits: creates reverse commit automatically if no conflicts.
    For merge commits: requires mainline parameter.

    Args:
        worktree_root: Worktree root directory
        state_root: State root directory
        git_exe: Git executable path
        repo_id: Repository ID
        worktree_id: Worktree ID
        target_oid: Commit OID to revert
        mainline: Parent number (1-based) for merge commits
        lock_dir: Lock directory
        message: Optional custom commit message

    Returns:
        (revert_state, receipt) - receipt is set if completed without conflicts

    Raises:
        GitDirtyWorktreeError: Worktree has uncommitted changes
        MainlineRequiredError: Mainline not specified for merge commit
        GitOperationError: Revert failed
    """
    # Verify clean worktree
    head, files, ongoing = get_status(worktree_root, git_exe)

    if files or ongoing:
        raise GitDirtyWorktreeError("Worktree has uncommitted changes")

    # Analyze revert
    analysis = analyze_revert(worktree_root, git_exe, target_oid)

    # Check mainline requirement for merge commits (AT-46)
    if analysis.is_merge and mainline is None:
        raise MainlineRequiredError(
            f"Merge commit has {len(analysis.parents)} parents. "
            "Must specify which parent to keep (mainline parameter)."
        )

    # Validate mainline
    if analysis.is_merge:
        if mainline < 1 or mainline > len(analysis.parents):
            raise GitOperationError(
                f"Invalid mainline {mainline}. Must be 1-{len(analysis.parents)}."
            )

    operation_id = generate_operation_id()

    def _execute_revert():
        nonlocal message
        # Create recovery point
        recovery_id = create_recovery_point(
            worktree_root=worktree_root,
            state_root=state_root,
            git_exe=git_exe,
            operation_id=operation_id,
            repo_id=repo_id,
            worktree_id=worktree_id
        ).recovery_id

        # Build revert command
        cmd = [git_exe, "revert", "--no-commit"]
        if analysis.is_merge:
            cmd.extend(["--mainline", str(mainline)])
        cmd.append(analysis.target_oid)

        result = _run_git_command(git_exe, cmd[1:], cwd=worktree_root, timeout=60)

        if result.returncode != 0:
            # Check if there are conflicts
            conflicts_result = _run_git_command(git_exe, [ "ls-files", "-u"], cwd=worktree_root, timeout=10
            )

            if conflicts_result.stdout.strip():
                # Has conflicts - return state for user resolution
                revert_state = {
                    "operation_id": operation_id,
                    "repo_id": repo_id,
                    "worktree_id": worktree_id,
                    "status": RevertStatus.AWAITING_RESOLUTION.value,
                    "target_oid": analysis.target_oid,
                    "is_merge": analysis.is_merge,
                    "mainline": mainline,
                    "recovery_id": recovery_id,
                    "started_at": datetime.now(timezone.utc).isoformat()
                }

                # Save state
                state_path = state_root / "operations" / operation_id / "revert_state.json"
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(json.dumps(revert_state, indent=2), encoding="utf-8")

                return (revert_state, None)
            else:
                raise GitOperationError(f"Revert failed: {result['stderr']}")

        # No conflicts - create commit
        if message is None:
            subject_line = analysis.commit_message.split("\n")[0]
            message = f"Revert: {subject_line}"

        commit_result = _run_git_command(git_exe, [ "commit", "-m", message], cwd=worktree_root, timeout=60
        )

        if commit_result.returncode != 0:
            raise GitOperationError(f"Failed to create revert commit: {commit_result['stderr']}")

        # Get new commit OID
        head_result = _run_git_command(git_exe, [ "rev-parse", "HEAD"], cwd=worktree_root, timeout=10
        )
        new_oid = head_result.stdout.strip()

        # Verify commit was created
        if not new_oid or new_oid == analysis.target_oid:
            raise GitOperationError("Failed to create revert commit")

        receipt = OperationReceipt(
            operation_id=operation_id,
            action="revert",
            repo_id=repo_id,
            worktree_id=worktree_id,
            completed_at=datetime.now(timezone.utc).isoformat(),
            result={
                "reverted_oid": analysis.target_oid,
                "new_commit_oid": new_oid,
                "is_merge": analysis.is_merge,
                "mainline": mainline,
                "message": message
            },
            recovery_id=recovery_id
        )

        return (None, receipt)

    with RepositoryLock(lock_dir, repo_id):
        return _execute_revert()


def abort_revert(
    worktree_root: Path,
    git_exe: str,
    repo_id: str,
    lock_dir: Path
) -> OperationReceipt:
    """Abort in-progress revert operation.

    Args:
        worktree_root: Worktree root directory
        git_exe: Git executable path
        repo_id: Repository ID
        lock_dir: Lock directory

    Returns:
        Receipt with abort status

    Raises:
        GitOperationError: Abort failed
    """
    operation_id = generate_operation_id()

    def _execute_abort():
        # Check if revert is in progress
        revert_head = worktree_root / ".git" / "REVERT_HEAD"
        if not revert_head.exists():
            raise GitOperationError("No revert in progress")

        original_head_result = _run_git_command(git_exe, [ "rev-parse", "HEAD"], cwd=worktree_root, timeout=10
        )
        original_head = original_head_result.stdout.strip()

        # Abort revert
        result = _run_git_command(git_exe, [ "revert", "--abort"], cwd=worktree_root, timeout=60
        )

        if result.returncode != 0:
            raise GitOperationError(f"Failed to abort revert: {result['stderr']}")

        # Verify HEAD is unchanged
        current_head_result = _run_git_command(git_exe, [ "rev-parse", "HEAD"], cwd=worktree_root, timeout=10
        )
        current_head = current_head_result.stdout.strip()

        if current_head != original_head:
            raise GitOperationError("HEAD changed during abort")

        # Verify no revert state remains
        if revert_head.exists():
            raise GitOperationError("REVERT_HEAD still exists after abort")

        receipt = OperationReceipt(
            operation_id=operation_id,
            action="abort_revert",
            repo_id=repo_id,
            worktree_id=None,
            completed_at=datetime.now(timezone.utc).isoformat(),
            result={
                "status": "aborted",
                "restored_head": original_head
            }
        )

        return receipt

    with RepositoryLock(lock_dir, repo_id):
        return _execute_abort()


# ============================================================================
# Restore Operations (F-26-B, F-26-D)
# ============================================================================

def analyze_restore_tree(
    worktree_root: Path,
    git_exe: str,
    target_oid: str
) -> RestoreAnalysis:
    """Analyze restore tree operation.

    Determines what files need to be restored/removed to match target tree.
    Detects conflicts with untracked/ignored files.

    Args:
        worktree_root: Worktree root directory
        git_exe: Git executable path
        target_oid: Target commit OID

    Returns:
        RestoreAnalysis with file operations and conflicts

    Raises:
        GitRefNotFoundError: Target OID not found
        GitOperationError: Analysis failed
    """
    # Verify target exists
    result = _run_git_command(git_exe, [ "rev-parse", "--verify", f"{target_oid}^{{commit}}"], cwd=worktree_root, timeout=10
    )
    if result.returncode != 0:
        raise GitRefNotFoundError(f"Commit not found: {target_oid}")

    verified_oid = result.stdout.strip()

    # Get target tree OID
    target_tree_result = _run_git_command(git_exe, [ "rev-parse", f"{verified_oid}^{{tree}}"], cwd=worktree_root, timeout=10
    )
    target_tree_oid = target_tree_result.stdout.strip()

    # Get current tree OID
    current_tree_result = _run_git_command(git_exe, [ "write-tree"], cwd=worktree_root, timeout=10
    )
    current_tree_oid = current_tree_result.stdout.strip()

    # Check if trees are already equal (AT-45)
    if target_tree_oid == current_tree_oid:
        return RestoreAnalysis(
            target_oid=verified_oid,
            target_tree_oid=target_tree_oid,
            current_tree_oid=current_tree_oid,
            is_no_change=True
        )

    # Get list of files in target tree
    target_files_result = _run_git_command(git_exe, [ "ls-tree", "-r", "--name-only", verified_oid], cwd=worktree_root, timeout=10
    )
    target_files = set(target_files_result.stdout.strip().split("\n")) if target_files_result.stdout.strip() else set()

    # Get list of files in current HEAD
    current_files_result = _run_git_command(git_exe, [ "ls-tree", "-r", "--name-only", "HEAD"], cwd=worktree_root, timeout=10
    )
    current_files = set(current_files_result.stdout.strip().split("\n")) if current_files_result.stdout.strip() else set()

    # Calculate operations
    files_to_restore = []
    for path in target_files:
        if path in current_files:
            files_to_restore.append((path, "modify"))
        else:
            files_to_restore.append((path, "add"))

    files_to_remove = [path for path in current_files if path not in target_files]

    # Check for untracked files that would conflict
    untracked_result = _run_git_command(git_exe, [ "ls-files", "--others", "--exclude-standard"], cwd=worktree_root, timeout=10
    )
    untracked_files = set(untracked_result.stdout.strip().split("\n")) if untracked_result.stdout.strip() else set()

    untracked_conflicts = []
    for path, action in files_to_restore:
        if action == "add" and path in untracked_files:
            untracked_conflicts.append(path)

    # Check for ignored files that would conflict
    ignored_conflicts = check_ignored_conflicts(worktree_root, git_exe, [p for p, a in files_to_restore if a == "add"])

    return RestoreAnalysis(
        target_oid=verified_oid,
        target_tree_oid=target_tree_oid,
        current_tree_oid=current_tree_oid,
        files_to_restore=files_to_restore,
        files_to_remove=files_to_remove,
        untracked_conflicts=untracked_conflicts,
        ignored_conflicts=ignored_conflicts,
        is_no_change=False
    )


def restore_tree(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str,
    target_oid: str,
    lock_dir: Path,
    message: Optional[str] = None
) -> OperationReceipt:
    """Restore working tree to match target commit tree.

    Creates a new commit with tree matching target, preserving history (AT-45).

    Args:
        worktree_root: Worktree root directory
        state_root: State root directory
        git_exe: Git executable path
        repo_id: Repository ID
        worktree_id: Worktree ID
        target_oid: Target commit OID
        lock_dir: Lock directory
        message: Optional custom commit message

    Returns:
        Receipt with restore result

    Raises:
        GitDirtyWorktreeError: Worktree has uncommitted changes
        GitOperationError: Restore failed or has conflicts
    """
    # Analyze restore first to check for untracked conflicts
    analysis = analyze_restore_tree(worktree_root, git_exe, target_oid)

    # Check for no-change case
    if analysis.is_no_change:
        receipt = OperationReceipt(
            operation_id=generate_operation_id(),
            action="restore_tree",
            repo_id=repo_id,
            worktree_id=worktree_id,
            completed_at=datetime.now(timezone.utc).isoformat(),
            result={
                "status": "no_change",
                "target_oid": analysis.target_oid,
                "target_tree_oid": analysis.target_tree_oid,
                "message": "Tree already matches target"
            }
        )
        return receipt

    # Check for untracked/ignored conflicts first (before dirty check)
    if analysis.untracked_conflicts:
        raise GitOperationError(
            f"Untracked files would be overwritten: {', '.join(analysis.untracked_conflicts)}"
        )

    if analysis.ignored_conflicts:
        raise GitOperationError(
            f"Ignored files would be overwritten: {', '.join(analysis.ignored_conflicts)}"
        )

    # Verify clean worktree (no staged/modified files)
    head, files, ongoing = get_status(worktree_root, git_exe)

    if files or ongoing:
        raise GitDirtyWorktreeError("Worktree must be clean for restore_tree")

    operation_id = generate_operation_id()

    def _execute_restore():
        nonlocal message
        # Create recovery point
        recovery_id = create_recovery_point(
            worktree_root=worktree_root,
            state_root=state_root,
            git_exe=git_exe,
            operation_id=operation_id,
            repo_id=repo_id,
            worktree_id=worktree_id
        ).recovery_id

        # Use git restore with --source (equivalent to reset --hard but doesn't move HEAD)
        # First, restore to index and worktree
        restore_result = _run_git_command(git_exe, [ "restore", "--source", analysis.target_oid, "--staged", "--worktree", "."], cwd=worktree_root, timeout=60
        )

        if restore_result.returncode != 0:
            raise GitOperationError(f"Failed to restore tree: {restore_result['stderr']}")

        # Verify tree OID matches target (AT-45 hard assertion)
        current_tree_result = _run_git_command(git_exe, [ "write-tree"], cwd=worktree_root, timeout=10
        )
        current_tree_oid = current_tree_result.stdout.strip()

        if current_tree_oid != analysis.target_tree_oid:
            raise GitOperationError(
                f"Tree mismatch after restore: expected {analysis.target_tree_oid}, got {current_tree_oid}"
            )

        # Create commit
        if message is None:
            short_oid = analysis.target_oid[:7]
            message = f"Restore tree to {short_oid}"

        commit_result = _run_git_command(git_exe, [ "commit", "-m", message], cwd=worktree_root, timeout=60
        )

        if commit_result.returncode != 0:
            raise GitOperationError(f"Failed to create restore commit: {commit_result['stderr']}")

        # Get new commit OID
        head_result = _run_git_command(git_exe, [ "rev-parse", "HEAD"], cwd=worktree_root, timeout=10
        )
        new_oid = head_result.stdout.strip()

        # Final verification: new commit's tree must equal target tree
        new_tree_result = _run_git_command(git_exe, [ "rev-parse", f"{new_oid}^{{tree}}"], cwd=worktree_root, timeout=10
        )
        new_tree_oid = new_tree_result.stdout.strip()

        if new_tree_oid != analysis.target_tree_oid:
            raise GitOperationError(
                f"New commit tree mismatch: expected {analysis.target_tree_oid}, got {new_tree_oid}"
            )

        receipt = OperationReceipt(
            operation_id=operation_id,
            action="restore_tree",
            repo_id=repo_id,
            worktree_id=worktree_id,
            completed_at=datetime.now(timezone.utc).isoformat(),
            result={
                "target_oid": analysis.target_oid,
                "target_tree_oid": analysis.target_tree_oid,
                "new_commit_oid": new_oid,
                "new_tree_oid": new_tree_oid,
                "files_restored": len(analysis.files_to_restore),
                "files_removed": len(analysis.files_to_remove),
                "message": message
            },
            recovery_id=recovery_id
        )

        return receipt

    with RepositoryLock(lock_dir, repo_id):
        return _execute_restore()


def restore_file(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str,
    target_oid: str,
    file_path: str,
    lock_dir: Path,
    stage: bool = False
) -> OperationReceipt:
    """Restore single file from target commit (F-26-D, AT-48).

    Args:
        worktree_root: Worktree root directory
        state_root: State root directory
        git_exe: Git executable path
        repo_id: Repository ID
        worktree_id: Worktree ID
        target_oid: Target commit OID
        file_path: File path to restore (relative to worktree root)
        lock_dir: Lock directory
        stage: Whether to stage the restored file

    Returns:
        Receipt with restore result

    Raises:
        GitOperationError: Restore failed
    """
    root = worktree_root.resolve()
    relative_path = Path(file_path)
    full_path = root / relative_path
    if (not file_path or relative_path.is_absolute() or relative_path.drive
            or ".." in relative_path.parts or not relative_path.parts
            or any(part.casefold() == ".git" for part in relative_path.parts)
            or not full_path.resolve().is_relative_to(root)):
        raise GitOperationError("File path must stay inside the worktree")

    # Verify target exists before deciding that a file should be removed.
    result = _run_git_command(git_exe, [ "rev-parse", "--verify", f"{target_oid}^{{commit}}"], cwd=worktree_root, timeout=10, check=False
    )
    if result.returncode != 0:
        raise GitRefNotFoundError(f"Commit not found: {target_oid}")

    verified_oid = result.stdout.strip()

    # Check if file exists in target
    file_check_result = _run_git_command(git_exe, [ "cat-file", "-e", f"{verified_oid}:{file_path}"], cwd=worktree_root, timeout=10, check=False
    )

    file_exists_in_target = file_check_result.returncode == 0
    if not file_exists_in_target:
        # A missing path is expected; an unreadable object must not delete data.
        tree_entry = _run_git_command(
            git_exe, ["ls-tree", "-z", verified_oid, "--", f":(literal){file_path}"],
            cwd=worktree_root, timeout=10,
        )
        if tree_entry.stdout:
            raise GitOperationError("Target file exists but its object cannot be read")

    operation_id = generate_operation_id()

    def _execute_restore_file():
        # Create recovery point for the specific file
        recovery_id = create_recovery_point(
            worktree_root=worktree_root,
            state_root=state_root,
            git_exe=git_exe,
            operation_id=operation_id,
            repo_id=repo_id,
            worktree_id=worktree_id
        ).recovery_id

        if file_exists_in_target:
            # Restore file
            cmd = [git_exe, "restore", "--source", verified_oid]
            if stage:
                cmd.append("--staged")
            cmd.extend(["--worktree", "--", file_path])

            result = _run_git_command(git_exe, cmd[1:], cwd=worktree_root, timeout=30)

            if result.returncode != 0:
                raise GitOperationError(f"Failed to restore file: {result['stderr']}")

            action = "restored"
        else:
            # File doesn't exist in target - remove it
            full_path = worktree_root / file_path
            if full_path.exists():
                full_path.unlink()

                if stage:
                    _run_git_command(git_exe, [ "rm", "--", file_path], cwd=worktree_root, timeout=30
                    )

            action = "removed"

        receipt = OperationReceipt(
            operation_id=operation_id,
            action="restore_file",
            repo_id=repo_id,
            worktree_id=worktree_id,
            completed_at=datetime.now(timezone.utc).isoformat(),
            result={
                "target_oid": verified_oid,
                "file_path": file_path,
                "action": action,
                "staged": stage
            },
            recovery_id=recovery_id
        )

        return receipt

    with RepositoryLock(lock_dir, repo_id):
        return _execute_restore_file()


# ============================================================================
# Constrained Reset (F-26-C)
# ============================================================================

def check_commit_published(
    worktree_root: Path,
    git_exe: str,
    commit_oid: str,
    max_age_seconds: int = 300
) -> Tuple[bool, Optional[str], bool]:
    """Check if commit is published (reachable from remote refs).

    Args:
        worktree_root: Worktree root directory
        git_exe: Git executable path
        commit_oid: Commit OID to check
        max_age_seconds: Max age of remote refs check (default 5 minutes)

    Returns:
        (is_published, check_time, is_stale) tuple

    Raises:
        GitOperationError: Check failed
    """
    # Check if there are any remote refs
    remote_refs_result = _run_git_command(git_exe, [ "for-each-ref", "refs/remotes"], cwd=worktree_root, timeout=10
    )

    if not remote_refs_result.stdout.strip():
        # No remote refs - not published
        return (False, None, False)

    # Check last fetch time from reflog
    fetch_reflog_result = _run_git_command(git_exe, [ "reflog", "show", "--date=iso-strict", "refs/remotes/origin/HEAD"], cwd=worktree_root, timeout=10, check=False
    )

    check_time = None
    is_stale = True

    if fetch_reflog_result.returncode == 0 and fetch_reflog_result.stdout.strip():
        # Parse last fetch time
        first_line = fetch_reflog_result.stdout.strip().split("\n")[0]
        # Format: <commit> <ref>@{<date>} <action>: <message>
        match = re.search(r"@\{([^}]+)\}", first_line)
        if match:
            check_time_str = match.group(1)
            try:
                check_time_dt = datetime.fromisoformat(check_time_str.replace("Z", "+00:00"))
                check_time = check_time_dt.isoformat()
                age = (datetime.now(timezone.utc) - check_time_dt).total_seconds()
                is_stale = age > max_age_seconds
            except ValueError:
                pass

    # Known remote reachability is proof of publication, even with an old or
    # absent reflog. Missing freshness evidence only prevents proving it local.
    # Check if commit is reachable from any remote ref
    for_each_ref_result = _run_git_command(git_exe, [ "for-each-ref", "--format=%(refname)", "refs/remotes"], cwd=worktree_root, timeout=10
    )

    remote_refs = for_each_ref_result.stdout.strip().split("\n")

    for ref in remote_refs:
        if not ref:
            continue

        # Check if commit is ancestor of this remote ref
        merge_base_result = _run_git_command(git_exe, [ "merge-base", "--is-ancestor", commit_oid, ref], cwd=worktree_root, timeout=10, check=False
        )

        if merge_base_result.returncode == 0:
            # Commit is reachable from this remote ref
            return (True, check_time, is_stale)
        if merge_base_result.returncode != 1:
            raise GitOperationError("Failed to check remote commit ancestry")

    return (False, check_time, is_stale)


def analyze_reset(
    worktree_root: Path,
    git_exe: str,
    target_oid: str
) -> ResetAnalysis:
    """Analyze reset operation (AT-47).

    Checks if reset is allowed:
    - Target must be ancestor of current branch
    - Current HEAD must be on a branch (not detached)
    - No commits to be removed are published

    Args:
        worktree_root: Worktree root directory
        git_exe: Git executable path
        target_oid: Target commit OID

    Returns:
        ResetAnalysis with safety checks

    Raises:
        GitRefNotFoundError: Target OID not found
        GitOperationError: Analysis failed
    """
    # Get current HEAD
    head_info = get_head_info(worktree_root, git_exe)

    if head_info.detached:
        raise GitOperationError("Cannot reset from detached HEAD")

    if head_info.unborn or not head_info.branch or not head_info.oid:
        raise GitOperationError("Cannot reset a branch without a commit")

    current_branch = head_info.branch
    current_oid = head_info.oid

    # Verify target exists
    result = _run_git_command(git_exe, [ "rev-parse", "--verify", f"{target_oid}^{{commit}}"], cwd=worktree_root, timeout=10
    )
    if result.returncode != 0:
        raise GitRefNotFoundError(f"Commit not found: {target_oid}")

    verified_oid = result.stdout.strip()

    # Check if target is ancestor of current HEAD
    is_ancestor_result = _run_git_command(git_exe, [ "merge-base", "--is-ancestor", verified_oid, current_oid], cwd=worktree_root, timeout=10, check=False
    )

    if is_ancestor_result.returncode not in (0, 1):
        raise GitOperationError("Failed to check reset target ancestry")
    is_ancestor = is_ancestor_result.returncode == 0

    if not is_ancestor:
        raise GitOperationError(f"{verified_oid} is not an ancestor of current branch")

    # Get commits that will be removed
    rev_list_result = _run_git_command(git_exe, [ "rev-list", f"{verified_oid}..{current_oid}"], cwd=worktree_root, timeout=10
    )

    commits_to_remove = [c for c in rev_list_result.stdout.strip().split("\n") if c]

    # Check if any commit is published
    is_published = False
    remote_refs_check_time = None
    remote_refs_stale = False

    for commit_oid in commits_to_remove:
        pub, check_time, stale = check_commit_published(worktree_root, git_exe, commit_oid)
        if stale:
            remote_refs_stale = True
        if check_time and not remote_refs_check_time:
            remote_refs_check_time = check_time
        if pub:
            is_published = True
            break

    # Get affected files
    if commits_to_remove:
        diff_result = _run_git_command(git_exe, [ "diff", "--name-only", verified_oid, current_oid], cwd=worktree_root, timeout=10
        )
        affected_files = [f for f in diff_result.stdout.strip().split("\n") if f]
    else:
        affected_files = []

    return ResetAnalysis(
        current_branch=current_branch,
        current_oid=current_oid,
        target_oid=verified_oid,
        is_ancestor=is_ancestor,
        is_published=is_published,
        commits_to_remove=commits_to_remove,
        affected_files=affected_files,
        remote_refs_check_time=remote_refs_check_time,
        remote_refs_stale=remote_refs_stale
    )


def reset_branch(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str,
    target_oid: str,
    lock_dir: Path,
    confirmation_branch_name: str
) -> OperationReceipt:
    """Reset current branch to target commit (AT-47, AT-48).

    Constrained reset - only allows resetting unpublished commits.
    Requires explicit confirmation by typing branch name.

    Args:
        worktree_root: Worktree root directory
        state_root: State root directory
        git_exe: Git executable path
        repo_id: Repository ID
        worktree_id: Worktree ID
        target_oid: Target commit OID
        lock_dir: Lock directory
        confirmation_branch_name: User must type branch name to confirm

    Returns:
        Receipt with reset result

    Raises:
        GitDirtyWorktreeError: Worktree has uncommitted changes
        PublishedCommitError: Attempting to reset published commits
        StateExpiredError: Remote refs are stale
        GitOperationError: Reset failed or confirmation mismatch
    """
    # Verify clean worktree
    head, files, ongoing = get_status(worktree_root, git_exe)

    if files or ongoing:
        raise GitDirtyWorktreeError("Worktree must be clean for reset")

    # Analyze reset
    analysis = analyze_reset(worktree_root, git_exe, target_oid)

    # Known publication is decisive even if remote freshness is unknown.
    if analysis.is_published:
        raise PublishedCommitError(
            "Cannot reset: commits are published (reachable from remote refs). "
            "Use restore_tree to create a new commit instead."
        )

    # Never treat stale or missing freshness evidence as permission to reset.
    if analysis.remote_refs_stale:
        raise StateExpiredError(
            "Remote refs have not been checked recently (>5 minutes). "
            "Run 'git fetch' first to ensure commits are not published."
        )

    # Verify confirmation
    if confirmation_branch_name != analysis.current_branch:
        raise GitOperationError(
            f"Confirmation mismatch: expected '{analysis.current_branch}', "
            f"got '{confirmation_branch_name}'"
        )

    operation_id = generate_operation_id()

    def _execute_reset():
        # Create recovery point
        recovery_id = create_recovery_point(
            worktree_root=worktree_root,
            state_root=state_root,
            git_exe=git_exe,
            operation_id=operation_id,
            repo_id=repo_id,
            worktree_id=worktree_id
        ).recovery_id

        # Perform hard reset
        reset_result = _run_git_command(git_exe, [ "reset", "--hard", analysis.target_oid], cwd=worktree_root, timeout=60
        )

        if reset_result.returncode != 0:
            raise GitOperationError(f"Reset failed: {reset_result['stderr']}")

        # Verify symbolic ref is unchanged
        head_info = get_head_info(worktree_root, git_exe)
        if head_info.branch != analysis.current_branch:
            raise GitOperationError(
                f"Branch changed during reset: expected {analysis.current_branch}, "
                f"got {head_info.branch}"
            )

        # Verify HEAD points to target
        if head_info.oid != analysis.target_oid:
            raise GitOperationError(
                f"HEAD mismatch after reset: expected {analysis.target_oid}, "
                f"got {head_info.oid}"
            )

        receipt = OperationReceipt(
            operation_id=operation_id,
            action="reset",
            repo_id=repo_id,
            worktree_id=worktree_id,
            completed_at=datetime.now(timezone.utc).isoformat(),
            result={
                "branch": analysis.current_branch,
                "from_oid": analysis.current_oid,
                "to_oid": analysis.target_oid,
                "commits_removed": len(analysis.commits_to_remove),
                "affected_files": len(analysis.affected_files)
            },
            recovery_id=recovery_id
        )

        return receipt

    with RepositoryLock(lock_dir, repo_id):
        return _execute_reset()


# ============================================================================
# Special Repository Handling (AT-49)
# ============================================================================

def check_repository_constraints(
    worktree_root: Path,
    git_exe: str
) -> Dict[str, Any]:
    """Check for special repository conditions that may limit operations (AT-49).

    Args:
        worktree_root: Worktree root directory
        git_exe: Git executable path

    Returns:
        Dictionary with constraint flags and messages
    """
    constraints = {
        "is_shallow": False,
        "is_partial": False,
        "has_submodules": False,
        "has_sparse_index": False,
        "has_split_index": False,
        "warnings": []
    }

    # Check for shallow clone
    shallow_file = worktree_root / ".git" / "shallow"
    if shallow_file.exists():
        constraints["is_shallow"] = True
        constraints["warnings"].append("Shallow clone detected - ancestry checks may be unreliable")

    # Check for partial clone
    config_result = _run_git_command(git_exe, [ "config", "--get", "extensions.partialClone"], cwd=worktree_root, timeout=10, check=False
    )
    if config_result.returncode not in (0, 1):
        raise GitOperationError("Failed to inspect partial-clone configuration")
    if config_result.returncode == 0:
        constraints["is_partial"] = True
        constraints["warnings"].append("Partial clone detected - some objects may not be available")

    # Check for submodules
    gitmodules = worktree_root / ".gitmodules"
    if gitmodules.exists():
        constraints["has_submodules"] = True
        constraints["warnings"].append("Submodules detected - operations affecting submodules are not supported in v1")

    # Check for split index
    index_path = worktree_root / ".git" / "index"
    if index_path.exists():
        try:
            with open(index_path, "rb") as f:
                # Git index header: 4-byte signature 'DIRC', 4-byte version
                header = f.read(8)
                if len(header) >= 8:
                    version = int.from_bytes(header[4:8], byteorder='big')

                    if version == 4:
                        constraints["has_split_index"] = True
                        constraints["warnings"].append("Split index detected - write operations not supported")
        except Exception:
            pass

    # Check for sparse checkout
    sparse_checkout_file = worktree_root / ".git" / "info" / "sparse-checkout"
    if sparse_checkout_file.exists():
        constraints["warnings"].append("Sparse checkout detected - write operations not supported")

    return constraints


def restore_tree_as_commit(worktree_root: Path, git_exe: str, target_oid: str) -> Dict[str, Any]:
    """Restore the complete tracked tree and append history (never reset/revert).

    Explicit web confirmation, identity/extension/overwrite checks and a common
    repository lock belong to the caller. Return partial if files were restored
    but no verified version was saved. Do not erase or automatically retry work.
    """
    def git(*args):
        return _run_git_command(git_exe, list(args), cwd=worktree_root)

    before = git('rev-parse', 'HEAD').stdout.strip()
    tree = git('rev-parse', target_oid + '^{tree}').stdout.strip()
    if tree == git('rev-parse', before + '^{tree}').stdout.strip():
        return {'outcome': 'no_change', 'message': '文件树已与目标一致。'}
    try:
        git('restore', '--source=' + target_oid, '--staged', '--worktree', '--', '.')
        if git('write-tree').stdout.strip() != tree:
            return {'outcome': 'partial', 'message': '恢复尚未完整完成，请检查当前文件，不会自动回滚。'}
        subject = git('show', '-s', '--format=%s', target_oid).stdout.strip()
        short = git('rev-parse', '--short', target_oid).stdout.strip()
        git('commit', '-m', f'恢复到 {short}：{subject}')
    except subprocess.SubprocessError:
        return {'outcome': 'partial', 'message': '文件恢复后版本尚未确认保存，请检查当前状态；不会自动回滚。'}
    if git('show', '-s', '--format=%P %T', 'HEAD').stdout.strip() != before + ' ' + tree:
        return {'outcome': 'partial', 'message': '恢复后的历史或文件树有额外变化，请检查，不会自动回滚。'}
    return {'outcome': 'done', 'message': '已恢复并新增版本，此前历史完整保留。',
            'commit_oid': git('rev-parse', 'HEAD').stdout.strip()}
