"""Git write operations with recovery point protection.

Implementation of T-08: Basic Git Operations.

Security requirements (S-20, S-21):
- All write operations must create recovery points first
- Use plan → execute → receipt pattern
- Concurrency lock protection
- Do not execute dangerous hooks
- Non-interactive command execution only

Acceptance tests: AT-32 through AT-37
"""

import hashlib
import json
import os
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

# Import from git_recovery
from git_recovery import (
    RecoveryError,
    RecoveryPoint,
    create_recovery_point,
    check_ignored_conflicts,
    reconcile_operation,
    load_recovery_point
)


class GitOperationError(GitError):
    """Git operation error."""
    pass


class GitDirtyWorktreeError(GitOperationError):
    """Worktree has uncommitted changes."""
    pass


class GitBranchExistsError(GitOperationError):
    """Branch already exists."""
    pass


class GitRefNotFoundError(GitOperationError):
    """Reference not found."""
    pass


class GitAuthRequiredError(GitOperationError):
    """Authentication required for operation."""
    pass


@dataclass
class OperationReceipt:
    """Receipt for completed operation."""
    operation_id: str
    action: str
    repo_id: str
    worktree_id: Optional[str]
    completed_at: str
    result: Dict[str, Any]
    recovery_id: Optional[str] = None


# ============================================================================
# Repository Initialization
# ============================================================================

def init_repository(
    target_dir: Path,
    git_exe: str,
    initial_branch: str = "main",
    author_name: Optional[str] = None,
    author_email: Optional[str] = None
) -> Dict[str, Any]:
    """Initialize a new Git repository.

    Args:
        target_dir: Target directory (must exist and be empty of Git)
        git_exe: Git executable path
        initial_branch: Initial branch name (default: "main")
        author_name: Optional author name (local config only)
        author_email: Optional author email (local config only)

    Returns:
        Dict with repo_id and initial_branch

    Raises:
        GitOperationError: If initialization fails
    """
    if not target_dir.exists():
        raise GitOperationError(f"Target directory does not exist: {target_dir}")

    # Check if already a Git repository
    result = _run_git_command(
        git_exe,
        ['rev-parse', '--git-dir'],
        cwd=target_dir,
        timeout=5.0,
        check=False
    )
    if result.returncode == 0:
        raise GitOperationError(f"Directory is already a Git repository: {target_dir}")

    # Validate branch name
    if not check_ref_format(initial_branch, git_exe):
        raise GitOperationError(f"Invalid branch name: {initial_branch}")

    try:
        # Initialize repository
        _run_git_command(
            git_exe,
            ['init', f'--initial-branch={initial_branch}'],
            cwd=target_dir,
            timeout=10.0
        )

        # Set local author config if provided
        if author_name:
            _run_git_command(
                git_exe,
                ['config', '--local', 'user.name', author_name],
                cwd=target_dir,
                timeout=5.0
            )

        if author_email:
            _run_git_command(
                git_exe,
                ['config', '--local', 'user.email', author_email],
                cwd=target_dir,
                timeout=5.0
            )

        # Generate repo_id and worktree_id
        from git_service import get_repository_paths, generate_repo_id, generate_worktree_id
        common_dir, _, _ = get_repository_paths(target_dir, git_exe)
        repo_id = generate_repo_id(common_dir)
        worktree_id = generate_worktree_id(target_dir)

        return {
            "repo_id": repo_id,
            "worktree_id": worktree_id,
            "common_dir": str(common_dir),
            "initial_branch": initial_branch
        }

    except subprocess.SubprocessError as e:
        raise GitOperationError(f"Failed to initialize repository: {e}")


def clone_repository(
    source_url: str,
    target_dir: Path,
    git_exe: str,
    branch: Optional[str] = None,
    recurse_submodules: bool = False
) -> Dict[str, Any]:
    """Clone a Git repository.

    Args:
        source_url: Source repository URL or path
        target_dir: Target directory (must not exist)
        git_exe: Git executable path
        branch: Optional specific branch to clone
        recurse_submodules: Whether to recurse submodules (default: False)

    Returns:
        Dict with repo_id and cloned_branch

    Raises:
        GitOperationError: If clone fails
        GitAuthRequiredError: If authentication is required
    """
    if target_dir.exists():
        raise GitOperationError(f"Target directory already exists: {target_dir}")

    # Validate URL scheme
    if source_url.startswith('ext::'):
        raise GitOperationError("ext:: protocol not allowed")

    # Check for embedded credentials
    if '@' in source_url and '://' in source_url:
        scheme_part = source_url.split('://')[0]
        if scheme_part in ('http', 'https', 'ssh'):
            # Check if @ is before the host (credentials)
            after_scheme = source_url.split('://')[1]
            if '@' in after_scheme.split('/')[0]:
                raise GitOperationError("URLs with embedded credentials not allowed")

    try:
        cmd = ['clone', '--progress']

        if branch:
            cmd.extend(['--branch', branch])

        if recurse_submodules:
            cmd.append('--recurse-submodules')

        cmd.extend([source_url, str(target_dir)])

        result = _run_git_command(
            git_exe,
            cmd,
            cwd=target_dir.parent,
            timeout=180.0
        )

        # Generate repo_id
        from git_service import get_repository_paths, generate_repo_id
        common_dir, _, _ = get_repository_paths(target_dir, git_exe)
        repo_id = generate_repo_id(common_dir)

        # Get current branch
        head = get_head_info(target_dir, git_exe)

        return {
            "repo_id": repo_id,
            "common_dir": str(common_dir),
            "cloned_branch": head.branch
        }

    except subprocess.CalledProcessError as e:
        stderr = e.stderr if hasattr(e, 'stderr') else ''
        if 'Authentication failed' in stderr or 'authentication required' in stderr.lower():
            raise GitAuthRequiredError(f"Authentication required for {source_url}")
        raise GitOperationError(f"Failed to clone repository: {e}")
    except subprocess.SubprocessError as e:
        raise GitOperationError(f"Failed to clone repository: {e}")


# ============================================================================
# Staging Operations
# ============================================================================

def stage_files(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str,
    file_paths: List[str],
    plan: GitPlan,
    lock_dir: Path
) -> OperationReceipt:
    """Stage files for commit.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable path
        repo_id: Repository ID
        worktree_id: Worktree ID
        file_paths: List of files to stage (relative paths)
        plan: Validated operation plan
        lock_dir: Lock directory

    Returns:
        OperationReceipt

    Raises:
        GitOperationError: If staging fails
    """
    operation_id = plan.operation_id

    with RepositoryLock(lock_dir, repo_id):
        # Verify plan is still valid
        head, files, _ = get_status(worktree_root, git_exe)
        current_token = compute_state_token(head, files, strength="full")
        valid, error = validate_plan_state(plan, current_token)

        if not valid:
            raise GitOperationError(f"Plan no longer valid: {error}")

        try:
            # Stage files using null-terminated pathspec
            for file_path in file_paths:
                _run_git_command(
                    git_exe,
                    ['add', '--', file_path],
                    cwd=worktree_root,
                    timeout=30.0
                )

            # Verify staging
            head_after, files_after, _ = get_status(worktree_root, git_exe)

            completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            return OperationReceipt(
                operation_id=operation_id,
                action="stage",
                repo_id=repo_id,
                worktree_id=worktree_id,
                completed_at=completed_at,
                result={
                    "staged_files": file_paths,
                    "head_oid": head_after.oid
                }
            )

        except subprocess.SubprocessError as e:
            raise GitOperationError(f"Failed to stage files: {e}")


def unstage_files(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str,
    file_paths: List[str],
    plan: GitPlan,
    lock_dir: Path
) -> OperationReceipt:
    """Unstage files.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable path
        repo_id: Repository ID
        worktree_id: Worktree ID
        file_paths: List of files to unstage (relative paths)
        plan: Validated operation plan
        lock_dir: Lock directory

    Returns:
        OperationReceipt

    Raises:
        GitOperationError: If unstaging fails
    """
    operation_id = plan.operation_id

    with RepositoryLock(lock_dir, repo_id):
        # Verify plan is still valid
        head, files, _ = get_status(worktree_root, git_exe)
        current_token = compute_state_token(head, files, strength="full")
        valid, error = validate_plan_state(plan, current_token)

        if not valid:
            raise GitOperationError(f"Plan no longer valid: {error}")

        try:
            # Check if HEAD exists
            if head.unborn:
                # Unborn HEAD: remove from index without restoring
                for file_path in file_paths:
                    _run_git_command(
                        git_exe,
                        ['rm', '--cached', '--', file_path],
                        cwd=worktree_root,
                        timeout=30.0,
                        check=False
                    )
            else:
                # Normal unstage: restore index from HEAD
                for file_path in file_paths:
                    _run_git_command(
                        git_exe,
                        ['restore', '--staged', '--', file_path],
                        cwd=worktree_root,
                        timeout=30.0
                    )

            # Verify unstaging
            head_after, files_after, _ = get_status(worktree_root, git_exe)

            completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            return OperationReceipt(
                operation_id=operation_id,
                action="unstage",
                repo_id=repo_id,
                worktree_id=worktree_id,
                completed_at=completed_at,
                result={
                    "unstaged_files": file_paths,
                    "head_oid": head_after.oid
                }
            )

        except subprocess.SubprocessError as e:
            raise GitOperationError(f"Failed to unstage files: {e}")


# ============================================================================
# Commit Operation
# ============================================================================

def commit_changes(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str,
    message: str,
    author_name: Optional[str] = None,
    author_email: Optional[str] = None,
    plan: Optional[GitPlan] = None,
    lock_dir: Optional[Path] = None
) -> OperationReceipt:
    """Create a commit.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable path
        repo_id: Repository ID
        worktree_id: Worktree ID
        message: Commit message (1-4000 characters)
        author_name: Optional author name override
        author_email: Optional author email override
        plan: Optional validated operation plan
        lock_dir: Optional lock directory

    Returns:
        OperationReceipt with new commit OID

    Raises:
        GitOperationError: If commit fails
    """
    # Validate message
    if not message or len(message) > 4000:
        raise GitOperationError("Commit message must be 1-4000 characters")

    operation_id = plan.operation_id if plan else generate_operation_id()

    # Use lock if provided
    lock_ctx = RepositoryLock(lock_dir, repo_id) if lock_dir else _dummy_context()

    with lock_ctx:
        # Verify plan if provided
        if plan:
            head, files, _ = get_status(worktree_root, git_exe)
            current_token = compute_state_token(head, files, strength="full")
            valid, error = validate_plan_state(plan, current_token)

            if not valid:
                raise GitOperationError(f"Plan no longer valid: {error}")

        try:
            # Build commit command
            cmd = ['commit', '-m', message]

            # Add author override if provided
            if author_name and author_email:
                cmd.extend(['--author', f'{author_name} <{author_email}>'])

            result = _run_git_command(
                git_exe,
                cmd,
                cwd=worktree_root,
                timeout=60.0
            )

            # Get new commit OID
            head_after = get_head_info(worktree_root, git_exe)

            if not head_after.oid:
                raise GitOperationError("Commit succeeded but no OID found")

            completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            return OperationReceipt(
                operation_id=operation_id,
                action="commit",
                repo_id=repo_id,
                worktree_id=worktree_id,
                completed_at=completed_at,
                result={
                    "commit_oid": head_after.oid,
                    "branch": head_after.branch,
                    "message": message
                }
            )

        except subprocess.CalledProcessError as e:
            stderr = e.stderr if hasattr(e, 'stderr') else ''
            if 'nothing to commit' in stderr:
                raise GitOperationError("Nothing to commit (index is empty)")
            raise GitOperationError(f"Failed to commit: {e}")
        except subprocess.SubprocessError as e:
            raise GitOperationError(f"Failed to commit: {e}")


class _dummy_context:
    """Dummy context manager for optional lock."""
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


# ============================================================================
# Branch Operations
# ============================================================================

def create_branch(
    worktree_root: Path,
    git_exe: str,
    branch_name: str,
    start_point: Optional[str] = None
) -> Dict[str, Any]:
    """Create a new branch.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path
        branch_name: New branch name
        start_point: Optional starting point (commit OID or ref)

    Returns:
        Dict with branch_name and start_oid

    Raises:
        GitBranchExistsError: If branch already exists
        GitOperationError: If creation fails
    """
    # Validate branch name
    if not check_ref_format(branch_name, git_exe):
        raise GitOperationError(f"Invalid branch name: {branch_name}")

    # Check if branch already exists
    try:
        result = _run_git_command(
            git_exe,
            ['rev-parse', '--verify', f'refs/heads/{branch_name}'],
            cwd=worktree_root,
            timeout=5.0,
            check=False
        )
        if result.returncode == 0:
            raise GitBranchExistsError(f"Branch already exists: {branch_name}")
    except subprocess.SubprocessError:
        pass

    try:
        # Resolve start point
        if start_point:
            result = _run_git_command(
                git_exe,
                ['rev-parse', '--verify', f'{start_point}^{{commit}}'],
                cwd=worktree_root,
                timeout=5.0
            )
            start_oid = result.stdout.strip()
        else:
            # Use HEAD
            head = get_head_info(worktree_root, git_exe)
            if not head.oid:
                raise GitOperationError("Cannot create branch from unborn HEAD")
            start_oid = head.oid

        # Create branch
        _run_git_command(
            git_exe,
            ['branch', branch_name, start_oid],
            cwd=worktree_root,
            timeout=10.0
        )

        return {
            "branch_name": branch_name,
            "start_oid": start_oid
        }

    except subprocess.SubprocessError as e:
        raise GitOperationError(f"Failed to create branch: {e}")


def switch_branch(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str,
    branch_name: str,
    plan: GitPlan,
    lock_dir: Path
) -> OperationReceipt:
    """Switch to a different branch.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable path
        repo_id: Repository ID
        worktree_id: Worktree ID
        branch_name: Target branch name
        plan: Validated operation plan (includes recovery point)
        lock_dir: Lock directory

    Returns:
        OperationReceipt

    Raises:
        GitDirtyWorktreeError: If worktree is dirty
        GitOperationError: If switch fails
    """
    operation_id = plan.operation_id

    with RepositoryLock(lock_dir, repo_id):
        # Verify plan is still valid
        head, files, _ = get_status(worktree_root, git_exe)
        current_token = compute_state_token(head, files, strength="full")
        valid, error = validate_plan_state(plan, current_token)

        if not valid:
            raise GitOperationError(f"Plan no longer valid: {error}")

        # Check if worktree is clean
        if files:
            raise GitDirtyWorktreeError(
                f"Worktree has {len(files)} uncommitted changes. "
                "Please commit, stash, or discard changes first."
            )

        try:
            # Verify target branch exists
            result = _run_git_command(
                git_exe,
                ['rev-parse', '--verify', f'refs/heads/{branch_name}'],
                cwd=worktree_root,
                timeout=5.0
            )
            target_oid = result.stdout.strip()

            # Switch branch
            _run_git_command(
                git_exe,
                ['switch', branch_name],
                cwd=worktree_root,
                timeout=30.0
            )

            # Verify switch
            head_after = get_head_info(worktree_root, git_exe)
            if head_after.branch != branch_name:
                raise GitOperationError(f"Switch failed: on {head_after.branch}, expected {branch_name}")

            completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            return OperationReceipt(
                operation_id=operation_id,
                action="switch_branch",
                repo_id=repo_id,
                worktree_id=worktree_id,
                completed_at=completed_at,
                result={
                    "branch": branch_name,
                    "oid": head_after.oid
                },
                recovery_id=plan.params.get("recovery_id")
            )

        except subprocess.SubprocessError as e:
            raise GitOperationError(f"Failed to switch branch: {e}")


def delete_branch(
    worktree_root: Path,
    git_exe: str,
    branch_name: str,
    force: bool = False
) -> Dict[str, Any]:
    """Delete a local branch.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path
        branch_name: Branch to delete
        force: Force delete even if not fully merged (default: False)

    Returns:
        Dict with deleted_branch and last_oid

    Raises:
        GitOperationError: If deletion fails
    """
    try:
        # Get branch OID before deletion
        result = _run_git_command(
            git_exe,
            ['rev-parse', '--verify', f'refs/heads/{branch_name}'],
            cwd=worktree_root,
            timeout=5.0
        )
        last_oid = result.stdout.strip()

        # Delete branch
        flag = '-D' if force else '-d'
        _run_git_command(
            git_exe,
            ['branch', flag, branch_name],
            cwd=worktree_root,
            timeout=10.0
        )

        return {
            "deleted_branch": branch_name,
            "last_oid": last_oid
        }

    except subprocess.CalledProcessError as e:
        stderr = e.stderr if hasattr(e, 'stderr') else ''
        if 'not fully merged' in stderr:
            raise GitOperationError(
                f"Branch '{branch_name}' is not fully merged. "
                "Use force=True to delete anyway."
            )
        raise GitOperationError(f"Failed to delete branch: {e}")
    except subprocess.SubprocessError as e:
        raise GitOperationError(f"Failed to delete branch: {e}")


# ============================================================================
# Tag Operations
# ============================================================================

def create_tag(
    worktree_root: Path,
    git_exe: str,
    tag_name: str,
    target_oid: Optional[str] = None,
    message: Optional[str] = None
) -> Dict[str, Any]:
    """Create a tag.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path
        tag_name: Tag name
        target_oid: Optional target commit OID (default: HEAD)
        message: Optional message (creates annotated tag if provided)

    Returns:
        Dict with tag_name and target_oid

    Raises:
        GitOperationError: If creation fails
    """
    # Validate tag name
    if not check_ref_format(tag_name, git_exe):
        raise GitOperationError(f"Invalid tag name: {tag_name}")

    try:
        # Check if tag already exists
        result = _run_git_command(
            git_exe,
            ['rev-parse', '--verify', f'refs/tags/{tag_name}'],
            cwd=worktree_root,
            timeout=5.0,
            check=False
        )
        if result.returncode == 0:
            raise GitOperationError(f"Tag already exists: {tag_name}")

        # Resolve target
        if target_oid:
            result = _run_git_command(
                git_exe,
                ['rev-parse', '--verify', f'{target_oid}^{{commit}}'],
                cwd=worktree_root,
                timeout=5.0
            )
            resolved_oid = result.stdout.strip()
        else:
            head = get_head_info(worktree_root, git_exe)
            if not head.oid:
                raise GitOperationError("Cannot create tag from unborn HEAD")
            resolved_oid = head.oid

        # Create tag
        if message:
            # Annotated tag
            _run_git_command(
                git_exe,
                ['tag', '-a', tag_name, resolved_oid, '-m', message],
                cwd=worktree_root,
                timeout=10.0
            )
        else:
            # Lightweight tag
            _run_git_command(
                git_exe,
                ['tag', tag_name, resolved_oid],
                cwd=worktree_root,
                timeout=10.0
            )

        return {
            "tag_name": tag_name,
            "target_oid": resolved_oid,
            "annotated": message is not None
        }

    except subprocess.SubprocessError as e:
        raise GitOperationError(f"Failed to create tag: {e}")


def delete_tag(
    worktree_root: Path,
    git_exe: str,
    tag_name: str
) -> Dict[str, Any]:
    """Delete a tag.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path
        tag_name: Tag to delete

    Returns:
        Dict with deleted_tag and target_oid

    Raises:
        GitOperationError: If deletion fails
    """
    try:
        # Get tag target before deletion
        result = _run_git_command(
            git_exe,
            ['rev-parse', '--verify', f'refs/tags/{tag_name}'],
            cwd=worktree_root,
            timeout=5.0
        )
        target_oid = result.stdout.strip()

        # Delete tag
        _run_git_command(
            git_exe,
            ['tag', '-d', tag_name],
            cwd=worktree_root,
            timeout=10.0
        )

        return {
            "deleted_tag": tag_name,
            "target_oid": target_oid
        }

    except subprocess.SubprocessError as e:
        raise GitOperationError(f"Failed to delete tag: {e}")


# ============================================================================
# Remote Operations
# ============================================================================

def add_remote(
    worktree_root: Path,
    git_exe: str,
    remote_name: str,
    remote_url: str
) -> Dict[str, Any]:
    """Add a remote.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path
        remote_name: Remote name
        remote_url: Remote URL

    Returns:
        Dict with remote_name and remote_url

    Raises:
        GitOperationError: If addition fails
    """
    # Validate URL
    if remote_url.startswith('ext::'):
        raise GitOperationError("ext:: protocol not allowed")

    # Check for embedded credentials
    if '@' in remote_url and '://' in remote_url:
        scheme_part = remote_url.split('://')[0]
        if scheme_part in ('http', 'https', 'ssh'):
            after_scheme = remote_url.split('://')[1]
            if '@' in after_scheme.split('/')[0]:
                raise GitOperationError("URLs with embedded credentials not allowed")

    try:
        # Add remote
        _run_git_command(
            git_exe,
            ['remote', 'add', remote_name, remote_url],
            cwd=worktree_root,
            timeout=10.0
        )

        return {
            "remote_name": remote_name,
            "remote_url": remote_url
        }

    except subprocess.CalledProcessError as e:
        stderr = e.stderr if hasattr(e, 'stderr') else ''
        if 'already exists' in stderr:
            raise GitOperationError(f"Remote already exists: {remote_name}")
        raise GitOperationError(f"Failed to add remote: {e}")
    except subprocess.SubprocessError as e:
        raise GitOperationError(f"Failed to add remote: {e}")


def remove_remote(
    worktree_root: Path,
    git_exe: str,
    remote_name: str
) -> Dict[str, Any]:
    """Remove a remote.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path
        remote_name: Remote name to remove

    Returns:
        Dict with removed_remote

    Raises:
        GitOperationError: If removal fails
    """
    try:
        # Remove remote
        _run_git_command(
            git_exe,
            ['remote', 'remove', remote_name],
            cwd=worktree_root,
            timeout=10.0
        )

        return {
            "removed_remote": remote_name
        }

    except subprocess.SubprocessError as e:
        raise GitOperationError(f"Failed to remove remote: {e}")


def fetch_remote(
    worktree_root: Path,
    git_exe: str,
    remote_name: str,
    refspec: Optional[str] = None,
    prune: bool = False
) -> Dict[str, Any]:
    """Fetch from a remote (does not modify worktree).

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path
        remote_name: Remote name
        refspec: Optional specific refspec to fetch
        prune: Whether to prune deleted remote refs (default: False)

    Returns:
        Dict with remote_name and fetched refs

    Raises:
        GitAuthRequiredError: If authentication is required
        GitOperationError: If fetch fails
    """
    try:
        cmd = ['fetch', remote_name]

        if prune:
            cmd.append('--prune')

        if refspec:
            cmd.append(refspec)

        result = _run_git_command(
            git_exe,
            cmd,
            cwd=worktree_root,
            timeout=180.0
        )

        # Get updated remote refs
        result = _run_git_command(
            git_exe,
            ['for-each-ref', '--format=%(refname)', f'refs/remotes/{remote_name}/'],
            cwd=worktree_root,
            timeout=10.0
        )

        refs = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]

        return {
            "remote_name": remote_name,
            "fetched_refs": refs
        }

    except subprocess.CalledProcessError as e:
        stderr = e.stderr if hasattr(e, 'stderr') else ''
        if 'Authentication failed' in stderr or 'authentication required' in stderr.lower():
            raise GitAuthRequiredError(f"Authentication required for remote '{remote_name}'")
        raise GitOperationError(f"Failed to fetch: {e}")
    except subprocess.SubprocessError as e:
        raise GitOperationError(f"Failed to fetch: {e}")


def push_to_remote(
    worktree_root: Path,
    git_exe: str,
    repo_id: str,
    remote_name: str,
    refspec: str,
    lock_dir: Path,
    force: bool = False
) -> Dict[str, Any]:
    """Push to a remote.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path
        repo_id: Repository ID
        remote_name: Remote name
        refspec: Refspec to push (e.g., "main:main")
        lock_dir: Lock directory
        force: Force push (default: False, USE WITH CAUTION)

    Returns:
        Dict with remote_name, refspec, and result

    Raises:
        GitAuthRequiredError: If authentication is required
        GitOperationError: If push fails
    """
    with RepositoryLock(lock_dir, repo_id):
        try:
            cmd = ['push', remote_name, refspec]

            if force:
                cmd.append('--force')

            result = _run_git_command(
                git_exe,
                cmd,
                cwd=worktree_root,
                timeout=180.0
            )

            return {
                "remote_name": remote_name,
                "refspec": refspec,
                "forced": force
            }

        except subprocess.CalledProcessError as e:
            stderr = e.stderr if hasattr(e, 'stderr') else ''
            if 'Authentication failed' in stderr or 'authentication required' in stderr.lower():
                raise GitAuthRequiredError(f"Authentication required for remote '{remote_name}'")
            if 'rejected' in stderr and not force:
                raise GitOperationError(
                    f"Push rejected. Remote has changes not present locally. "
                    "Fetch and merge first, or use force=True (dangerous)."
                )
            raise GitOperationError(f"Failed to push: {e}")
        except subprocess.SubprocessError as e:
            raise GitOperationError(f"Failed to push: {e}")


# ============================================================================
# Author Configuration
# ============================================================================

def set_author(
    worktree_root: Path,
    git_exe: str,
    author_name: str,
    author_email: str,
    scope: str = "local"
) -> Dict[str, Any]:
    """Set Git author configuration.

    Args:
        worktree_root: Git worktree root
        git_exe: Git executable path
        author_name: Author name
        author_email: Author email
        scope: Configuration scope ("local", "global", or "system")

    Returns:
        Dict with author_name, author_email, and scope

    Raises:
        GitOperationError: If configuration fails
    """
    if scope not in ("local", "global", "system"):
        raise GitOperationError(f"Invalid scope: {scope}")

    try:
        # Set user.name
        _run_git_command(
            git_exe,
            ['config', f'--{scope}', 'user.name', author_name],
            cwd=worktree_root,
            timeout=5.0
        )

        # Set user.email
        _run_git_command(
            git_exe,
            ['config', f'--{scope}', 'user.email', author_email],
            cwd=worktree_root,
            timeout=5.0
        )

        return {
            "author_name": author_name,
            "author_email": author_email,
            "scope": scope
        }

    except subprocess.SubprocessError as e:
        raise GitOperationError(f"Failed to set author: {e}")


# ============================================================================
# High-level Operation Workflows
# ============================================================================

def execute_with_recovery(
    worktree_root: Path,
    state_root: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str,
    operation_fn,
    plan: GitPlan,
    lock_dir: Path,
    create_recovery: bool = True
) -> OperationReceipt:
    """Execute an operation with recovery point protection.

    Args:
        worktree_root: Git worktree root
        state_root: Project state root
        git_exe: Git executable path
        repo_id: Repository ID
        worktree_id: Worktree ID
        operation_fn: Operation function to execute
        plan: Operation plan
        lock_dir: Lock directory
        create_recovery: Whether to create recovery point

    Returns:
        OperationReceipt

    Raises:
        GitOperationError: If operation fails
    """
    recovery_id = None

    try:
        # Create recovery point if requested
        if create_recovery:
            recovery_point = create_recovery_point(
                worktree_root=worktree_root,
                state_root=state_root,
                git_exe=git_exe,
                operation_id=plan.operation_id,
                repo_id=repo_id,
                worktree_id=worktree_id
            )
            recovery_id = recovery_point.recovery_id

        # Execute operation
        receipt = operation_fn()

        # Add recovery_id to receipt if created
        if recovery_id:
            receipt.recovery_id = recovery_id

        return receipt

    except Exception as e:
        # On failure, recovery point remains for manual restoration
        if recovery_id:
            raise GitOperationError(
                f"Operation failed: {e}. "
                f"Recovery point available: {recovery_id}"
            )
        raise


def commit_selected_files(worktree_root: Path, git_exe: str, paths: List[str], message: str) -> str:
    """Commit complete literal paths, preserving unrelated staged entries.

    The web adapter owns preview validation and the repository lock. A failure
    deliberately leaves the selected staging intact; never reset the repository.
    """
    if not paths or not message.strip():
        raise GitOperationError("请选择文件并填写版本说明。")
    unique = list(dict.fromkeys(paths))
    data = b"".join(path.encode("utf-8") + b"\0" for path in unique)
    env = {"GIT_LITERAL_PATHSPECS": "1"}
    options = ["--pathspec-from-file=-", "--pathspec-file-nul"]
    tracked = set(_run_git_command(git_exe, ["ls-files", "-z"], cwd=worktree_root,
                                   text=False).stdout.split(b"\0"))
    # An already-staged deletion / rename source no longer exists in the index.
    # add rejects that path, while commit --only must still include its deletion.
    stage = [path for path in unique if path.encode("utf-8") in tracked
             or (worktree_root / path).exists() or (worktree_root / path).is_symlink()]
    if stage:
        _run_git_command(git_exe, ["add", "-A", *options], cwd=worktree_root,
                         input_data=b"".join(path.encode("utf-8") + b"\0" for path in stage),
                         text=False, env_override=env)
    _run_git_command(git_exe, ["commit", "--only", "-m", message, *options],
                     cwd=worktree_root, input_data=data, text=False, env_override=env)
    return _run_git_command(git_exe, ["rev-parse", "HEAD"], cwd=worktree_root).stdout.strip()
