"""Read-only Git graph: visible refs, real parents and stable paginated lanes."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from git_service import GitHead, GitRepositoryError, _run_git_command, get_head_info


@dataclass
class CommitNode:
    """A commit node in the graph."""
    oid: str
    parents: List[str] = field(default_factory=list)
    children: List[str] = field(default_factory=list)
    subject: str = ""
    author_name: str = ""
    author_email: str = ""
    committed_at: str = ""
    branches: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    lane: int = 0
    row: int = 0
    short_oid: str = ""
    committed_at_iso: str = ""
    parent_lanes: List[int] = field(default_factory=list)


@dataclass
class GraphSnapshot:
    """Cached commit graph snapshot (legacy opt-in persistence API)."""
    snapshot_id: str
    repo_path: str
    ref: str
    commit_count: int
    head_oid: str
    branches: List[str] = field(default_factory=list)
    created_at: str = ""
    nodes: List[Dict[str, Any]] = field(default_factory=list)


class GraphSnapshotExpiredError(GitRepositoryError):
    """The caller must discard the old graph and request its first page again."""

    code = "snapshot_expired"

    def __init__(self, current_snapshot_id: str):
        super().__init__("Git graph snapshot changed; refresh version history")
        self.current_snapshot_id = current_snapshot_id


def generate_snapshot_id(
    repo_path: Path,
    ref: str,
    head_oid: str,
    refs: Optional[List[Dict[str, str]]] = None,
    head_branch: Optional[str] = None
) -> str:
    """Hash the repository, HEAD identity and all visible reference values.

    The original three-argument form remains supported. ``build_graph`` always
    includes refs and the symbolic HEAD, including refs outside the loaded page.
    """
    content = json.dumps(
        [str(repo_path.resolve()), ref, head_oid, head_branch,
         sorted(refs or [], key=lambda item: item['refname'])],
        sort_keys=True, ensure_ascii=True, separators=(',', ':')
    )
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:32]


def _read_graph_state(repo_path: Path, git_exe: str):
    head = get_head_info(repo_path, git_exe)
    # Explicit namespaces exclude recovery, stash, replace and other private refs.
    try:
        result = _run_git_command(
            git_exe,
            ['for-each-ref', '--sort=refname',
             '--format=%(refname)%00%(objectname)%00%(*objectname)%00%(symref)',
             'refs/heads/', 'refs/remotes/', 'refs/tags/'],
            cwd=repo_path
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise GitRepositoryError(f'Failed to read graph references: {exc}') from exc
    refs = []
    for line in result.stdout.splitlines():
        refname, oid, peeled_oid, symref = line.split('\0')
        refs.append(dict(refname=refname, oid=oid, peeled_oid=peeled_oid, symref=symref))
    return head, refs


def _load_commits(
    repo_path: Path, git_exe: str, head: GitHead,
    refs: List[Dict[str, str]], max_count: int, skip: int
) -> List[CommitNode]:
    # Freeze revision roots to the captured OIDs, not refnames that may move.
    roots = list(dict.fromkeys(
        ([head.oid] if head.oid else []) +
        [r['oid'] for r in refs if r['refname'].startswith(('refs/heads/', 'refs/remotes/'))]
    ))
    if not roots or not max_count:
        return []
    try:
        result = _run_git_command(
            git_exe,
            ['--no-replace-objects', 'log', '--stdin', '--topo-order',
             f'--max-count={max_count}', f'--skip={skip}',
             '--no-patch', '--no-color', '--no-decorate', '--no-notes',
             '--no-show-signature', '--encoding=UTF-8', '--abbrev=7', '-z',
             '--format=%H%x00%P%x00%s%x00%an%x00%ae%x00%ct%x00%h%x00%cI'],
            cwd=repo_path, input_data='\n'.join(roots) + '\n'
        )
        # Eight fixed NUL-separated fields per commit; no newline/path splitting.
        fields = result.stdout.split('\0')
        if fields[-1] == '':
            fields.pop()
        if len(fields) % 8:
            raise ValueError('Malformed Git commit metadata')
        commits: Dict[str, CommitNode] = {}
        for offset in range(0, len(fields), 8):
            oid, parents, subject, author, email, stamp, short, iso = fields[offset:offset + 8]
            commits[oid] = CommitNode(
                oid=oid, parents=parents.split(), subject=subject,
                author_name=author, author_email=email,
                committed_at=datetime.fromtimestamp(int(stamp), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                short_oid=short, committed_at_iso=iso
            )
        _restore_boundary_parents(repo_path, git_exe, commits)
        for node in commits.values():
            for parent in node.parents:
                if parent in commits:
                    commits[parent].children.append(node.oid)
        for item in refs:
            node = commits.get(item['peeled_oid'] or item['oid'])
            if node is None:
                continue
            refname = item['refname']
            if refname.startswith('refs/heads/'):
                node.branches.append(refname[len('refs/heads/'):])
            elif refname.startswith('refs/remotes/'):
                node.branches.append(refname[len('refs/remotes/'):])
            elif refname.startswith('refs/tags/'):
                node.tags.append(refname[len('refs/tags/'):])
        return list(commits.values())
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        raise GitRepositoryError(f'Failed to load commit graph: {exc}') from exc


def _restore_boundary_parents(
    repo_path: Path, git_exe: str, commits: Dict[str, CommitNode]
) -> None:
    """Git log hides parents at shallow boundaries; recover the object headers.

    Only apparent roots need a raw read, usually none in a paginated prefix.
    Batch framing uses byte sizes so UTF-8 messages/signatures cannot shift it.
    """
    roots = [node.oid for node in commits.values() if not node.parents]
    if not roots:
        return
    data = _run_git_command(
        git_exe, ['--no-replace-objects', 'cat-file', '--batch'], cwd=repo_path,
        input_data=('\n'.join(roots) + '\n').encode('ascii'), text=False
    ).stdout
    offset = 0
    for oid in roots:
        end = data.index(b'\n', offset)
        actual_oid, kind, size = data[offset:end].split()
        if actual_oid.decode('ascii') != oid or kind != b'commit':
            raise ValueError('Malformed Git commit object')
        offset = end + 1
        content = data[offset:offset + int(size)]
        offset += int(size) + 1
        headers = content.split(b'\n\n', 1)[0].split(b'\n')
        commits[oid].parents = [line[7:].decode('ascii') for line in headers if line.startswith(b'parent ')]


def load_commit_graph(
    repo_path: Path,
    git_exe: str,
    ref: str = "HEAD",
    max_count: int = 500,
    skip: int = 0
) -> List[CommitNode]:
    """Read HEAD and local/remote branch history, excluding private-only commits.

    ``ref`` is retained for compatibility; as before this is a combined graph,
    not a single-branch filter. Tags decorate reachable commits but add no roots.
    """
    if max_count < 0 or skip < 0:
        raise ValueError('max_count and skip must be non-negative')
    head, refs = _read_graph_state(repo_path, git_exe)
    return _load_commits(repo_path, git_exe, head, refs, max_count, skip)


def assign_lanes(nodes: List[CommitNode]) -> List[CommitNode]:
    """Reserve pending-parent lanes while walking from tips toward ancestors.

    A pending parent's lane is retained until that parent is processed, including
    parents outside the requested prefix. Replaying a longer prefix therefore
    preserves every earlier row, lane and parent-edge lane without a disk cache.
    """
    active: List[Optional[str]] = []
    pending: Dict[str, int] = {}

    def free_lane() -> int:
        for index, oid in enumerate(active):
            if oid is None:
                return index
        active.append(None)
        return len(active) - 1

    for row, node in enumerate(nodes):
        lane = pending.pop(node.oid) if node.oid in pending else free_lane()
        node.row, node.lane = row, lane
        active[lane] = None
        node.parent_lanes = []
        for index, parent in enumerate(node.parents):
            if parent not in pending:
                parent_lane = lane if index == 0 else free_lane()
                active[parent_lane] = parent
                pending[parent] = parent_lane
            node.parent_lanes.append(pending[parent])
    return nodes


def build_graph(
    repo_path: Path,
    git_exe: str,
    ref: str = "HEAD",
    page: int = 0,
    page_size: int = 100,
    snapshot_id: Optional[str] = None
) -> Dict[str, Any]:
    """Return one graph page, validating refs both before and after the read.

    An optional old ``snapshot_id`` raises GraphSnapshotExpiredError on change.
    Rows and lanes are global, not page-relative. ``parent_lanes`` parallels
    ``parents``; ``boundary_parents`` identifies real parents beyond the loaded
    prefix (never synthetic root nodes). ``refs`` includes all visible labels.
    The replay is bounded to the requested prefix plus one lookahead commit.
    """
    if page < 0 or page_size < 1:
        raise ValueError('page must be non-negative and page_size must be positive')
    head, refs = _read_graph_state(repo_path, git_exe)
    current_id = generate_snapshot_id(repo_path, ref, head.oid or '', refs, head.branch)
    if snapshot_id is not None and snapshot_id != current_id:
        raise GraphSnapshotExpiredError(current_id)

    skip = page * page_size
    stop = skip + page_size
    prefix = _load_commits(repo_path, git_exe, head, refs, stop + 1, 0)
    has_more = len(prefix) > stop
    prefix = assign_lanes(prefix[:stop])
    loaded = {node.oid for node in prefix}
    nodes_data = []
    for node in prefix[skip:]:
        nodes_data.append({
            'oid': node.oid, 'short_oid': node.short_oid,
            'parents': node.parents, 'children': node.children,
            'subject': node.subject, 'author_name': node.author_name,
            'author_email': node.author_email, 'committed_at': node.committed_at,
            'committed_at_iso': node.committed_at_iso,
            'branches': node.branches, 'tags': node.tags,
            'lane': node.lane, 'row': node.row,
            'parent_lanes': node.parent_lanes,
            'boundary_parents': [
                {'oid': oid, 'lane': lane}
                for oid, lane in zip(node.parents, node.parent_lanes) if oid not in loaded
            ]
        })
    # Include the entire frontier: a prior page can still have a pending parent.
    boundary = {}
    for node in prefix:
        for oid, lane in zip(node.parents, node.parent_lanes):
            if oid not in loaded:
                boundary[oid] = lane

    final_head, final_refs = _read_graph_state(repo_path, git_exe)
    final_id = generate_snapshot_id(repo_path, ref, final_head.oid or '', final_refs, final_head.branch)
    if final_id != current_id:
        raise GraphSnapshotExpiredError(final_id)

    branches = []
    for item in refs:
        refname = item['refname']
        remote = refname.startswith('refs/remotes/')
        if not remote and not refname.startswith('refs/heads/'):
            continue
        name = refname[len('refs/remotes/' if remote else 'refs/heads/'):]
        branches.append({
            'name': name, 'oid': item['oid'], 'refname': refname,
            'current': not remote and name == head.branch, 'remote': remote
        })
    branches.sort(key=lambda b: (not b['current'], b['name'].lower(), b['name']))
    return {
        'ok': True, 'snapshot_id': current_id, 'ref': ref,
        'head_oid': head.oid, 'head_branch': head.branch,
        'page': page, 'page_size': page_size, 'commit_count': len(nodes_data),
        'has_more': has_more, 'nodes': nodes_data, 'branches': branches, 'refs': refs,
        'boundary_parents': [{'oid': oid, 'lane': lane} for oid, lane in boundary.items()]
    }


def save_snapshot(snapshot: GraphSnapshot, cache_dir: Path) -> None:
    """Save graph snapshot to cache.

    Args:
        snapshot: Snapshot to save
        cache_dir: Cache directory
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{snapshot.snapshot_id}.json"

    data = {
        "snapshot_id": snapshot.snapshot_id,
        "repo_path": snapshot.repo_path,
        "ref": snapshot.ref,
        "commit_count": snapshot.commit_count,
        "head_oid": snapshot.head_oid,
        "branches": snapshot.branches,
        "created_at": snapshot.created_at,
        "nodes": snapshot.nodes
    }

    cache_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


def load_snapshot(snapshot_id: str, cache_dir: Path) -> Optional[GraphSnapshot]:
    """Load graph snapshot from cache.

    Args:
        snapshot_id: Snapshot ID
        cache_dir: Cache directory

    Returns:
        GraphSnapshot or None if not found
    """
    cache_file = cache_dir / f"{snapshot_id}.json"
    if not cache_file.exists():
        return None

    try:
        data = json.loads(cache_file.read_text(encoding='utf-8'))
        return GraphSnapshot(
            snapshot_id=data["snapshot_id"],
            repo_path=data["repo_path"],
            ref=data["ref"],
            commit_count=data["commit_count"],
            head_oid=data["head_oid"],
            branches=data.get("branches", []),
            created_at=data.get("created_at", ""),
            nodes=data.get("nodes", [])
        )
    except (json.JSONDecodeError, KeyError, OSError):
        return None
