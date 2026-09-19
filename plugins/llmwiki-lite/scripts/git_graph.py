"""Git commit graph visualization with topological sorting and lane assignment.

Implements:
- Topological sorting of commits
- Lane assignment algorithm for graph visualization
- Snapshot caching for performance
- Pagination support (100 nodes per page)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from git_service import (
    GitError,
    GitRepositoryError,
    _run_git_command,
    get_head_info,
    list_branches,
)


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


@dataclass
class GraphSnapshot:
    """Cached commit graph snapshot."""
    snapshot_id: str
    repo_path: str
    ref: str
    commit_count: int
    head_oid: str
    branches: List[str] = field(default_factory=list)
    created_at: str = ""
    nodes: List[Dict[str, Any]] = field(default_factory=list)


def generate_snapshot_id(repo_path: Path, ref: str, head_oid: str) -> str:
    """Generate snapshot ID for caching.

    Args:
        repo_path: Repository path
        ref: Reference (branch or commit)
        head_oid: Current HEAD OID

    Returns:
        Snapshot ID (hex string)
    """
    content = f"{repo_path}\x00{ref}\x00{head_oid}"
    hash_bytes = hashlib.sha256(content.encode('utf-8')).digest()
    return hash_bytes.hex()[:32]


def load_commit_graph(
    repo_path: Path,
    git_exe: str,
    ref: str = "HEAD",
    max_count: int = 500,
    skip: int = 0
) -> List[CommitNode]:
    """Load commit graph from repository.

    Args:
        repo_path: Repository path
        git_exe: Path to git executable
        ref: Reference to start from (branch or commit)
        max_count: Maximum number of commits to load
        skip: Number of commits to skip

    Returns:
        List of CommitNode objects in topological order

    Raises:
        GitRepositoryError: If commits cannot be loaded
    """
    try:
        # Get commit history with parent information
        # Format: <oid>%x00<parents>%x00<subject>%x00<author_name>%x00<author_email>%x00<commit_time>
        result = _run_git_command(
            git_exe,
            [
                'log',
                '--all',
                '--topo-order',
                f'--max-count={max_count}',
                f'--skip={skip}',
                '--format=%H%x00%P%x00%s%x00%an%x00%ae%x00%ct',
                '--date-order'
            ],
            cwd=repo_path,
            timeout=30.0
        )

        # Parse commits
        commits: Dict[str, CommitNode] = {}
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue

            parts = line.split('\x00')
            if len(parts) < 6:
                continue

            oid = parts[0]
            parents = parts[1].split() if parts[1] else []
            subject = parts[2]
            author_name = parts[3]
            author_email = parts[4]
            commit_time = int(parts[5])

            committed_at = datetime.fromtimestamp(commit_time, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            node = CommitNode(
                oid=oid,
                parents=parents,
                subject=subject,
                author_name=author_name,
                author_email=author_email,
                committed_at=committed_at
            )
            commits[oid] = node

        # Build parent-child relationships
        for oid, node in commits.items():
            for parent_oid in node.parents:
                if parent_oid in commits:
                    commits[parent_oid].children.append(oid)

        # Get branch and tag references
        refs_result = _run_git_command(
            git_exe,
            ['for-each-ref', '--format=%(objectname) %(refname)', 'refs/heads/', 'refs/tags/'],
            cwd=repo_path,
            timeout=10.0
        )

        for line in refs_result.stdout.strip().split('\n'):
            if not line:
                continue

            parts = line.split(' ', 1)
            if len(parts) != 2:
                continue

            oid, refname = parts
            if oid not in commits:
                continue

            if refname.startswith('refs/heads/'):
                branch_name = refname[len('refs/heads/'):]
                commits[oid].branches.append(branch_name)
            elif refname.startswith('refs/tags/'):
                tag_name = refname[len('refs/tags/'):]
                commits[oid].tags.append(tag_name)

        # Return in topological order (already sorted by git log --topo-order)
        return list(commits.values())

    except Exception as e:
        raise GitRepositoryError(f"Failed to load commit graph: {e}")


def assign_lanes(nodes: List[CommitNode]) -> List[CommitNode]:
    """Assign lane positions for graph visualization.

    Uses a greedy algorithm to minimize lane crossings:
    1. Process commits in topological order
    2. Assign each commit to the leftmost available lane
    3. Reserve lanes for active branches

    Args:
        nodes: List of commit nodes in topological order

    Returns:
        Nodes with lane assignments
    """
    if not nodes:
        return nodes

    # Build OID to node mapping
    oid_to_node: Dict[str, CommitNode] = {node.oid: node for node in nodes}

    # Track active lanes and their current commit
    active_lanes: List[Optional[str]] = []  # lane index -> OID or None
    oid_to_lane: Dict[str, int] = {}  # OID -> assigned lane

    for row, node in enumerate(nodes):
        node.row = row

        # Try to reuse parent's lane if it's available
        assigned_lane = None
        if node.parents:
            # Check first parent (main line of development)
            parent_oid = node.parents[0]
            if parent_oid in oid_to_lane:
                parent_lane = oid_to_lane[parent_oid]
                # Check if this lane is free (parent has no more children after this)
                parent_node = oid_to_node.get(parent_oid)
                if parent_node and len(parent_node.children) == 1:
                    # Parent only has this child, reuse lane
                    assigned_lane = parent_lane

        # Otherwise find the leftmost available lane
        if assigned_lane is None:
            # Find first free lane
            for i, occupied in enumerate(active_lanes):
                if occupied is None:
                    assigned_lane = i
                    break

            # No free lane found, create new one
            if assigned_lane is None:
                assigned_lane = len(active_lanes)
                active_lanes.append(None)

        # Ensure we have enough lanes
        while len(active_lanes) <= assigned_lane:
            active_lanes.append(None)

        # Assign lane
        node.lane = assigned_lane
        oid_to_lane[node.oid] = assigned_lane
        active_lanes[assigned_lane] = node.oid

        # Mark parent lanes as free if this is their last child
        for parent_oid in node.parents:
            if parent_oid in oid_to_node:
                parent_node = oid_to_node[parent_oid]
                # Check if all children have been processed
                all_children_processed = all(
                    child_oid in oid_to_lane for child_oid in parent_node.children
                )
                if all_children_processed and parent_oid in oid_to_lane:
                    parent_lane = oid_to_lane[parent_oid]
                    if parent_lane < len(active_lanes) and active_lanes[parent_lane] == parent_oid:
                        active_lanes[parent_lane] = None

    return nodes


def build_graph(
    repo_path: Path,
    git_exe: str,
    ref: str = "HEAD",
    page: int = 0,
    page_size: int = 100
) -> Dict[str, Any]:
    """Build commit graph with pagination.

    Args:
        repo_path: Repository path
        git_exe: Path to git executable
        ref: Reference to start from
        page: Page number (0-indexed)
        page_size: Number of commits per page

    Returns:
        Graph data dictionary
    """
    # Load commits
    skip = page * page_size
    nodes = load_commit_graph(
        repo_path,
        git_exe,
        ref=ref,
        max_count=page_size,
        skip=skip
    )

    # Assign lanes
    nodes = assign_lanes(nodes)

    # Get HEAD info
    head = get_head_info(repo_path, git_exe)

    # Get branches
    branches = list_branches(repo_path, git_exe, head.branch)

    # Generate snapshot ID
    snapshot_id = generate_snapshot_id(repo_path, ref, head.oid or "")

    # Convert to JSON-serializable format
    nodes_data = [
        {
            "oid": node.oid,
            "short_oid": node.oid[:7],
            "parents": node.parents,
            "children": node.children,
            "subject": node.subject,
            "author_name": node.author_name,
            "author_email": node.author_email,
            "committed_at": node.committed_at,
            "branches": node.branches,
            "tags": node.tags,
            "lane": node.lane,
            "row": node.row
        }
        for node in nodes
    ]

    return {
        "ok": True,
        "snapshot_id": snapshot_id,
        "ref": ref,
        "head_oid": head.oid,
        "head_branch": head.branch,
        "page": page,
        "page_size": page_size,
        "commit_count": len(nodes),
        "has_more": len(nodes) == page_size,
        "nodes": nodes_data,
        "branches": [
            {
                "name": b.name,
                "oid": b.oid,
                "current": b.current
            }
            for b in branches
        ]
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
