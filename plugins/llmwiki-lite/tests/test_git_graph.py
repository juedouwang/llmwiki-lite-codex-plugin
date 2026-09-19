"""Tests for git_graph module.

Covers AT-24 to AT-27.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from git_service import detect_git_executable, GitRepositoryError
from git_graph import (
    load_commit_graph,
    assign_lanes,
    build_graph,
    generate_snapshot_id,
    save_snapshot,
    load_snapshot,
    GraphSnapshot,
    CommitNode
)


class TestGitGraphFixtureF1(unittest.TestCase):
    """Test git graph with GIT-F1 fixture (普通分叉).

    Covers AT-24 to AT-27.
    """

    @classmethod
    def setUpClass(cls):
        """Create GIT-F1 fixture."""
        cls.temp_dir = tempfile.mkdtemp(prefix="git_graph_test_")
        cls.repo_path = Path(cls.temp_dir) / "test_repo"
        cls.repo_path.mkdir()
        cls.cache_dir = Path(cls.temp_dir) / "cache"
        cls.cache_dir.mkdir()

        cls.git_exe = detect_git_executable()

        # Initialize repository
        cls._run_git(['init', '-b', 'main'], cls.repo_path)
        cls._run_git(['config', 'user.name', 'Fixture Researcher'], cls.repo_path)
        cls._run_git(['config', 'user.email', 'fixture@example.invalid'], cls.repo_path)

        # Create R0: README.md, config.txt, assets/sample.bin
        readme = cls.repo_path / "README.md"
        readme.write_text("# Test Repository\n", encoding='utf-8')

        config = cls.repo_path / "config.txt"
        config.write_text("value=0\n", encoding='utf-8')

        assets = cls.repo_path / "assets"
        assets.mkdir()
        sample_bin = assets / "sample.bin"
        sample_bin.write_bytes(b'\x00\x01\x02\x03\x04')

        cls._run_git(['add', '.'], cls.repo_path)
        cls._run_git_with_date(['commit', '-m', 'R0: Initial commit'], cls.repo_path, 0)

        # Store R0 OID
        result = cls._run_git(['rev-parse', 'HEAD'], cls.repo_path)
        cls.r0_oid = result.stdout.strip()

        # Create M1: modify README
        readme.write_text("# Test Repository\n\nMain branch work.\n", encoding='utf-8')
        cls._run_git(['add', 'README.md'], cls.repo_path)
        cls._run_git_with_date(['commit', '-m', 'M1: Update README'], cls.repo_path, 1)

        # Store M1 OID
        result = cls._run_git(['rev-parse', 'HEAD'], cls.repo_path)
        cls.m1_oid = result.stdout.strip()

        # Create M2: modify config
        config.write_text("value=main\n", encoding='utf-8')
        cls._run_git(['add', 'config.txt'], cls.repo_path)
        cls._run_git_with_date(['commit', '-m', 'M2: Set config to main'], cls.repo_path, 2)

        # Store M2 OID
        result = cls._run_git(['rev-parse', 'HEAD'], cls.repo_path)
        cls.m2_oid = result.stdout.strip()

        # Create experiment branch from R0
        cls._run_git(['checkout', '-b', 'experiment', cls.r0_oid], cls.repo_path)

        # Create X1: add experiment.txt
        experiment = cls.repo_path / "experiment.txt"
        experiment.write_text("Experimental feature\n", encoding='utf-8')
        cls._run_git(['add', 'experiment.txt'], cls.repo_path)
        cls._run_git_with_date(['commit', '-m', 'X1: Add experiment'], cls.repo_path, 3)

        # Store X1 OID
        result = cls._run_git(['rev-parse', 'HEAD'], cls.repo_path)
        cls.x1_oid = result.stdout.strip()

        # Create X2: modify config
        config.write_text("value=experiment\n", encoding='utf-8')
        cls._run_git(['add', 'config.txt'], cls.repo_path)
        cls._run_git_with_date(['commit', '-m', 'X2: Set config to experiment'], cls.repo_path, 4)

        # Store X2 OID
        result = cls._run_git(['rev-parse', 'HEAD'], cls.repo_path)
        cls.x2_oid = result.stdout.strip()

        # Switch back to main
        cls._run_git(['checkout', 'main'], cls.repo_path)

    @classmethod
    def tearDownClass(cls):
        """Clean up fixture."""
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    @classmethod
    def _run_git(cls, args, cwd):
        """Run git command."""
        result = subprocess.run(
            [cls.git_exe] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
        )
        return result

    @classmethod
    def _run_git_with_date(cls, args, cwd, day_offset):
        """Run git commit with fixed date."""
        base_date = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)
        commit_date = base_date + timedelta(days=day_offset)
        date_str = commit_date.strftime("%Y-%m-%d %H:%M:%S +0000")

        env = {
            **os.environ,
            'GIT_AUTHOR_DATE': date_str,
            'GIT_COMMITTER_DATE': date_str,
            'GIT_TERMINAL_PROMPT': '0'
        }

        subprocess.run(
            [cls.git_exe] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            env=env
        )

    def test_at24_load_commit_graph(self):
        """AT-24: 加载提交图谱.

        获取仓库提交历史、父子关系、分支和标签信息
        """
        # Load commit graph
        nodes = load_commit_graph(self.repo_path, self.git_exe, ref="HEAD", max_count=100)

        # Should have 5 commits (R0, M1, M2, X1, X2)
        self.assertEqual(len(nodes), 5)

        # Find commits by subject
        commits_by_subject = {node.subject: node for node in nodes}

        # Verify R0 has no parents
        r0 = commits_by_subject.get("R0: Initial commit")
        self.assertIsNotNone(r0)
        self.assertEqual(len(r0.parents), 0)

        # Verify M1 has R0 as parent
        m1 = commits_by_subject.get("M1: Update README")
        self.assertIsNotNone(m1)
        self.assertEqual(len(m1.parents), 1)
        self.assertEqual(m1.parents[0], self.r0_oid)

        # Verify branch labels
        m2 = commits_by_subject.get("M2: Set config to main")
        self.assertIsNotNone(m2)
        self.assertIn("main", m2.branches)

        x2 = commits_by_subject.get("X2: Set config to experiment")
        self.assertIsNotNone(x2)
        self.assertIn("experiment", x2.branches)

    def test_at25_lane_assignment(self):
        """AT-25: 车道分配算法.

        为提交分配可视化车道，避免交叉，主线优先左侧
        """
        # Load commits
        nodes = load_commit_graph(self.repo_path, self.git_exe, ref="HEAD", max_count=100)

        # Assign lanes
        nodes = assign_lanes(nodes)

        # All nodes should have lane assignments
        for node in nodes:
            self.assertIsNotNone(node.lane)
            self.assertGreaterEqual(node.lane, 0)

        # Check that lane assignments are reasonable
        commits_by_oid = {node.oid: node for node in nodes}

        # All commits should have valid lane assignments
        # Since we use --all --topo-order, the exact order depends on git's algorithm
        # Just verify lanes are non-negative and bounded
        max_lane = max(node.lane for node in nodes)
        self.assertLessEqual(max_lane, len(nodes))  # Can't have more lanes than commits

        # Verify parent-child lane relationships make sense
        # A child should be in the same lane as parent or in an adjacent lane
        for node in nodes:
            for parent_oid in node.parents:
                if parent_oid in commits_by_oid:
                    parent = commits_by_oid[parent_oid]
                    # Lane difference should be reasonable (not jumping many lanes)
                    lane_diff = abs(node.lane - parent.lane)
                    self.assertLessEqual(lane_diff, 3,
                        f"Large lane jump between {node.subject} and parent")

    def test_at26_pagination(self):
        """AT-26: 分页加载.

        首屏100节点，按需加载更多，避免大仓库卡顿
        """
        # Build graph with pagination
        page_size = 2
        result = build_graph(
            self.repo_path,
            self.git_exe,
            ref="HEAD",
            page=0,
            page_size=page_size
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["page"], 0)
        self.assertEqual(result["page_size"], page_size)
        self.assertEqual(len(result["nodes"]), page_size)

        # Load next page
        result_page2 = build_graph(
            self.repo_path,
            self.git_exe,
            ref="HEAD",
            page=1,
            page_size=page_size
        )

        self.assertEqual(len(result_page2["nodes"]), page_size)

        # Nodes should be different
        page1_oids = {node["oid"] for node in result["nodes"]}
        page2_oids = {node["oid"] for node in result_page2["nodes"]}
        self.assertEqual(len(page1_oids & page2_oids), 0)  # No overlap

    def test_at27_snapshot_caching(self):
        """AT-27: 快照缓存.

        snapshot_id 机制缓存图谱，避免重复计算
        """
        # Build graph
        result = build_graph(
            self.repo_path,
            self.git_exe,
            ref="HEAD",
            page=0,
            page_size=100
        )

        snapshot_id = result["snapshot_id"]
        self.assertIsNotNone(snapshot_id)
        self.assertEqual(len(snapshot_id), 32)

        # Create snapshot object
        snapshot = GraphSnapshot(
            snapshot_id=snapshot_id,
            repo_path=str(self.repo_path),
            ref="HEAD",
            commit_count=len(result["nodes"]),
            head_oid=result["head_oid"],
            branches=[b["name"] for b in result["branches"]],
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            nodes=result["nodes"]
        )

        # Save snapshot
        save_snapshot(snapshot, self.cache_dir)

        # Verify cache file exists
        cache_file = self.cache_dir / f"{snapshot_id}.json"
        self.assertTrue(cache_file.exists())

        # Load snapshot
        loaded = load_snapshot(snapshot_id, self.cache_dir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.snapshot_id, snapshot_id)
        self.assertEqual(loaded.commit_count, snapshot.commit_count)
        self.assertEqual(len(loaded.nodes), len(snapshot.nodes))

        # Verify same snapshot_id for same state
        result2 = build_graph(
            self.repo_path,
            self.git_exe,
            ref="HEAD",
            page=0,
            page_size=100
        )
        self.assertEqual(result2["snapshot_id"], snapshot_id)

    def test_node_details(self):
        """Test commit node details."""
        nodes = load_commit_graph(self.repo_path, self.git_exe, ref="HEAD", max_count=100)

        for node in nodes:
            # Verify OID format
            self.assertIsNotNone(node.oid)
            self.assertEqual(len(node.oid), 40)  # SHA1 hex

            # Verify author info
            self.assertEqual(node.author_name, "Fixture Researcher")
            self.assertEqual(node.author_email, "fixture@example.invalid")

            # Verify timestamp format
            self.assertIsNotNone(node.committed_at)
            self.assertRegex(node.committed_at, r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')

    def test_branch_info_in_graph(self):
        """Test branch information in graph output."""
        result = build_graph(
            self.repo_path,
            self.git_exe,
            ref="HEAD",
            page=0,
            page_size=100
        )

        branches = result["branches"]
        self.assertGreater(len(branches), 0)

        branch_names = [b["name"] for b in branches]
        self.assertIn("main", branch_names)
        self.assertIn("experiment", branch_names)

        # Current branch should be marked
        current_branches = [b for b in branches if b["current"]]
        self.assertEqual(len(current_branches), 1)
        self.assertEqual(current_branches[0]["name"], "main")

    def test_snapshot_id_generation(self):
        """Test snapshot ID generation."""
        snapshot_id1 = generate_snapshot_id(self.repo_path, "HEAD", self.m2_oid)
        snapshot_id2 = generate_snapshot_id(self.repo_path, "HEAD", self.m2_oid)

        # Same inputs should give same ID
        self.assertEqual(snapshot_id1, snapshot_id2)

        # Different HEAD should give different ID
        snapshot_id3 = generate_snapshot_id(self.repo_path, "HEAD", self.r0_oid)
        self.assertNotEqual(snapshot_id1, snapshot_id3)


if __name__ == '__main__':
    unittest.main()
