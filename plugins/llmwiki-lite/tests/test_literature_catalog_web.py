"""
Tests for literature catalog web UI (T-04).

Covers:
- AT-16: Migration with LIT-F1 fixture
- AT-17: Idempotent migration and rollback
- AT-18: User removal and AI re-mention handling
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

# Add scripts to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from literature_catalog import LiteratureCatalog
from literature_catalog_web import (
    _classify_migration_file,
    _scan_migration_candidates,
    apply_migration,
    rollback_migration,
)


class TestMigrationClassification(unittest.TestCase):
    """Test file classification for migration."""

    def test_classify_valid_paper_pdf(self):
        """Large PDF should be classified as candidate."""
        result = _classify_migration_file("paper.pdf", 500_000)
        self.assertEqual(result["category"], "candidate")

    def test_classify_matplotlib_icon(self):
        """Matplotlib icon should be excluded."""
        result = _classify_migration_file("matplotlib_icon.pdf", 10_000)
        self.assertEqual(result["category"], "excluded")
        self.assertIn("图标", result["reason"])

    def test_classify_experiment_report(self):
        """Experiment report should be excluded."""
        result = _classify_migration_file("experiment_report.pdf", 200_000)
        self.assertEqual(result["category"], "excluded")
        self.assertIn("实验报告", result["reason"])

    def test_classify_dependency_html(self):
        """Dependency HTML should be excluded."""
        result = _classify_migration_file("requirements.html", 5_000)
        self.assertEqual(result["category"], "excluded")

    def test_classify_small_pdf(self):
        """Small PDF should need confirmation."""
        result = _classify_migration_file("small.pdf", 50_000)
        self.assertEqual(result["category"], "needs_confirmation")

    def test_classify_html_paper(self):
        """HTML file should need confirmation."""
        result = _classify_migration_file("paper.html", 100_000)
        self.assertEqual(result["category"], "needs_confirmation")


class TestLitF1Migration(unittest.TestCase):
    """Test AT-16: Migration with LIT-F1 fixture."""

    def setUp(self):
        """Create LIT-F1 fixture scenario."""
        self.temp_dir = tempfile.mkdtemp()
        self.source_root = Path(self.temp_dir) / "project"
        self.wiki_root = self.source_root / "wiki"
        self.papers_dir = self.source_root / "references" / "papers"

        self.source_root.mkdir()
        self.wiki_root.mkdir()
        self.papers_dir.mkdir(parents=True)

        # Create LIT-F1 files
        # 2 valid paper PDFs
        (self.papers_dir / "attention_is_all_you_need.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 500_000)
        (self.papers_dir / "bert_pretraining.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 600_000)

        # 1 matplotlib icon PDF
        (self.papers_dir / "matplotlib_logo.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 5_000)

        # 1 dependency HTML
        (self.papers_dir / "dependencies.html").write_bytes(b"<html></html>")

        # 1 experiment report
        (self.papers_dir / "experiment_qa_render.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 100_000)

        # 1 valid reading note (not in papers dir, in wiki)
        note_path = self.wiki_root / "reading_notes"
        note_path.mkdir()
        (note_path / "attention_reading.md").write_text(
            "---\ntitle: Attention精读\npaper_file: references/papers/attention_is_all_you_need.pdf\n---\n\n内容"
        )

    def tearDown(self):
        """Clean up temp directory."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scan_identifies_candidates(self):
        """Scanning should identify valid papers and exclude others."""
        result = _scan_migration_candidates(self.source_root)

        self.assertEqual(len(result["candidates"]), 2)
        self.assertGreaterEqual(len(result["excluded"]), 3)  # icon, html, report

        candidate_names = [c["path"] for c in result["candidates"]]
        self.assertIn("references/papers/attention_is_all_you_need.pdf", candidate_names)
        self.assertIn("references/papers/bert_pretraining.pdf", candidate_names)

        excluded_names = [e["path"] for e in result["excluded"]]
        self.assertIn("references/papers/matplotlib_logo.pdf", excluded_names)
        self.assertIn("references/papers/experiment_qa_render.pdf", excluded_names)

    def test_migration_does_not_add_excluded_to_catalog(self):
        """AT-16: Dependencies/icons/reports should not enter formal catalog."""
        catalog = LiteratureCatalog(self.wiki_root)

        # Apply migration with only valid papers
        selected_paths = [
            "references/papers/attention_is_all_you_need.pdf",
            "references/papers/bert_pretraining.pdf",
        ]

        result = apply_migration(
            catalog=catalog,
            source_root=self.source_root,
            selected_paths=selected_paths,
            collection_source="migration"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["imported"]), 2)

        # Check catalog only has 2 items
        items = catalog.list_items()["items"]
        self.assertEqual(len(items), 2)

        # Excluded files should not be in catalog
        all_paths = [item.get("attachments", [{}])[0].get("path", "") for item in items]
        self.assertNotIn("references/papers/matplotlib_logo.pdf", all_paths)
        self.assertNotIn("references/papers/experiment_qa_render.pdf", all_paths)


class TestMigrationIdempotence(unittest.TestCase):
    """Test AT-17: Migration is idempotent and rollback works."""

    def setUp(self):
        """Create test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.source_root = Path(self.temp_dir) / "project"
        self.wiki_root = self.source_root / "wiki"
        self.papers_dir = self.source_root / "references" / "papers"

        self.source_root.mkdir()
        self.wiki_root.mkdir()
        self.papers_dir.mkdir(parents=True)

        (self.papers_dir / "paper1.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 500_000)

    def tearDown(self):
        """Clean up."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_migration_is_idempotent(self):
        """AT-17: Running migration twice should be idempotent."""
        catalog = LiteratureCatalog(self.wiki_root)
        selected_paths = ["references/papers/paper1.pdf"]

        # First migration
        result1 = apply_migration(catalog, self.source_root, selected_paths, "migration")
        self.assertTrue(result1["ok"])
        self.assertEqual(len(result1["imported"]), 1)

        revision1 = catalog.load()["revision"]

        # Second migration (should be idempotent)
        result2 = apply_migration(catalog, self.source_root, selected_paths, "migration")
        self.assertTrue(result2["ok"])
        self.assertEqual(len(result2["imported"]), 0)  # Already imported
        self.assertEqual(len(result2["skipped"]), 1)

        revision2 = catalog.load()["revision"]

        # Revision should change (metadata update) but items should remain same
        items = catalog.list_items()["items"]
        self.assertEqual(len(items), 1)

    def test_rollback_restores_catalog_only(self):
        """AT-17: Rollback should restore catalog/mapping, not delete original files."""
        catalog = LiteratureCatalog(self.wiki_root)
        selected_paths = ["references/papers/paper1.pdf"]

        # Perform migration
        result = apply_migration(catalog, self.source_root, selected_paths, "migration")
        self.assertTrue(result["ok"])
        migration_id = result["migration_id"]

        # Check file still exists
        original_file = self.papers_dir / "paper1.pdf"
        self.assertTrue(original_file.exists())

        # Rollback
        rollback_result = rollback_migration(catalog, migration_id)
        self.assertTrue(rollback_result["ok"])

        # Catalog should be empty
        items = catalog.list_items()["items"]
        self.assertEqual(len(items), 0)

        # Original file should still exist (not deleted)
        self.assertTrue(original_file.exists())

        # Migration manifest should exist
        migrations_dir = self.wiki_root / ".literature" / "migrations"
        self.assertTrue(migrations_dir.exists())


class TestUserRemovalAndRestore(unittest.TestCase):
    """Test AT-18: User removal and AI re-mention handling."""

    def setUp(self):
        """Create test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.wiki_root = Path(self.temp_dir) / "wiki"
        self.wiki_root.mkdir()

    def tearDown(self):
        """Clean up."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_removed_item_goes_to_dismissed(self):
        """AT-18: Removed item should go to dismissed_candidates."""
        catalog = LiteratureCatalog(self.wiki_root)

        # Create item
        result = catalog.create_item(
            title="Test Paper",
            doi="10.1234/test"
        )
        item_id = result["item"]["id"]

        # Remove item
        remove_result = catalog.remove_item(item_id)
        self.assertTrue(remove_result["ok"])

        # Item should not be in main list
        items = catalog.list_items()["items"]
        self.assertEqual(len(items), 0)

        # Item should be in dismissed
        data = catalog.load()
        dismissed = data["dismissed_candidates"]
        self.assertEqual(len(dismissed), 1)
        self.assertEqual(dismissed[0]["id"], item_id)

    def test_cannot_auto_readd_dismissed_item(self):
        """AT-18: Same strong identifier should not auto re-add after removal."""
        catalog = LiteratureCatalog(self.wiki_root)

        # Create and remove item
        result = catalog.create_item(title="Test Paper", doi="10.1234/test")
        item_id = result["item"]["id"]
        catalog.remove_item(item_id)

        # Try to create again with same DOI (simulating AI re-mention)
        result2 = catalog.create_item(title="Test Paper Again", doi="10.1234/test")

        # Should detect as dismissed
        self.assertIn("dismissed", result2.get("warnings", [{}])[0].get("type", ""))

    def test_can_explicitly_restore_dismissed_item(self):
        """AT-18: User can explicitly restore a dismissed item."""
        catalog = LiteratureCatalog(self.wiki_root)

        # Create, remove, then restore
        result = catalog.create_item(title="Test Paper", doi="10.1234/test")
        item_id = result["item"]["id"]
        catalog.remove_item(item_id)

        restore_result = catalog.restore_item(item_id)
        self.assertTrue(restore_result["ok"])

        # Item should be back in main list
        items = catalog.list_items()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], item_id)

    def test_reading_notes_remain_after_removal(self):
        """AT-18: Old reading notes should remain accessible after item removal."""
        catalog = LiteratureCatalog(self.wiki_root)

        # Create item with reading note path
        result = catalog.create_item(
            title="Test Paper",
            doi="10.1234/test",
            reading_note_paths=["wiki/notes/test_reading.md"]
        )
        item_id = result["item"]["id"]

        # Create the actual note file
        note_path = self.wiki_root / "notes" / "test_reading.md"
        note_path.parent.mkdir(parents=True)
        note_path.write_text("# Test Reading\n\nContent here")

        # Remove item
        catalog.remove_item(item_id)

        # Note file should still exist
        self.assertTrue(note_path.exists())
        self.assertEqual(note_path.read_text(), "# Test Reading\n\nContent here")


if __name__ == "__main__":
    unittest.main()
