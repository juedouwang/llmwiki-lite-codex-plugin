"""Git repository detection and read-only operations.

Security requirements (S-20, S-21):
- All Git commands must be non-interactive
- Path traversal protection
- Do not execute dangerous hooks
- Concurrency lock protection
"""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Minimum required Git version
MIN_GIT_VERSION = (2, 43, 0)


@dataclass
class GitCapabilities:
    """Git installation capabilities."""
    git_executable: str
    version: Tuple[int, int, int]
    supports_sha256: bool = False
    can_read: bool = True
    can_write: bool = False
    reasons: List[str] = field(default_factory=list)


@dataclass
class GitIdentity:
    """Git user identity configuration."""
    name: Optional[str] = None
    email: Optional[str] = None
    source: str = "unknown"  # local, global, system, missing


@dataclass
class GitRepository:
    """Git repository identity."""
    repo_id: str
    project_id: str
    common_dir: Path
    object_format: str  # "sha1" or "sha256"
    worktree_root: Optional[Path] = None
    is_bare: bool = False


@dataclass
class GitWorktree:
    """Git worktree identity."""
    worktree_id: str
    repo_id: str
    root_path: Path
    is_main: bool = False


@dataclass
class GitHead:
    """Git HEAD information."""
    oid: Optional[str] = None
    branch: Optional[str] = None
    unborn: bool = False
    detached: bool = False


@dataclass
class GitFileStatus:
    """Git file status."""
    file_id: str
    path: str
    old_path: Optional[str] = None
    index_status: str = ""  # A, M, D, R, C, U, etc.
    worktree_status: str = ""
    is_binary: bool = False
    partial_staged: bool = False
    size_bytes: Optional[int] = None


@dataclass
class GitBranch:
    """Git branch information."""
    name: str
    oid: str
    current: bool = False
    upstream: Optional[str] = None
    ahead: Optional[int] = None
    behind: Optional[int] = None
    occupied_worktree_id: Optional[str] = None


@dataclass
class GitRemote:
    """Git remote information."""
    id: str
    name: str
    display_url: str
    last_fetch_at: Optional[str] = None


@dataclass
class GitStatus:
    """Complete Git repository status."""
    repo_id: str
    worktree_id: str
    head: GitHead
    files: List[GitFileStatus] = field(default_factory=list)
    branches: List[GitBranch] = field(default_factory=list)
    remotes: List[GitRemote] = field(default_factory=list)
    ongoing_operation: Optional[str] = None
    capabilities: Dict[str, Any] = field(default_factory=dict)
    state_token: str = ""
    state_token_strength: str = "lightweight"


class GitError(Exception):
    """Base Git error."""
    pass


class GitNotFoundError(GitError):
    """Git executable not found."""
    pass


class GitVersionError(GitError):
    """Git version too old."""
    pass


class GitRepositoryError(GitError):
    """Repository-related error."""
    pass


class GitLockError(GitError):
    """Repository lock error."""
    pass


def detect_git_executable() -> str:
    """Detect Git executable path.

    Returns:
        Absolute path to git executable

    Raises:
        GitNotFoundError: If git is not found
    """
    git_exe = shutil.which("git")
    if not git_exe:
        raise GitNotFoundError("Git executable not found in PATH")

    # Get absolute path
    return str(Path(git_exe).resolve())


def get_git_version(git_exe: str) -> Tuple[int, int, int]:
    """Get Git version.

    Args:
        git_exe: Path to git executable

    Returns:
        Version tuple (major, minor, patch)

    Raises:
        GitVersionError: If version cannot be determined
    """
    try:
        result = _run_git_command(
            git_exe,
            ["--version"],
            timeout=5.0
        )
        # Parse "git version 2.43.0" or "git version 2.43.0.windows.1"
        match = re.search(r'git version (\d+)\.(\d+)\.(\d+)', result.stdout)
        if not match:
            raise GitVersionError(f"Cannot parse git version: {result.stdout}")

        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))

        if version < MIN_GIT_VERSION:
            raise GitVersionError(
                f"Git version {version[0]}.{version[1]}.{version[2]} is too old. "
                f"Minimum required: {MIN_GIT_VERSION[0]}.{MIN_GIT_VERSION[1]}.{MIN_GIT_VERSION[2]}"
            )

        return version
    except subprocess.SubprocessError as e:
        raise GitVersionError(f"Failed to get git version: {e}")


def detect_git_capabilities(git_exe: Optional[str] = None) -> GitCapabilities:
    """Detect Git installation capabilities.

    Args:
        git_exe: Optional path to git executable

    Returns:
        GitCapabilities object
    """
    if git_exe is None:
        try:
            git_exe = detect_git_executable()
        except GitNotFoundError as e:
            return GitCapabilities(
                git_executable="",
                version=(0, 0, 0),
                can_read=False,
                can_write=False,
                reasons=[str(e)]
            )

    try:
        version = get_git_version(git_exe)
    except GitVersionError as e:
        return GitCapabilities(
            git_executable=git_exe,
            version=(0, 0, 0),
            can_read=False,
            can_write=False,
            reasons=[str(e)]
        )

    # Check SHA256 support (Git 2.42+)
    supports_sha256 = version >= (2, 42, 0)

    return GitCapabilities(
        git_executable=git_exe,
        version=version,
        supports_sha256=supports_sha256,
        can_read=True,
        can_write=True
    )


def _run_git_command(
    git_exe: str,
    args: List[str],
    cwd: Optional[Path] = None,
    timeout: float = 30.0,
    check: bool = True,
    env_override: Optional[Dict[str, str]] = None,
    input_data: Optional[Union[str, bytes]] = None,
    text: bool = True
) -> subprocess.CompletedProcess:
    """Run Git without a shell, terminal, interactive prompts or optional writes.

    Existing positional arguments remain compatible. ``input_data`` is a str in
    text mode (including NUL separators), or bytes with ``text=False``. Text I/O
    uses UTF-8 with surrogateescape so unusual filename bytes round-trip; binary
    mode does not decode or translate newlines. Network callers may pass 90s.

    Hooks, signing, filters and custom SSH commands are not bypassed. Callers
    must reject write/network operations whose custom executors cannot safely
    run non-interactively; CREATE_NO_WINDOW cannot constrain their descendants.
    """
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    # These are safety invariants, including for callers supplying author dates.
    env.update({
        'GIT_OPTIONAL_LOCKS': '0',
        'GIT_TERMINAL_PROMPT': '0',
        'GCM_INTERACTIVE': 'never',
        'GCM_GUI_PROMPT': '0',
        'GIT_ASKPASS': 'false',
        'SSH_ASKPASS': 'false',
        'SSH_ASKPASS_REQUIRE': 'never',
        'GIT_PAGER': 'cat',
        'PAGER': 'cat',
        'GIT_EDITOR': 'true',
        'GIT_SEQUENCE_EDITOR': 'true',
        'GIT_MERGE_AUTOEDIT': 'no',
        'LANG': 'C',
        'LC_ALL': 'C'
    })
    kwargs: Dict[str, Any] = {
        'check': check,
        'capture_output': True,
        'text': text,
        'timeout': timeout,
        'env': env,
        'shell': False
    }
    if text:
        kwargs.update(encoding='utf-8', errors='surrogateescape')
    if input_data is None:
        kwargs['stdin'] = subprocess.DEVNULL
    else:
        kwargs['input'] = input_data
    if cwd is not None:
        kwargs['cwd'] = str(cwd)
    if platform.system() == 'Windows':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    # git diff refreshes the index even with GIT_OPTIONAL_LOCKS=0 on some
    # versions. Disable that optimization, not hooks, signatures or filters.
    return subprocess.run([git_exe, '-c', 'diff.autoRefreshIndex=false'] + args, **kwargs)


def is_git_repository(path: Path, git_exe: str) -> bool:
    """Check if path is inside a Git repository.

    Args:
        path: Path to check
        git_exe: Path to git executable

    Returns:
        True if path is in a git repository
    """
    try:
        _run_git_command(
            git_exe,
            ['rev-parse', '--git-dir'],
            cwd=path,
            timeout=5.0
        )
        return True
    except subprocess.SubprocessError:
        return False


def get_repository_paths(repo_path: Path, git_exe: str) -> Tuple[Path, Path, bool]:
    """Get repository common-dir and worktree root.

    Args:
        repo_path: Path inside repository
        git_exe: Path to git executable

    Returns:
        Tuple of (common_dir, worktree_root, is_bare)

    Raises:
        GitRepositoryError: If paths cannot be determined
    """
    try:
        # Get worktree root (or bare repo path)
        result = _run_git_command(
            git_exe,
            ['rev-parse', '--show-toplevel'],
            cwd=repo_path,
            timeout=5.0,
            check=False
        )

        is_bare = result.returncode != 0

        if is_bare:
            # For bare repos, use --git-dir
            result = _run_git_command(
                git_exe,
                ['rev-parse', '--git-dir'],
                cwd=repo_path,
                timeout=5.0
            )
            worktree_root = None
            git_dir = Path(result.stdout.strip()).resolve()
        else:
            worktree_root = Path(result.stdout.strip()).resolve()
            # Get git common dir
            result = _run_git_command(
                git_exe,
                ['rev-parse', '--git-common-dir'],
                cwd=worktree_root,
                timeout=5.0
            )
            git_common = result.stdout.strip()

            # Resolve relative path
            if not Path(git_common).is_absolute():
                result_git_dir = _run_git_command(
                    git_exe,
                    ['rev-parse', '--git-dir'],
                    cwd=worktree_root,
                    timeout=5.0
                )
                git_dir_path = Path(result_git_dir.stdout.strip())
                if not git_dir_path.is_absolute():
                    git_dir_path = worktree_root / git_dir_path
                git_dir = (git_dir_path / '..' / git_common).resolve()
            else:
                git_dir = Path(git_common).resolve()

        return git_dir, worktree_root, is_bare

    except subprocess.SubprocessError as e:
        raise GitRepositoryError(f"Failed to get repository paths: {e}")


def get_object_format(common_dir: Path, git_exe: str) -> str:
    """Get repository object format (sha1 or sha256).

    Args:
        common_dir: Repository common directory
        git_exe: Path to git executable

    Returns:
        "sha1" or "sha256"
    """
    try:
        result = _run_git_command(
            git_exe,
            ['rev-parse', '--show-object-format'],
            cwd=common_dir,
            timeout=5.0,
            check=False
        )

        if result.returncode == 0:
            format_str = result.stdout.strip()
            if format_str in ('sha1', 'sha256'):
                return format_str

        # Fallback: assume sha1
        return 'sha1'

    except subprocess.SubprocessError:
        return 'sha1'


def generate_repo_id(common_dir: Path) -> str:
    """Generate repository ID from common-dir path.

    Args:
        common_dir: Repository common directory (must be resolved)

    Returns:
        Repository ID (hex string)
    """
    # Canonical path representation
    canonical = str(common_dir).replace('\\', '/')
    hash_bytes = hashlib.sha256(canonical.encode('utf-8')).digest()
    return hash_bytes[:16].hex()


def generate_worktree_id(root_path: Path) -> str:
    """Generate worktree ID from root path.

    Args:
        root_path: Worktree root directory (must be resolved)

    Returns:
        Worktree ID (hex string)
    """
    canonical = str(root_path).replace('\\', '/')
    hash_bytes = hashlib.sha256(canonical.encode('utf-8')).digest()
    return hash_bytes[:16].hex()


def get_git_identity(repo_path: Path, git_exe: str) -> GitIdentity:
    """Get Git user identity configuration.

    Args:
        repo_path: Repository path
        git_exe: Path to git executable

    Returns:
        GitIdentity object
    """
    identity = GitIdentity()

    # Try to get local config first
    for scope in ['local', 'global', 'system']:
        try:
            # Get user.name
            result = _run_git_command(
                git_exe,
                ['config', f'--{scope}', '--get', 'user.name'],
                cwd=repo_path,
                timeout=5.0,
                check=False
            )
            if result.returncode == 0 and result.stdout.strip():
                identity.name = result.stdout.strip()
                identity.source = scope

            # Get user.email
            result = _run_git_command(
                git_exe,
                ['config', f'--{scope}', '--get', 'user.email'],
                cwd=repo_path,
                timeout=5.0,
                check=False
            )
            if result.returncode == 0 and result.stdout.strip():
                identity.email = result.stdout.strip()
                if not identity.name:
                    identity.source = scope

            # If we found both, stop
            if identity.name and identity.email:
                break

        except subprocess.SubprocessError:
            continue

    if not identity.name and not identity.email:
        identity.source = "missing"

    return identity


def get_head_info(repo_path: Path, git_exe: str) -> GitHead:
    """Read OID and symbolic name together; unborn branches use symbolic-ref."""
    head = GitHead()
    try:
        result = _run_git_command(
            git_exe, ['rev-parse', '--revs-only', 'HEAD', '--symbolic-full-name', 'HEAD'],
            cwd=repo_path, timeout=5.0, check=False
        )
        values = result.stdout.splitlines()
        if result.returncode == 0 and values and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', values[0]):
            head.oid = values[0]
            ref = values[1] if len(values) > 1 else ''
            head.branch = ref[len('refs/heads/'):] if ref.startswith('refs/heads/') else None
            head.detached = ref in {'', 'HEAD'}
            return head
        head.unborn = True
        result = _run_git_command(git_exe, ['symbolic-ref', 'HEAD'], cwd=repo_path, timeout=5.0, check=False)
        ref = result.stdout.strip()
        if result.returncode == 0 and ref.startswith('refs/heads/'):
            head.branch = ref[len('refs/heads/'):]
    except subprocess.SubprocessError:
        pass
    return head


def get_status(
    repo_path: Path,
    git_exe: str,
    include_files: bool = True,
    max_files: int = 200
) -> Tuple[GitHead, List[GitFileStatus], Optional[str]]:
    """Get repository status.

    Args:
        repo_path: Repository path
        git_exe: Path to git executable
        include_files: Whether to include file list
        max_files: Maximum number of files to return

    Returns:
        Tuple of (head, files, ongoing_operation)
    """
    head = get_head_info(repo_path, git_exe)
    files = []
    ongoing_operation = None

    if not include_files:
        return head, files, ongoing_operation

    try:
        # Run git status with porcelain v2 format
        result = _run_git_command(
            git_exe,
            [
                'status',
                '--porcelain=v2',
                '-z',
                '--branch',
                '--untracked-files=all'
            ],
            cwd=repo_path,
            timeout=10.0
        )

        # Parse null-terminated output
        entries = result.stdout.split('\0')
        file_count = 0

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

                    file_id = _generate_file_id(path, index_status, worktree_status)

                    files.append(GitFileStatus(
                        file_id=file_id,
                        path=path,
                        index_status=index_status,
                        worktree_status=worktree_status
                    ))

                    file_count += 1
                    if file_count >= max_files:
                        break

            # Parse renamed/copied entries
            # Format: 2 XY sub <mH> <mI> <mW> <hH> <hI> <X><score> <path><sep><origPath>
            elif entry.startswith('2 '):
                parts = entry.split(' ')
                if len(parts) >= 10:
                    xy = parts[1]
                    path_part = ' '.join(parts[9:])

                    # The path contains tab-separated old and new paths
                    if '\t' in path_part:
                        new_path, old_path = path_part.split('\t', 1)
                    else:
                        new_path = path_part
                        old_path = None

                    index_status = xy[0] if xy[0] != '.' else ''
                    worktree_status = xy[1] if xy[1] != '.' else ''

                    file_id = _generate_file_id(new_path, index_status, worktree_status)

                    files.append(GitFileStatus(
                        file_id=file_id,
                        path=new_path,
                        old_path=old_path,
                        index_status=index_status,
                        worktree_status=worktree_status
                    ))

                    file_count += 1
                    if file_count >= max_files:
                        break

            # Parse untracked entries
            # Format: ? <path>
            elif entry.startswith('? '):
                path = entry[2:]
                file_id = _generate_file_id(path, '', '?')

                files.append(GitFileStatus(
                    file_id=file_id,
                    path=path,
                    worktree_status='?'
                ))

                file_count += 1
                if file_count >= max_files:
                    break

            # Parse unmerged entries
            # Format: u XY sub <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
            elif entry.startswith('u '):
                parts = entry.split(' ')
                if len(parts) >= 11:
                    xy = parts[1]
                    path = ' '.join(parts[10:])

                    file_id = _generate_file_id(path, 'U', 'U')

                    files.append(GitFileStatus(
                        file_id=file_id,
                        path=path,
                        index_status='U',
                        worktree_status='U'
                    ))

                    file_count += 1
                    if file_count >= max_files:
                        break

    except subprocess.SubprocessError:
        pass

    # Check for ongoing operations
    git_dir_result = _run_git_command(
        git_exe,
        ['rev-parse', '--git-dir'],
        cwd=repo_path,
        timeout=5.0,
        check=False
    )

    if git_dir_result.returncode == 0:
        git_dir = Path(git_dir_result.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = repo_path / git_dir

        # Check for ongoing operations
        if (git_dir / 'MERGE_HEAD').exists():
            ongoing_operation = 'merge'
        elif (git_dir / 'REVERT_HEAD').exists():
            ongoing_operation = 'revert'
        elif (git_dir / 'CHERRY_PICK_HEAD').exists():
            ongoing_operation = 'cherry-pick'
        elif (git_dir / 'rebase-merge').exists() or (git_dir / 'rebase-apply').exists():
            ongoing_operation = 'rebase'

    return head, files, ongoing_operation


def _generate_file_id(path: str, index_status: str, worktree_status: str) -> str:
    """Generate a file ID for status tracking.

    Args:
        path: File path
        index_status: Index status character
        worktree_status: Worktree status character

    Returns:
        File ID (hex string)
    """
    content = f"{path}\x00{index_status}\x00{worktree_status}"
    hash_bytes = hashlib.sha256(content.encode('utf-8')).digest()
    return hash_bytes[:16].hex()


def list_branches(
    repo_path: Path,
    git_exe: str,
    current_branch: Optional[str] = None
) -> List[GitBranch]:
    """List all branches.

    Args:
        repo_path: Repository path
        git_exe: Path to git executable
        current_branch: Current branch name (from HEAD)

    Returns:
        List of GitBranch objects
    """
    branches = []

    try:
        # Get local branches - use newline separator instead of null
        result = _run_git_command(
            git_exe,
            ['for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads/'],
            cwd=repo_path,
            timeout=10.0
        )

        for line in result.stdout.strip().split('\n'):
            if not line:
                continue

            parts = line.split(' ')
            if len(parts) != 2:
                continue

            refname, oid = parts
            if not refname.startswith('refs/heads/'):
                continue

            name = refname[len('refs/heads/'):]
            is_current = (name == current_branch)

            branches.append(GitBranch(
                name=name,
                oid=oid,
                current=is_current
            ))

    except subprocess.SubprocessError:
        pass

    # Sort: current first, then by name
    branches.sort(key=lambda b: (not b.current, b.name.lower(), b.name))

    return branches


def list_remotes(repo_path: Path, git_exe: str) -> List[GitRemote]:
    """List all remotes.

    Args:
        repo_path: Repository path
        git_exe: Path to git executable

    Returns:
        List of GitRemote objects
    """
    remotes = []

    try:
        # Get remote names
        result = _run_git_command(
            git_exe,
            ['remote'],
            cwd=repo_path,
            timeout=5.0
        )

        for name in result.stdout.strip().split('\n'):
            if not name:
                continue

            # Get remote URL
            url_result = _run_git_command(
                git_exe,
                ['remote', 'get-url', name],
                cwd=repo_path,
                timeout=5.0,
                check=False
            )

            if url_result.returncode != 0:
                continue

            url = url_result.stdout.strip()
            display_url = _sanitize_url(url)

            # Generate remote ID
            remote_id = _generate_remote_id(repo_path, name)

            remotes.append(GitRemote(
                id=remote_id,
                name=name,
                display_url=display_url
            ))

    except subprocess.SubprocessError:
        pass

    return remotes


def _generate_remote_id(repo_path: Path, remote_name: str) -> str:
    """Generate remote ID.

    Args:
        repo_path: Repository path
        remote_name: Remote name

    Returns:
        Remote ID (hex string)
    """
    # Note: In production, this should use repo_id from database
    # For now, use a placeholder
    content = f"{repo_path}\x00{remote_name}"
    hash_bytes = hashlib.sha256(content.encode('utf-8')).digest()
    return hash_bytes[:16].hex()


def _sanitize_url(url: str) -> str:
    """Sanitize URL for display (remove credentials).

    Args:
        url: Original URL

    Returns:
        Sanitized URL
    """
    # Remove embedded credentials from URLs
    # https://user:pass@host/path -> https://host/path
    url = re.sub(r'://[^@/]+@', '://', url)

    # Remove query parameters with tokens
    url = re.sub(r'[?&](?:token|access_token|key)=[^&]*', '', url)

    return url


def compute_state_token(
    head: GitHead,
    files: List[GitFileStatus],
    strength: str = "lightweight"
) -> str:
    """Compute state token for change detection.

    Args:
        head: HEAD information
        files: File status list
        strength: "lightweight" or "full"

    Returns:
        State token (hex string)
    """
    state_data = {
        "head_oid": head.oid,
        "head_branch": head.branch,
        "unborn": head.unborn,
        "detached": head.detached,
        "file_count": len(files)
    }

    if strength == "full":
        # Include file details for full strength
        state_data["files"] = [
            {
                "path": f.path,
                "index_status": f.index_status,
                "worktree_status": f.worktree_status
            }
            for f in files[:1000]  # Limit to avoid huge tokens
        ]

    # Canonical JSON
    canonical = json.dumps(state_data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    hash_bytes = hashlib.sha256(canonical.encode('utf-8')).digest()
    return hash_bytes.hex()


def get_full_status(
    repo_path: Path,
    git_exe: str,
    repo_id: str,
    worktree_id: str
) -> GitStatus:
    """Get complete repository status.

    Args:
        repo_path: Repository path
        git_exe: Path to git executable
        repo_id: Repository ID
        worktree_id: Worktree ID

    Returns:
        GitStatus object
    """
    head, files, ongoing_operation = get_status(repo_path, git_exe)
    branches = list_branches(repo_path, git_exe, head.branch)
    remotes = list_remotes(repo_path, git_exe)

    capabilities = {
        "read": True,
        "write": False,
        "reasons": []
    }

    state_token = compute_state_token(head, files, strength="lightweight")

    return GitStatus(
        repo_id=repo_id,
        worktree_id=worktree_id,
        head=head,
        files=files,
        branches=branches,
        remotes=remotes,
        ongoing_operation=ongoing_operation,
        capabilities=capabilities,
        state_token=state_token,
        state_token_strength="lightweight"
    )


# ============================================================================
# Repository Lock Management
# ============================================================================

class RepositoryLock:
    """Cross-process repository lock using file-based locking."""

    def __init__(self, lock_dir: Path, repo_id: str, timeout: float = 5.0):
        """Initialize repository lock.

        Args:
            lock_dir: Directory for lock files
            repo_id: Repository ID
            timeout: Lock acquisition timeout in seconds
        """
        self.lock_dir = Path(lock_dir)
        self.repo_id = repo_id
        self.timeout = timeout
        self.lock_file = self.lock_dir / f"{repo_id}.lock"
        self._lock_fd: Optional[int] = None

    def __enter__(self):
        """Acquire lock."""
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Release lock."""
        self.release()
        return False

    def acquire(self):
        """Acquire the lock.

        Raises:
            GitLockError: If lock cannot be acquired within timeout
        """
        # Ensure lock directory exists
        self.lock_dir.mkdir(parents=True, exist_ok=True)

        start_time = time.time()

        while True:
            try:
                # Try to create lock file exclusively
                fd = os.open(
                    self.lock_file,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644
                )

                # Write lock info
                lock_info = {
                    "repo_id": self.repo_id,
                    "pid": os.getpid(),
                    "acquired_at": time.time()
                }
                os.write(fd, json.dumps(lock_info).encode('utf-8'))
                os.close(fd)

                # Open for reading to keep reference
                self._lock_fd = os.open(self.lock_file, os.O_RDONLY)
                return

            except FileExistsError:
                # Lock already held
                elapsed = time.time() - start_time
                if elapsed >= self.timeout:
                    raise GitLockError(
                        f"Could not acquire lock for repository {self.repo_id} "
                        f"within {self.timeout} seconds"
                    )

                # Wait a bit before retrying
                time.sleep(0.1)

            except OSError as e:
                raise GitLockError(f"Failed to acquire lock: {e}")

    def release(self):
        """Release the lock."""
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
                self._lock_fd = None
            except OSError:
                pass

        try:
            self.lock_file.unlink(missing_ok=True)
        except OSError:
            pass


# ============================================================================
# Plan and Operation Management
# ============================================================================

@dataclass
class GitPlan:
    """Git operation plan."""
    plan_id: str
    operation_id: str
    action: str
    repo_id: str
    worktree_id: Optional[str]
    state: str = "previewed"
    state_token: str = ""
    state_token_strength: str = "full"
    params: Dict[str, Any] = field(default_factory=dict)
    affected_files: List[str] = field(default_factory=list)
    before_refs: Dict[str, str] = field(default_factory=dict)
    expected_after: Dict[str, Any] = field(default_factory=dict)
    risks: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_prompt: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: str = ""


def generate_operation_id() -> str:
    """Generate a unique operation ID.

    Returns:
        Operation ID (UUID hex)
    """
    import uuid
    return uuid.uuid4().hex


def create_plan(
    action: str,
    repo_id: str,
    worktree_id: Optional[str],
    params: Dict[str, Any],
    state_token: str,
    current_time: Optional[str] = None
) -> GitPlan:
    """Create an operation plan.

    Args:
        action: Action name
        repo_id: Repository ID
        worktree_id: Worktree ID (optional)
        params: Action parameters
        state_token: Current state token
        current_time: Current timestamp (for testing)

    Returns:
        GitPlan object
    """
    from datetime import datetime, timezone, timedelta

    operation_id = generate_operation_id()

    if current_time is None:
        now = datetime.now(timezone.utc)
        created_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        expires_at = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        created_at = current_time
        # Parse and add 5 minutes
        dt = datetime.fromisoformat(current_time.replace('Z', '+00:00'))
        expires_at = (dt + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    return GitPlan(
        plan_id=operation_id,  # v1: plan_id = operation_id
        operation_id=operation_id,
        action=action,
        repo_id=repo_id,
        worktree_id=worktree_id,
        state="previewed",
        state_token=state_token,
        state_token_strength="full",
        params=params,
        created_at=created_at,
        expires_at=expires_at
    )


def validate_plan_state(
    plan: GitPlan,
    current_token: str,
    current_time: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """Validate that plan state is still valid.

    Args:
        plan: Plan to validate
        current_token: Current state token
        current_time: Current timestamp (for testing)

    Returns:
        Tuple of (valid, error_message)
    """
    from datetime import datetime, timezone

    # Check expiration
    if current_time is None:
        now = datetime.now(timezone.utc)
    else:
        now = datetime.fromisoformat(current_time.replace('Z', '+00:00'))

    expires = datetime.fromisoformat(plan.expires_at.replace('Z', '+00:00'))
    if now > expires:
        return False, "Plan has expired. Please regenerate."

    # Check state token
    if plan.state_token != current_token:
        return False, "Repository state has changed. Please regenerate plan."

    return True, None


# ============================================================================
# Read-only Query Operations
# ============================================================================

def get_commit_info(
    repo_path: Path,
    git_exe: str,
    commit_oid: str
) -> Dict[str, Any]:
    """Get commit information.

    Args:
        repo_path: Repository path
        git_exe: Path to git executable
        commit_oid: Commit OID

    Returns:
        Commit information dict

    Raises:
        GitRepositoryError: If commit cannot be found
    """
    try:
        # Verify OID
        result = _run_git_command(
            git_exe,
            ['rev-parse', '--verify', f'{commit_oid}^{{commit}}'],
            cwd=repo_path,
            timeout=5.0
        )
        verified_oid = result.stdout.strip()

        # Get commit details
        result = _run_git_command(
            git_exe,
            [
                'show',
                '--no-patch',
                '--format=%H%n%P%n%T%n%an%n%ae%n%at%n%cn%n%ce%n%ct%n%s%n%b',
                verified_oid
            ],
            cwd=repo_path,
            timeout=10.0
        )

        lines = result.stdout.split('\n')
        if len(lines) < 10:
            raise GitRepositoryError(f"Invalid commit format for {commit_oid}")

        full_oid = lines[0]
        parents = lines[1].split() if lines[1] else []
        tree_oid = lines[2]
        author_name = lines[3]
        author_email = lines[4]
        author_time = int(lines[5])
        committer_name = lines[6]
        committer_email = lines[7]
        commit_time = int(lines[8])
        subject = lines[9]
        body = '\n'.join(lines[10:])

        return {
            "oid": full_oid,
            "parents": parents,
            "tree_oid": tree_oid,
            "author_name": author_name,
            "author_email": author_email,
            "authored_at": datetime.fromtimestamp(author_time, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "committer_name": committer_name,
            "committer_email": committer_email,
            "committed_at": datetime.fromtimestamp(commit_time, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "subject": subject,
            "body": body
        }

    except subprocess.SubprocessError as e:
        raise GitRepositoryError(f"Failed to get commit info: {e}")


def get_diff(
    repo_path: Path,
    git_exe: str,
    base: str,
    target: str,
    file_path: Optional[str] = None
) -> Dict[str, Any]:
    """Get diff between two refs.

    Args:
        repo_path: Repository path
        git_exe: Path to git executable
        base: Base ref
        target: Target ref
        file_path: Optional specific file path

    Returns:
        Diff information dict

    Raises:
        GitRepositoryError: If diff cannot be generated
    """
    try:
        # Build command
        cmd = [
            'diff',
            '--no-ext-diff',
            '--no-textconv',
            '--name-status',
            '-z',
            base,
            target
        ]

        if file_path:
            cmd.extend(['--', file_path])

        result = _run_git_command(
            git_exe,
            cmd,
            cwd=repo_path,
            timeout=10.0
        )

        # Parse null-terminated output
        files = []
        entries = result.stdout.split('\0')

        i = 0
        while i < len(entries) - 1:
            status = entries[i]
            if not status:
                i += 1
                continue

            # Handle rename/copy (two paths)
            if status.startswith('R') or status.startswith('C'):
                if i + 2 < len(entries):
                    old_path = entries[i + 1]
                    new_path = entries[i + 2]
                    files.append({
                        "status": status[0],
                        "old_path": old_path,
                        "new_path": new_path
                    })
                    i += 3
                else:
                    break
            else:
                # Single path
                path = entries[i + 1]
                files.append({
                    "status": status,
                    "path": path
                })
                i += 2

        return {
            "base": base,
            "target": target,
            "files": files
        }

    except subprocess.SubprocessError as e:
        raise GitRepositoryError(f"Failed to get diff: {e}")


def check_ref_format(ref_name: str, git_exe: str) -> bool:
    """Check if a ref name is valid.

    Args:
        ref_name: Reference name to check
        git_exe: Path to git executable

    Returns:
        True if valid
    """
    try:
        result = _run_git_command(
            git_exe,
            ['check-ref-format', '--branch', ref_name],
            cwd=None,
            timeout=5.0,
            check=False
        )
        return result.returncode == 0
    except subprocess.SubprocessError:
        return False

