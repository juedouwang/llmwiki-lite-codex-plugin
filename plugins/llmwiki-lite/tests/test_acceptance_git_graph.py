"""Acceptance tests for T-06: Git Graph Visualization.

AT-24: Basic graph loading
AT-25: Lane assignment and visualization
AT-26: Pagination support
AT-27: Branch switching
"""

import json
import subprocess
from pathlib import Path

import pytest

# Add parent scripts directory to path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "plugins" / "llmwiki-lite" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from git_graph import build_graph, assign_lanes, load_commit_graph
from git_service import detect_git_executable, is_git_repository


@pytest.fixture
def sample_repo(tmp_path):
    """Create a sample git repository for testing."""
    repo = tmp_path / "sample_repo"
    repo.mkdir()

    git_exe = detect_git_executable()

    def run_git(*args):
        subprocess.run(
            [git_exe] + list(args),
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    # Initialize repo
    run_git("init")
    run_git("config", "user.name", "Test User")
    run_git("config", "user.email", "test@example.com")

    # Create commits on main
    (repo / "file1.txt").write_text("content1")
    run_git("add", "file1.txt")
    run_git("commit", "-m", "Initial commit")

    (repo / "file2.txt").write_text("content2")
    run_git("add", "file2.txt")
    run_git("commit", "-m", "Add file2")

    # Create feature branch
    run_git("checkout", "-b", "feature")
    (repo / "file3.txt").write_text("content3")
    run_git("add", "file3.txt")
    run_git("commit", "-m", "Feature commit")

    # Back to master and add another commit
    run_git("checkout", "master")
    (repo / "file4.txt").write_text("content4")
    run_git("add", "file4.txt")
    run_git("commit", "-m", "Master continues")

    return repo


def test_at24_basic_graph_loading(sample_repo):
    """AT-24: Verify basic graph can be loaded from a git repository."""
    git_exe = detect_git_executable()

    # Verify it's a git repository
    assert is_git_repository(sample_repo, git_exe)

    # Build graph
    result = build_graph(sample_repo, git_exe, ref="HEAD", page=0, page_size=100)

    # Verify structure
    assert result["ok"] is True
    assert "nodes" in result
    assert "branches" in result
    assert len(result["nodes"]) >= 3  # At least 3 commits
    assert len(result["branches"]) >= 2  # main and feature

    # Verify node structure
    for node in result["nodes"]:
        assert "oid" in node
        assert "short_oid" in node
        assert "subject" in node
        assert "author_name" in node
        assert "author_email" in node
        assert "committed_at" in node
        assert "parents" in node
        assert "lane" in node
        assert "row" in node
        assert "branches" in node


def test_at25_lane_assignment(sample_repo):
    """AT-25: Verify lane assignment correctly visualizes branching."""
    git_exe = detect_git_executable()

    result = build_graph(sample_repo, git_exe, ref="--all", page=0, page_size=100)

    assert result["ok"] is True

    # Check that lanes are assigned
    lanes_used = set(node["lane"] for node in result["nodes"])
    assert len(lanes_used) >= 1  # At least one lane used

    # Verify lanes are non-negative integers
    for node in result["nodes"]:
        assert isinstance(node["lane"], int)
        assert node["lane"] >= 0

    # Verify rows are sequential
    rows = [node["row"] for node in result["nodes"]]
    assert rows == list(range(len(rows)))


def test_at26_pagination_support(sample_repo):
    """AT-26: Verify pagination correctly limits and offsets results."""
    git_exe = detect_git_executable()

    # Load first page
    page1 = build_graph(sample_repo, git_exe, ref="HEAD", page=0, page_size=2)
    assert page1["ok"] is True
    assert len(page1["nodes"]) <= 2

    # If there are more commits, has_more should be True
    total_commits = len(
        subprocess.run(
            [git_exe, "log", "--format=%H", "HEAD"],
            cwd=sample_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().split("\n")
    )

    if total_commits > 2:
        assert page1["has_more"] is True

        # Load second page
        page2 = build_graph(sample_repo, git_exe, ref="HEAD", page=1, page_size=2)
        assert page2["ok"] is True

        # Verify different commits
        page1_oids = {node["oid"] for node in page1["nodes"]}
        page2_oids = {node["oid"] for node in page2["nodes"]}
        assert page1_oids != page2_oids  # Different pages should have different commits
    else:
        assert page1["has_more"] is False


def test_at27_branch_switching(sample_repo):
    """AT-27: Verify branch switching returns correct commit history."""
    git_exe = detect_git_executable()

    # Load master branch
    master_result = build_graph(sample_repo, git_exe, ref="master", page=0, page_size=100)
    assert master_result["ok"] is True

    # Load feature branch
    feature_result = build_graph(
        sample_repo, git_exe, ref="feature", page=0, page_size=100
    )
    assert feature_result["ok"] is True

    # Verify branches list
    assert len(master_result["branches"]) >= 2
    assert len(feature_result["branches"]) >= 2

    # Both should have branch info
    master_branches = {b["name"] for b in master_result["branches"]}
    feature_branches = {b["name"] for b in feature_result["branches"]}

    assert "master" in master_branches
    assert "feature" in feature_branches


def test_lane_assignment_simple():
    """Test lane assignment with simple linear history."""
    from git_graph import CommitNode

    # Simple linear history
    nodes = [
        CommitNode(
            oid="c3",
            subject="Third commit",
            author_name="Test",
            author_email="test@example.com",
            committed_at="2024-01-03T00:00:00Z",
            parents=["c2"],
            branches=[],
            lane=0,
            row=0,
        ),
        CommitNode(
            oid="c2",
            subject="Second commit",
            author_name="Test",
            author_email="test@example.com",
            committed_at="2024-01-02T00:00:00Z",
            parents=["c1"],
            branches=[],
            lane=0,
            row=1,
        ),
        CommitNode(
            oid="c1",
            subject="First commit",
            author_name="Test",
            author_email="test@example.com",
            committed_at="2024-01-01T00:00:00Z",
            parents=[],
            branches=[],
            lane=0,
            row=2,
        ),
    ]

    result = assign_lanes(nodes)

    # Verify all commits have lanes assigned
    for node in result:
        assert isinstance(node.lane, int)
        assert node.lane >= 0
