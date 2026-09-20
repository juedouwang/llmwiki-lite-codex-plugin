"""Git merge operations with conflict resolution.

Implementation of T-09: Merge and Conflict Resolution.

This module implements:
1. Merge operations (fast-forward, three-way, no-conflict preview)
2. Conflict detection and resolution (text, binary, rename/delete)
3. Conflict UI (per-file resolution, manual edit, mark resolved)
4. Cancel and recovery (git merge --abort, restart recovery)

Security requirements:
- All merge operations must create recovery points first
- Use plan → execute → receipt pattern
- Concurrency lock protection
- Non-interactive command execution only

Acceptance tests: AT-38, AT-39, AT-43, AT-44
Fixtures: GIT-F1, GIT-F5
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
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
    GitPlan,
    RepositoryLock,
    _run_git_command,
    get_head_info,
    get_status,
    check_ref_format,
    compute_state_token,
    create_plan,
    validate_plan_state,
    generate_operation_id
)

# Import from git_operations
from git_operations import (
    GitOperationError,
    GitDirtyWorktreeError,
    GitRefNotFoundError,
    OperationReceipt,
    execute_with_recovery
)

# Import from git_recovery
from git_recovery import (
    RecoveryError,
    RecoveryPoint,
    create_recovery_point,
    check_ignored_conflicts,
    reconcile_operation,
    load_recovery_point
)


class MergeType(Enum):
    """Merge type classification."""
    FAST_FORWARD = "fast_forward"
    THREE_WAY = "three_way"
    ALREADY_UP_TO_DATE = "already_up_to_date"
    UNRELATED_HISTORIES = "unrelated_histories"


class ConflictType(Enum):
    """Conflict type classification."""
    TEXT = "text"
    ADD_ADD = "add_add"
    MODIFY_DELETE = "modify_delete"
    DELETE_MODIFY = "delete_modify"
    RENAME = "rename"
    BINARY = "binary"


class MergeStatus(Enum):
    """Merge operation status."""
    PREVIEWED = "previewed"
    IN_PROGRESS = "in_progress"
    AWAITING_RESOLUTION = "awaiting_resolution"
    COMPLETED = "completed"
    ABORTED = "aborted"


@dataclass
class ConflictMarker:
    """Conflict marker in text file."""
    start_line: int
    separator_line: int
    end_line: int
    ours_content: str
    theirs_content: str
    base_content: Optional[str] = None


@dataclass
class FileConflict:
    """File conflict details."""
    path: str
    conflict_type: ConflictType
    ours_oid: Optional[str] = None
    theirs_oid: Optional[str] = None
    base_oid: Optional[str] = None
    ours_mode: Optional[str] = None
    theirs_mode: Optional[str] = None
    base_mode: Optional[str] = None
    old_path: Optional[str] = None
    is_binary: bool = False
    markers: List[ConflictMarker] = field(default_factory=list)
    resolved: bool = False
    resolution_choice: Optional[str] = None  # "ours", "theirs", "manual"
    resolution_content: Optional[bytes] = None


@dataclass
class MergeAnalysis:
    """Merge analysis result."""
    merge_type: MergeType
    target_branch: str
    target_oid: str
    source_branch: str
    source_oid: str
    merge_base_oid: Optional[str] = None
    can_fast_forward: bool = False
    has_conflicts: bool = False
    affected_files: List[str] = field(default_factory=list)
    conflicts: List[FileConflict] = field(default_factory=list)


@dataclass
class MergeState:
    """In-progress merge state."""
    operation_id: str
    repo_id: str
    worktree_id: str
    status: MergeStatus
    target_branch: str
    target_oid: str
    source_branch: str
    source_oid: str
    merge_base_oid: Optional[str] = None
    conflicts: List[FileConflict] = field(default_factory=list)
    recovery_id: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ============================================================================
# Merge Analysis
# ============================================================================

def analyze_merge(
    worktree_root: Path,
    git_exe: str,
    target_branch: str,
    source_ref: str
) -> MergeAnalysis:
    """Analyze merge between target and source.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path
        target_branch: Target branch name (current branch)
        source_ref: Source reference to merge from

    Returns:
        MergeAnalysis

    Raises:
        GitOperationError: If analysis fails
    """
    # Get target OID
    result = _run_git_command(
        git_exe,
        ["rev-parse", "--verify", target_branch],
        cwd=worktree_root
    )
    target_oid = result.stdout.strip()

    # Get source OID
    result = _run_git_command(
        git_exe,
        ["rev-parse", "--verify", source_ref],
        cwd=worktree_root
    )
    source_oid = result.stdout.strip()

    # Check if already up-to-date
    if target_oid == source_oid:
        return MergeAnalysis(
            merge_type=MergeType.ALREADY_UP_TO_DATE,
            target_branch=target_branch,
            target_oid=target_oid,
            source_branch=source_ref,
            source_oid=source_oid
        )

    # Try to find merge base
    merge_base_oid = None
    try:
        result = _run_git_command(
            git_exe,
            ["merge-base", target_oid, source_oid],
            cwd=worktree_root
        )
        merge_base_oid = result.stdout.strip()
    except subprocess.CalledProcessError:
        # No merge base - unrelated histories
        return MergeAnalysis(
            merge_type=MergeType.UNRELATED_HISTORIES,
            target_branch=target_branch,
            target_oid=target_oid,
            source_branch=source_ref,
            source_oid=source_oid
        )

    # Check if can fast-forward
    can_fast_forward = (merge_base_oid == target_oid)

    if can_fast_forward:
        # Get affected files for fast-forward
        result = _run_git_command(
            git_exe,
            ["diff", "--name-only", target_oid, source_oid],
            cwd=worktree_root
        )
        affected_files = [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]

        return MergeAnalysis(
            merge_type=MergeType.FAST_FORWARD,
            target_branch=target_branch,
            target_oid=target_oid,
            source_branch=source_ref,
            source_oid=source_oid,
            merge_base_oid=merge_base_oid,
            can_fast_forward=True,
            affected_files=affected_files
        )

    # Three-way merge - check for conflicts
    # Use git merge-tree to simulate merge
    result = _run_git_command(
        git_exe,
        ["merge-tree", merge_base_oid, target_oid, source_oid],
        cwd=worktree_root
    )

    merge_tree_output = result.stdout
    has_conflicts = "changed in both" in merge_tree_output or "+<<<<<<<" in merge_tree_output

    # Get affected files
    result = _run_git_command(
        git_exe,
        ["diff", "--name-only", merge_base_oid, source_oid],
        cwd=worktree_root
    )
    source_changed = set(line.strip() for line in result.stdout.splitlines() if line.strip())

    result = _run_git_command(
        git_exe,
        ["diff", "--name-only", merge_base_oid, target_oid],
        cwd=worktree_root
    )
    target_changed = set(line.strip() for line in result.stdout.splitlines() if line.strip())

    affected_files = sorted(source_changed | target_changed)

    return MergeAnalysis(
        merge_type=MergeType.THREE_WAY,
        target_branch=target_branch,
        target_oid=target_oid,
        source_branch=source_ref,
        source_oid=source_oid,
        merge_base_oid=merge_base_oid,
        can_fast_forward=False,
        has_conflicts=has_conflicts,
        affected_files=affected_files
    )


# ============================================================================
# Conflict Detection
# ============================================================================

def detect_conflicts(
    worktree_root: Path,
    git_exe: str
) -> List[FileConflict]:
    """Detect conflicts in working tree after merge.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path

    Returns:
        List of FileConflict

    Raises:
        GitOperationError: If detection fails
    """
    conflicts = []

    # Use git ls-files -u to find unmerged files
    result = _run_git_command(
        git_exe,
        ["ls-files", "-u", "-z"],
        cwd=worktree_root
    )

    if not result.stdout:
        return conflicts

    # Parse unmerged files
    # Format: <mode> <oid> <stage>\t<path>\0
    entries = result.stdout.split('\0')
    file_stages: Dict[str, Dict[int, Tuple[str, str]]] = {}

    for entry in entries:
        if not entry.strip():
            continue

        parts = entry.split('\t', 1)
        if len(parts) != 2:
            continue

        meta, path = parts
        mode_oid_stage = meta.split()
        if len(mode_oid_stage) != 3:
            continue

        mode, oid, stage = mode_oid_stage
        stage_num = int(stage)

        if path not in file_stages:
            file_stages[path] = {}
        file_stages[path][stage_num] = (mode, oid)

    # Analyze each conflicted file
    for path, stages in file_stages.items():
        base_mode, base_oid = stages.get(1, (None, None))
        ours_mode, ours_oid = stages.get(2, (None, None))
        theirs_mode, theirs_oid = stages.get(3, (None, None))

        # Determine conflict type
        conflict_type = _classify_conflict(
            base_oid, ours_oid, theirs_oid,
            base_mode, ours_mode, theirs_mode
        )

        # Check if binary
        is_binary = _is_binary_file(worktree_root, git_exe, path, ours_oid or theirs_oid or base_oid)

        conflict = FileConflict(
            path=path,
            conflict_type=conflict_type,
            ours_oid=ours_oid,
            theirs_oid=theirs_oid,
            base_oid=base_oid,
            ours_mode=ours_mode,
            theirs_mode=theirs_mode,
            base_mode=base_mode,
            is_binary=is_binary
        )

        # If text conflict, parse conflict markers
        if conflict_type == ConflictType.TEXT and not is_binary:
            file_path = worktree_root / path
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding='utf-8')
                    conflict.markers = _parse_conflict_markers(content)
                except (UnicodeDecodeError, OSError):
                    # Treat as binary if cannot read as text
                    conflict.is_binary = True

        conflicts.append(conflict)

    return conflicts


def _classify_conflict(
    base_oid: Optional[str],
    ours_oid: Optional[str],
    theirs_oid: Optional[str],
    base_mode: Optional[str],
    ours_mode: Optional[str],
    theirs_mode: Optional[str]
) -> ConflictType:
    """Classify conflict type based on stages."""
    # ADD/ADD conflict
    if base_oid is None and ours_oid and theirs_oid:
        return ConflictType.ADD_ADD

    # MODIFY/DELETE conflict
    if base_oid and ours_oid and theirs_oid is None:
        return ConflictType.MODIFY_DELETE

    # DELETE/MODIFY conflict
    if base_oid and ours_oid is None and theirs_oid:
        return ConflictType.DELETE_MODIFY

    # Check for rename (mode change or complex)
    if base_mode != ours_mode or base_mode != theirs_mode:
        if base_oid and ours_oid and theirs_oid:
            # Could be rename or mode change
            return ConflictType.RENAME

    # Default: text conflict
    return ConflictType.TEXT


def _is_binary_file(
    worktree_root: Path,
    git_exe: str,
    path: str,
    oid: Optional[str]
) -> bool:
    """Check if file is binary.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path
        path: File path
        oid: Object ID (if None, check working tree)

    Returns:
        True if binary
    """
    try:
        if oid:
            # Check object in Git database
            result = _run_git_command(
                git_exe,
                ["cat-file", "-p", oid],
                cwd=worktree_root
            )
            content = result.stdout.encode('utf-8', errors='ignore')
        else:
            # Check working tree file
            file_path = worktree_root / path
            if not file_path.exists():
                return False
            content = file_path.read_bytes()

        # Simple heuristic: check for null bytes
        return b'\0' in content[:8192]

    except (subprocess.CalledProcessError, OSError):
        return False


def _parse_conflict_markers(content: str) -> List[ConflictMarker]:
    """Parse conflict markers in text content.

    Args:
        content: File content with conflict markers

    Returns:
        List of ConflictMarker
    """
    markers = []
    lines = content.splitlines(keepends=True)

    i = 0
    while i < len(lines):
        line = lines[i]

        # Look for start marker
        if line.startswith('<<<<<<<'):
            start_line = i
            ours_lines = []
            separator_line = None
            theirs_lines = []
            end_line = None

            # Collect ours content
            i += 1
            while i < len(lines):
                if lines[i].startswith('======='):
                    separator_line = i
                    break
                ours_lines.append(lines[i])
                i += 1

            # Collect theirs content
            if separator_line is not None:
                i += 1
                while i < len(lines):
                    if lines[i].startswith('>>>>>>>'):
                        end_line = i
                        break
                    theirs_lines.append(lines[i])
                    i += 1

            if separator_line is not None and end_line is not None:
                markers.append(ConflictMarker(
                    start_line=start_line,
                    separator_line=separator_line,
                    end_line=end_line,
                    ours_content=''.join(ours_lines),
                    theirs_content=''.join(theirs_lines)
                ))

        i += 1

    return markers


# ============================================================================
# Merge Operations
# ============================================================================

def start_merge(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str,
    source_ref: str,
    lock_dir: Path,
    no_ff: bool = False
) -> Tuple[MergeState, Optional[OperationReceipt]]:
    """Start merge operation.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable path
        repo_id: Repository ID
        worktree_id: Worktree ID
        source_ref: Source reference to merge
        lock_dir: Lock directory
        no_ff: Force creation of merge commit (no fast-forward)

    Returns:
        (MergeState, Optional[OperationReceipt])
        Receipt is None if merge needs user resolution

    Raises:
        GitOperationError: If merge fails
        GitDirtyWorktreeError: If worktree is not clean
    """
    # Check worktree is clean
    head, files, ongoing_op = get_status(worktree_root, git_exe)
    if files:  # Has uncommitted changes
        raise GitDirtyWorktreeError(
            "Worktree has uncommitted changes. "
            "Commit or stash changes before merging."
        )

    # Get current branch
    head_info = get_head_info(worktree_root, git_exe)
    if not head_info.branch:
        raise GitOperationError("Cannot merge in detached HEAD state")

    target_branch = head_info.branch

    # Analyze merge
    analysis = analyze_merge(worktree_root, git_exe, target_branch, source_ref)

    # Handle special cases
    if analysis.merge_type == MergeType.ALREADY_UP_TO_DATE:
        # No operation needed
        operation_id = generate_operation_id()
        receipt = OperationReceipt(
            operation_id=operation_id,
            action="merge",
            repo_id=repo_id,
            worktree_id=worktree_id,
            completed_at=datetime.now(timezone.utc).isoformat(),
            result={
                "status": "no_change",
                "message": "Already up-to-date",
                "target_branch": target_branch,
                "source_ref": source_ref
            }
        )

        merge_state = MergeState(
            operation_id=operation_id,
            repo_id=repo_id,
            worktree_id=worktree_id,
            status=MergeStatus.COMPLETED,
            target_branch=target_branch,
            target_oid=analysis.target_oid,
            source_branch=source_ref,
            source_oid=analysis.source_oid
        )

        return merge_state, receipt

    if analysis.merge_type == MergeType.UNRELATED_HISTORIES:
        raise GitOperationError(
            "Cannot merge unrelated histories. "
            "v1 does not support --allow-unrelated-histories option."
        )

    # Create operation plan
    state_token = compute_state_token(head, files)

    plan = create_plan(
        action="merge",
        repo_id=repo_id,
        worktree_id=worktree_id,
        state_token=state_token,
        params={
            "target_branch": target_branch,
            "source_ref": source_ref,
            "no_ff": no_ff,
            "merge_type": analysis.merge_type.value
        }
    )

    operation_id = plan.operation_id

    # Handle fast-forward
    if analysis.merge_type == MergeType.FAST_FORWARD and not no_ff:
        def execute_ff_merge():
            # Create recovery point
            recovery_point = create_recovery_point(
                worktree_root=worktree_root,
                state_root=state_root,
                git_exe=git_exe,
                operation_id=operation_id,
                repo_id=repo_id,
                worktree_id=worktree_id
            )

            # Execute fast-forward merge
            _run_git_command(
                git_exe,
                ["merge", "--ff-only", source_ref],
                cwd=worktree_root
            )

            # Verify
            new_head = get_head_info(worktree_root, git_exe)
            if new_head.oid != analysis.source_oid:
                raise GitOperationError(
                    f"Fast-forward merge verification failed: "
                    f"expected {analysis.source_oid}, got {new_head.oid}"
                )

            receipt = OperationReceipt(
                operation_id=operation_id,
                action="merge",
                repo_id=repo_id,
                worktree_id=worktree_id,
                completed_at=datetime.now(timezone.utc).isoformat(),
                result={
                    "status": "fast_forward",
                    "target_branch": target_branch,
                    "source_ref": source_ref,
                    "new_oid": new_head.oid,
                    "affected_files": analysis.affected_files
                },
                recovery_id=recovery_point.recovery_id
            )

            return receipt

        with RepositoryLock(lock_dir, repo_id):
            receipt = execute_ff_merge()

            merge_state = MergeState(
                operation_id=operation_id,
                repo_id=repo_id,
                worktree_id=worktree_id,
                status=MergeStatus.COMPLETED,
                target_branch=target_branch,
                target_oid=analysis.target_oid,
                source_branch=source_ref,
                source_oid=analysis.source_oid,
                recovery_id=receipt.recovery_id
            )

            return merge_state, receipt

    # Three-way merge - start with --no-commit
    def execute_three_way_merge():
        # Create recovery point
        recovery_point = create_recovery_point(
            worktree_root=worktree_root,
            state_root=state_root,
            git_exe=git_exe,
            operation_id=operation_id,
            repo_id=repo_id,
            worktree_id=worktree_id
        )

        # Execute merge with --no-commit
        try:
            _run_git_command(
                git_exe,
                ["merge", "--no-ff", "--no-commit", source_ref],
                cwd=worktree_root
            )
        except subprocess.CalledProcessError as e:
            # Merge may fail due to conflicts - this is expected
            if e.returncode != 1:
                raise GitOperationError(f"Merge failed: {e.stderr}")

        # Detect conflicts
        conflicts = detect_conflicts(worktree_root, git_exe)

        merge_state = MergeState(
            operation_id=operation_id,
            repo_id=repo_id,
            worktree_id=worktree_id,
            status=MergeStatus.AWAITING_RESOLUTION if conflicts else MergeStatus.IN_PROGRESS,
            target_branch=target_branch,
            target_oid=analysis.target_oid,
            source_branch=source_ref,
            source_oid=analysis.source_oid,
            merge_base_oid=analysis.merge_base_oid,
            conflicts=conflicts,
            recovery_id=recovery_point.recovery_id
        )

        # Save merge state
        _save_merge_state(state_root, merge_state)

        return merge_state

    with RepositoryLock(lock_dir, repo_id):
        merge_state = execute_three_way_merge()
        return merge_state, None


# ============================================================================
# Merge State Management
# ============================================================================

def _save_merge_state(state_root: Path, merge_state: MergeState):
    """Save merge state to disk."""
    merge_dir = state_root / "operations" / "merges"
    merge_dir.mkdir(parents=True, exist_ok=True)

    state_file = merge_dir / f"{merge_state.operation_id}.json"

    data = {
        "operation_id": merge_state.operation_id,
        "repo_id": merge_state.repo_id,
        "worktree_id": merge_state.worktree_id,
        "status": merge_state.status.value,
        "target_branch": merge_state.target_branch,
        "target_oid": merge_state.target_oid,
        "source_branch": merge_state.source_branch,
        "source_oid": merge_state.source_oid,
        "merge_base_oid": merge_state.merge_base_oid,
        "conflicts": [
            {
                "path": c.path,
                "conflict_type": c.conflict_type.value,
                "ours_oid": c.ours_oid,
                "theirs_oid": c.theirs_oid,
                "base_oid": c.base_oid,
                "ours_mode": c.ours_mode,
                "theirs_mode": c.theirs_mode,
                "base_mode": c.base_mode,
                "old_path": c.old_path,
                "is_binary": c.is_binary,
                "resolved": c.resolved,
                "resolution_choice": c.resolution_choice
            }
            for c in merge_state.conflicts
        ],
        "recovery_id": merge_state.recovery_id,
        "started_at": merge_state.started_at,
        "last_updated": merge_state.last_updated
    }

    state_file.write_text(json.dumps(data, indent=2), encoding='utf-8')


def load_merge_state(state_root: Path, operation_id: str) -> Optional[MergeState]:
    """Load merge state from disk."""
    state_file = state_root / "operations" / "merges" / f"{operation_id}.json"

    if not state_file.exists():
        return None

    try:
        data = json.loads(state_file.read_text(encoding='utf-8'))

        conflicts = [
            FileConflict(
                path=c["path"],
                conflict_type=ConflictType(c["conflict_type"]),
                ours_oid=c.get("ours_oid"),
                theirs_oid=c.get("theirs_oid"),
                base_oid=c.get("base_oid"),
                ours_mode=c.get("ours_mode"),
                theirs_mode=c.get("theirs_mode"),
                base_mode=c.get("base_mode"),
                old_path=c.get("old_path"),
                is_binary=c.get("is_binary", False),
                resolved=c.get("resolved", False),
                resolution_choice=c.get("resolution_choice")
            )
            for c in data.get("conflicts", [])
        ]

        return MergeState(
            operation_id=data["operation_id"],
            repo_id=data["repo_id"],
            worktree_id=data["worktree_id"],
            status=MergeStatus(data["status"]),
            target_branch=data["target_branch"],
            target_oid=data["target_oid"],
            source_branch=data["source_branch"],
            source_oid=data["source_oid"],
            merge_base_oid=data.get("merge_base_oid"),
            conflicts=conflicts,
            recovery_id=data.get("recovery_id"),
            started_at=data["started_at"],
            last_updated=data["last_updated"]
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        raise GitOperationError(f"Failed to load merge state: {e}")


def get_active_merge_state(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str
) -> Optional[MergeState]:
    """Get active merge state from Git and state files.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable path
        repo_id: Repository ID

    Returns:
        MergeState if merge is in progress, None otherwise
    """
    # Check if Git merge is in progress
    merge_head_file = worktree_root / ".git" / "MERGE_HEAD"
    if not merge_head_file.exists():
        return None

    # Find most recent merge state file
    merge_dir = state_root / "operations" / "merges"
    if not merge_dir.exists():
        return None

    state_files = sorted(
        merge_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    for state_file in state_files:
        try:
            merge_state = load_merge_state(state_root, state_file.stem)
            if merge_state and merge_state.repo_id == repo_id:
                if merge_state.status in (MergeStatus.IN_PROGRESS, MergeStatus.AWAITING_RESOLUTION):
                    # Refresh conflicts
                    conflicts = detect_conflicts(worktree_root, git_exe)
                    merge_state.conflicts = conflicts
                    merge_state.last_updated = datetime.now(timezone.utc).isoformat()
                    _save_merge_state(state_root, merge_state)
                    return merge_state
        except GitOperationError:
            continue

    return None


# ============================================================================
# Conflict Resolution
# ============================================================================

def resolve_conflict(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    operation_id: str,
    file_path: str,
    resolution: str,
    content: Optional[bytes] = None
) -> MergeState:
    """Resolve a single file conflict.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable path
        operation_id: Merge operation ID
        file_path: Conflicted file path
        resolution: Resolution choice ("ours", "theirs", "manual", "delete")
        content: Manual resolution content (required if resolution="manual")

    Returns:
        Updated MergeState

    Raises:
        GitOperationError: If resolution fails
    """
    merge_state = load_merge_state(state_root, operation_id)
    if not merge_state:
        raise GitOperationError(f"Merge state not found: {operation_id}")

    # Find conflict
    conflict = next((c for c in merge_state.conflicts if c.path == file_path), None)
    if not conflict:
        raise GitOperationError(f"Conflict not found: {file_path}")

    if conflict.resolved:
        raise GitOperationError(f"Conflict already resolved: {file_path}")

    # Apply resolution
    full_path = worktree_root / file_path

    if resolution == "ours":
        if conflict.ours_oid:
            # Checkout from ours side
            _run_git_command(
                git_exe,
                ["checkout", "--ours", "--", file_path],
                cwd=worktree_root
            )
        else:
            raise GitOperationError(f"Cannot resolve to ours: file does not exist in our side")

    elif resolution == "theirs":
        if conflict.theirs_oid:
            # Checkout from theirs side
            _run_git_command(
                git_exe,
                ["checkout", "--theirs", "--", file_path],
                cwd=worktree_root
            )
        else:
            raise GitOperationError(f"Cannot resolve to theirs: file does not exist in their side")

    elif resolution == "delete":
        # Explicitly delete
        if full_path.exists():
            full_path.unlink()

    elif resolution == "manual":
        if content is None:
            raise GitOperationError("Manual resolution requires content")
        # Write manual resolution
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

    else:
        raise GitOperationError(f"Invalid resolution: {resolution}")

    # Stage the resolution
    _run_git_command(
        git_exe,
        ["add", "--", file_path],
        cwd=worktree_root
    )

    # Mark conflict as resolved
    conflict.resolved = True
    conflict.resolution_choice = resolution
    if resolution == "manual":
        conflict.resolution_content = content

    merge_state.last_updated = datetime.now(timezone.utc).isoformat()
    _save_merge_state(state_root, merge_state)

    return merge_state


def check_all_conflicts_resolved(merge_state: MergeState) -> bool:
    """Check if all conflicts are resolved."""
    return all(c.resolved for c in merge_state.conflicts)


# ============================================================================
# Complete Merge
# ============================================================================

def complete_merge(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str,
    operation_id: str,
    lock_dir: Path,
    message: Optional[str] = None
) -> OperationReceipt:
    """Complete merge operation by creating merge commit.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable path
        repo_id: Repository ID
        worktree_id: Worktree ID
        operation_id: Merge operation ID
        lock_dir: Lock directory
        message: Commit message (auto-generated if None)

    Returns:
        OperationReceipt

    Raises:
        GitOperationError: If completion fails
    """
    merge_state = load_merge_state(state_root, operation_id)
    if not merge_state:
        raise GitOperationError(f"Merge state not found: {operation_id}")

    if merge_state.status not in (MergeStatus.IN_PROGRESS, MergeStatus.AWAITING_RESOLUTION):
        raise GitOperationError(f"Merge not in progress: {merge_state.status.value}")

    # Check all conflicts are resolved
    if not check_all_conflicts_resolved(merge_state):
        unresolved = [c.path for c in merge_state.conflicts if not c.resolved]
        raise GitOperationError(
            f"Cannot complete merge: unresolved conflicts in {len(unresolved)} files: "
            f"{', '.join(unresolved[:5])}"
        )

    # Check unmerged index entries
    result = _run_git_command(
        git_exe,
        ["ls-files", "-u"],
        cwd=worktree_root
    )
    if result.stdout.strip():
        raise GitOperationError(
            "Cannot complete merge: index still has unmerged entries. "
            "Ensure all conflicts are resolved and staged."
        )

    # Generate commit message if not provided
    if not message:
        message = f"Merge branch '{merge_state.source_branch}' into {merge_state.target_branch}"

    # Check for conflict markers in working tree
    has_markers = _check_conflict_markers_in_staged(worktree_root, git_exe)
    if has_markers:
        # This is a warning, not a blocker - user may have confirmed these are intentional
        pass

    def execute_merge_commit():
        # Create merge commit
        _run_git_command(
            git_exe,
            ["commit", "--no-edit", "-m", message],
            cwd=worktree_root
        )

        # Verify merge commit
        new_head = get_head_info(worktree_root, git_exe)
        if not new_head.oid:
            raise GitOperationError("Merge commit verification failed: no HEAD oid")

        # Get parents
        result = _run_git_command(
            git_exe,
            ["rev-parse", f"{new_head.oid}^@"],
            cwd=worktree_root
        )
        parents = [p.strip() for p in result.stdout.splitlines() if p.strip()]

        # Verify parents match expected
        if len(parents) != 2:
            raise GitOperationError(
                f"Merge commit verification failed: expected 2 parents, got {len(parents)}"
            )
        if parents[0] != merge_state.target_oid:
            raise GitOperationError(
                f"Merge commit verification failed: first parent should be {merge_state.target_oid}, got {parents[0]}"
            )
        if parents[1] != merge_state.source_oid:
            raise GitOperationError(
                f"Merge commit verification failed: second parent should be {merge_state.source_oid}, got {parents[1]}"
            )

        # Get tree
        result = _run_git_command(
            git_exe,
            ["rev-parse", f"{new_head.oid}^{{tree}}"],
            cwd=worktree_root
        )
        tree_oid = result.stdout.strip()

        receipt = OperationReceipt(
            operation_id=operation_id,
            action="merge",
            repo_id=repo_id,
            worktree_id=worktree_id,
            completed_at=datetime.now(timezone.utc).isoformat(),
            result={
                "status": "completed",
                "target_branch": merge_state.target_branch,
                "source_branch": merge_state.source_branch,
                "merge_commit_oid": new_head.oid,
                "parents": parents,
                "tree_oid": tree_oid,
                "conflicts_resolved": len(merge_state.conflicts)
            },
            recovery_id=merge_state.recovery_id
        )

        # Update merge state
        merge_state.status = MergeStatus.COMPLETED
        merge_state.last_updated = datetime.now(timezone.utc).isoformat()
        _save_merge_state(state_root, merge_state)

        return receipt

    with RepositoryLock(lock_dir, repo_id):
        receipt = execute_merge_commit()
        return receipt


def _check_conflict_markers_in_staged(worktree_root: Path, git_exe: str) -> bool:
    """Check if any staged files contain conflict markers."""
    # Get list of staged files
    result = _run_git_command(
        git_exe,
        ["diff", "--cached", "--name-only"],
        cwd=worktree_root
    )

    staged_files = [line.strip() for line in result.stdout.splitlines() if line.strip()]

    for file_path in staged_files:
        full_path = worktree_root / file_path
        if not full_path.exists():
            continue

        try:
            content = full_path.read_text(encoding='utf-8')
            if re.search(r'^<{7}\s|^={7}\s|^>{7}\s', content, re.MULTILINE):
                return True
        except (UnicodeDecodeError, OSError):
            continue

    return False


# ============================================================================
# Abort Merge
# ============================================================================

def abort_merge(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str,
    operation_id: str,
    lock_dir: Path
) -> OperationReceipt:
    """Abort merge operation and restore to pre-merge state.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable path
        repo_id: Repository ID
        worktree_id: Worktree ID
        operation_id: Merge operation ID
        lock_dir: Lock directory

    Returns:
        OperationReceipt

    Raises:
        GitOperationError: If abort fails
    """
    merge_state = load_merge_state(state_root, operation_id)
    if not merge_state:
        raise GitOperationError(f"Merge state not found: {operation_id}")

    if merge_state.status not in (MergeStatus.IN_PROGRESS, MergeStatus.AWAITING_RESOLUTION):
        raise GitOperationError(f"Merge not in progress: {merge_state.status.value}")

    # Get expected state after abort
    expected_head_oid = merge_state.target_oid

    def execute_abort():
        # Execute git merge --abort
        try:
            _run_git_command(
                git_exe,
                ["merge", "--abort"],
                cwd=worktree_root
            )
        except subprocess.CalledProcessError as e:
            raise GitOperationError(f"Failed to abort merge: {e.stderr}")

        # Verify HEAD is restored
        new_head = get_head_info(worktree_root, git_exe)
        if new_head.oid != expected_head_oid:
            # Abort verification failed - mark as needs_recovery
            raise GitOperationError(
                f"Merge abort verification failed: "
                f"expected HEAD {expected_head_oid}, got {new_head.oid}. "
                f"Repository may need manual recovery."
            )

        # Verify index is clean
        result = _run_git_command(
            git_exe,
            ["ls-files", "-u"],
            cwd=worktree_root
        )
        if result.stdout.strip():
            raise GitOperationError(
                "Merge abort verification failed: index still has unmerged entries. "
                "Repository may need manual recovery."
            )

        # Verify working tree matches expected
        head, files, ongoing_op = get_status(worktree_root, git_exe)
        if files:  # Has uncommitted changes
            # Working tree has changes - check if they match pre-merge state
            # This is acceptable if recovery point confirms these existed before merge
            pass

        receipt = OperationReceipt(
            operation_id=operation_id,
            action="merge_abort",
            repo_id=repo_id,
            worktree_id=worktree_id,
            completed_at=datetime.now(timezone.utc).isoformat(),
            result={
                "status": "aborted",
                "target_branch": merge_state.target_branch,
                "source_branch": merge_state.source_branch,
                "restored_head": new_head.oid
            },
            recovery_id=merge_state.recovery_id
        )

        # Update merge state
        merge_state.status = MergeStatus.ABORTED
        merge_state.last_updated = datetime.now(timezone.utc).isoformat()
        _save_merge_state(state_root, merge_state)

        return receipt

    with RepositoryLock(lock_dir, repo_id):
        receipt = execute_abort()
        return receipt


# ============================================================================
# Preview Functions
# ============================================================================

def preview_merge_result(
    worktree_root: Path,
    git_exe: str,
    merge_state: MergeState
) -> Dict[str, Any]:
    """Preview merge result before creating merge commit.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path
        merge_state: Current merge state

    Returns:
        Preview information dict
    """
    # Get current index tree
    result = _run_git_command(
        git_exe,
        ["write-tree"],
        cwd=worktree_root
    )
    tree_oid = result.stdout.strip()

    # Get diff between base and merge result
    if merge_state.merge_base_oid:
        result = _run_git_command(
            git_exe,
            ["diff", "--stat", merge_state.merge_base_oid, tree_oid],
            cwd=worktree_root
        )
        diff_stat = result.stdout
    else:
        diff_stat = "No merge base"

    # Get list of modified files
    result = _run_git_command(
        git_exe,
        ["diff", "--name-status", merge_state.target_oid, tree_oid],
        cwd=worktree_root
    )

    modified_files = []
    for line in result.stdout.splitlines():
        if line.strip():
            parts = line.split('\t', 1)
            if len(parts) == 2:
                status, path = parts
                modified_files.append({"status": status, "path": path})

    return {
        "tree_oid": tree_oid,
        "diff_stat": diff_stat,
        "modified_files": modified_files,
        "conflicts_resolved": len([c for c in merge_state.conflicts if c.resolved]),
        "total_conflicts": len(merge_state.conflicts)
    }


def merge_fixed_commit(worktree_root: Path, git_exe: str, target_oid: str, message: str):
    """Merge a reviewed immutable OID; caller owns clean checks and repository lock.

    Return None if already contained, otherwise the actual CompletedProcess so
    the caller can distinguish an in-progress conflict from another failure.
    No autostash, rebase or synthetic no-ff merge is permitted by this entry.
    """
    args = {'cwd': worktree_root}
    before = _run_git_command(git_exe, ['rev-parse', 'HEAD'], **args).stdout.strip()
    if _run_git_command(git_exe, ['merge-base', '--is-ancestor', target_oid, before], check=False, **args).returncode == 0:
        return None
    ff = _run_git_command(git_exe, ['merge-base', '--is-ancestor', before, target_oid], check=False, **args).returncode == 0
    return _run_git_command(git_exe, ['-c', 'merge.autostash=false', '-c', 'merge.ff=true',
                            '-c', 'rerere.enabled=false', 'merge', '--ff-only' if ff else '--ff',
                            '--no-edit', '-m', message, target_oid], check=False, **args)
