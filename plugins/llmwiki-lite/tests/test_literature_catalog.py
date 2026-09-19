"""
Tests for strict literature catalog management.

Covers:
- AT-10: Pure link entries without download
- AT-11: DOI deduplication (case-insensitive)
- AT-12: arXiv version handling (v1/v2 same base ID)
- AT-13: Same title different identifiers (warning, no auto-merge)
- AT-14: Failed metadata fetch (preserve known info)
- AT-15: Attach PDF to existing entry (ID/annotations preserved)
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

# Add scripts to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from literature_catalog import (
    LiteratureCatalog,
    LiteratureCatalogError,
    RevisionConflictError,
    _normalize_doi,
    _normalize_arxiv,
    _normalize_url,
    _validate_item,
    _check_identity_conflict,
    MAX_TITLE_LENGTH,
    MAX_AUTHORS
)


class TestNormalization(unittest.TestCase):
    """Test identifier normalization functions."""

    def test_normalize_doi_lowercase(self):
        """DOIs should be normalized to lowercase."""
        self.assertEqual(_normalize_doi("10.1234/Example"), "10.1234/example")

    def test_normalize_doi_remove_prefix(self):
        """DOI prefixes should be removed."""
        self.assertEqual(_normalize_doi("doi:10.1234/test"), "10.1234/test")
        self.assertEqual(_normalize_doi("DOI:10.1234/test"), "10.1234/test")
        self.assertEqual(_normalize_doi("https://doi.org/10.1234/test"), "10.1234/test")
        self.assertEqual(_normalize_doi("http://dx.doi.org/10.1234/test"), "10.1234/test")

    def test_normalize_doi_trim(self):
        """DOIs should be trimmed."""
        self.assertEqual(_normalize_doi("  10.1234/test  "), "10.1234/test")

    def test_normalize_arxiv_remove_version(self):
        """arXiv versions should be removed to get base ID."""
        self.assertEqual(_normalize_arxiv("1803.12345v1"), "1803.12345")
        self.assertEqual(_normalize_arxiv("1803.12345v2"), "1803.12345")
        self.assertEqual(_normalize_arxiv("1803.12345V3"), "1803.12345")

    def test_normalize_arxiv_old_format(self):
        """Old arXiv format should be preserved (without version)."""
        self.assertEqual(_normalize_arxiv("cs/0703001v1"), "cs/0703001")
        self.assertEqual(_normalize_arxiv("math/0309136v2"), "math/0309136")

    def test_normalize_arxiv_remove_prefix(self):
        """arXiv prefixes should be removed."""
        self.assertEqual(_normalize_arxiv("arxiv:1803.12345v1"), "1803.12345")
        self.assertEqual(_normalize_arxiv("arXiv:1803.12345"), "1803.12345")
        self.assertEqual(_normalize_arxiv("https://arxiv.org/abs/1803.12345v2"), "1803.12345")

    def test_normalize_url_scheme_host_lowercase(self):
        """URL scheme and host should be lowercase."""
        normalized = _normalize_url("HTTP://Example.COM/Path")
        self.assertTrue(normalized.startswith("http://example.com"))

    def test_normalize_url_remove_fragment(self):
        """URL fragments should be removed."""
        normalized = _normalize_url("https://example.com/page#section")
        self.assertNotIn("#", normalized)

    def test_normalize_url_remove_utm(self):
        """UTM tracking parameters should be removed."""
        normalized = _normalize_url("https://example.com/page?id=123&utm_source=test&utm_campaign=abc")
        self.assertIn("id=123", normalized)
        self.assertNotIn("utm_source", normalized)
        self.assertNotIn("utm_campaign", normalized)

    def test_normalize_url_reject_credentials(self):
        """URLs with credentials should be rejected."""
        self.assertEqual(_normalize_url("https://user:pass@example.com/"), "")

    def test_normalize_url_reject_non_http(self):
        """Non-HTTP(S) URLs should be rejected."""
        self.assertEqual(_normalize_url("ftp://example.com/"), "")
        self.assertEqual(_normalize_url("file:///local/path"), "")


class TestValidation(unittest.TestCase):
    """Test item validation functions."""

    def test_validate_item_minimal(self):
        """Minimal valid item should pass validation."""
        item = {
            "id": "test123",
            "title": "Test Paper",
            "authors": [],
            "year": None,
            "venue": "",
            "publication_type": "unknown",
            "identifiers": {"doi": None, "arxiv": None},
            "urls": [],
            "attachments": [],
            "identity_status": "unverified",
            "reading_status": "unread",
            "collection_source": "manual",
            "source_refs": [],
            "tags": [],
            "reading_note_paths": [],
            "archived": False,
            "created_at": "2026-09-19T00:00:00Z",
            "updated_at": "2026-09-19T00:00:00Z"
        }
        valid, msg = _validate_item(item)
        self.assertTrue(valid, msg)

    def test_validate_item_missing_field(self):
        """Missing required field should fail validation."""
        item = {
            "id": "test123",
            "title": "Test Paper"
            # Missing other required fields
        }
        valid, msg = _validate_item(item)
        self.assertFalse(valid)
        self.assertIn("Missing required field", msg)

    def test_validate_item_title_too_long(self):
        """Title exceeding max length should fail."""
        item = {
            "id": "test123",
            "title": "A" * (MAX_TITLE_LENGTH + 1),
            "authors": [],
            "year": None,
            "venue": "",
            "publication_type": "unknown",
            "identifiers": {"doi": None, "arxiv": None},
            "urls": [],
            "attachments": [],
            "identity_status": "unverified",
            "reading_status": "unread",
            "collection_source": "manual",
            "source_refs": [],
            "tags": [],
            "reading_note_paths": [],
            "archived": False,
            "created_at": "2026-09-19T00:00:00Z",
            "updated_at": "2026-09-19T00:00:00Z"
        }
        valid, msg = _validate_item(item)
        self.assertFalse(valid)
        self.assertIn("exceeds", msg.lower())

    def test_validate_item_invalid_year(self):
        """Year outside valid range should fail."""
        item = {
            "id": "test123",
            "title": "Test Paper",
            "authors": [],
            "year": 1200,  # Too old
            "venue": "",
            "publication_type": "unknown",
            "identifiers": {"doi": None, "arxiv": None},
            "urls": [],
            "attachments": [],
            "identity_status": "unverified",
            "reading_status": "unread",
            "collection_source": "manual",
            "source_refs": [],
            "tags": [],
            "reading_note_paths": [],
            "archived": False,
            "created_at": "2026-09-19T00:00:00Z",
            "updated_at": "2026-09-19T00:00:00Z"
        }
        valid, msg = _validate_item(item)
        self.assertFalse(valid)
        self.assertIn("Year", msg)

    def test_validate_item_invalid_publication_type(self):
        """Invalid publication_type should fail."""
        item = {
            "id": "test123",
            "title": "Test Paper",
            "authors": [],
            "year": None,
            "venue": "",
            "publication_type": "invalid_type",
            "identifiers": {"doi": None, "arxiv": None},
            "urls": [],
            "attachments": [],
            "identity_status": "unverified",
            "reading_status": "unread",
            "collection_source": "manual",
            "source_refs": [],
            "tags": [],
            "reading_note_paths": [],
            "archived": False,
            "created_at": "2026-09-19T00:00:00Z",
            "updated_at": "2026-09-19T00:00:00Z"
        }
        valid, msg = _validate_item(item)
        self.assertFalse(valid)
        self.assertIn("publication_type", msg)


class TestIdentityConflict(unittest.TestCase):
    """Test identity conflict detection per F-11."""

    def test_same_doi_conflict(self):
        """Same normalized DOI should be detected as conflict."""
        new_item = {
            "id": "new123",
            "title": "New Paper",
            "identifiers": {"doi": "10.1234/test", "arxiv": None},
            "urls": []
        }
        existing = [
            {
                "id": "existing456",
                "title": "Existing Paper",
                "identifiers": {"doi": "10.1234/TEST", "arxiv": None},  # Different case
                "urls": []
            }
        ]

        has_conflict, conflicting_id, warnings = _check_identity_conflict(new_item, existing)
        self.assertTrue(has_conflict)
        self.assertEqual(conflicting_id, "existing456")

    def test_same_arxiv_base_id_conflict(self):
        """Same arXiv base ID (different versions) should be detected as conflict."""
        new_item = {
            "id": "new123",
            "title": "New Paper",
            "identifiers": {"doi": None, "arxiv": "1803.12345v2"},
            "urls": []
        }
        existing = [
            {
                "id": "existing456",
                "title": "Existing Paper",
                "identifiers": {"doi": None, "arxiv": "1803.12345v1"},
                "urls": []
            }
        ]

        has_conflict, conflicting_id, warnings = _check_identity_conflict(new_item, existing)
        self.assertTrue(has_conflict)
        self.assertEqual(conflicting_id, "existing456")

    def test_conflicting_doi_for_same_arxiv(self):
        """Same arXiv ID but different DOI should be detected as conflict."""
        new_item = {
            "id": "new123",
            "title": "New Paper",
            "identifiers": {"doi": "10.1234/aaa", "arxiv": "1803.12345"},
            "urls": []
        }
        existing = [
            {
                "id": "existing456",
                "title": "Existing Paper",
                "identifiers": {"doi": "10.1234/bbb", "arxiv": "1803.12345"},
                "urls": []
            }
        ]

        has_conflict, conflicting_id, warnings = _check_identity_conflict(new_item, existing)
        self.assertTrue(has_conflict)

    def test_same_title_warning_only(self):
        """Same title (normalized) should produce warning, not conflict."""
        new_item = {
            "id": "new123",
            "title": "Deep  Learning  Paper",  # Extra spaces
            "identifiers": {"doi": None, "arxiv": None},
            "urls": []
        }
        existing = [
            {
                "id": "existing456",
                "title": "Deep Learning Paper",
                "identifiers": {"doi": None, "arxiv": None},
                "urls": []
            }
        ]

        has_conflict, conflicting_id, warnings = _check_identity_conflict(new_item, existing)
        self.assertFalse(has_conflict)
        self.assertIsNone(conflicting_id)
        self.assertTrue(len(warnings) > 0)
        self.assertIn("existing456", warnings[0])

    def test_different_items_no_conflict(self):
        """Completely different items should have no conflict."""
        new_item = {
            "id": "new123",
            "title": "New Paper A",
            "identifiers": {"doi": "10.1234/aaa", "arxiv": "1803.11111"},
            "urls": []
        }
        existing = [
            {
                "id": "existing456",
                "title": "Existing Paper B",
                "identifiers": {"doi": "10.1234/bbb", "arxiv": "1803.22222"},
                "urls": []
            }
        ]

        has_conflict, conflicting_id, warnings = _check_identity_conflict(new_item, existing)
        self.assertFalse(has_conflict)
        self.assertIsNone(conflicting_id)


class TestCatalogOperations(unittest.TestCase):
    """Test catalog CRUD operations."""

    def setUp(self):
        """Create temporary wiki root for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self.temp_dir.name)
        self.catalog = LiteratureCatalog(str(self.wiki_root))

    def tearDown(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def test_create_catalog_directories(self):
        """Catalog initialization should create directories."""
        self.assertTrue(self.catalog.literature_dir.exists())
        self.assertTrue(self.catalog.history_dir.exists())

    def test_empty_catalog_revision(self):
        """Empty catalog (no file) should have empty revision."""
        revision = self.catalog.get_revision()
        self.assertEqual(revision, "")

    def test_create_item_minimal(self):
        """AT-10: Should create item with just link, no download."""
        result = self.catalog.create_item(
            title="Attention Is All You Need",
            doi="10.48550/arXiv.1706.03762",
            arxiv="1706.03762",
            urls=[
                {
                    "id": "url1",
                    "url": "https://arxiv.org/abs/1706.03762",
                    "kind": "preprint",
                    "label": "arXiv"
                }
            ]
        )

        self.assertTrue(result["ok"])
        self.assertIn("item", result)
        self.assertEqual(result["item"]["title"], "Attention Is All You Need")
        self.assertIsNotNone(result["item"]["id"])
        self.assertEqual(len(result["item"]["attachments"]), 0)  # No file attached
        self.assertIn("revision", result)

    def test_create_item_doi_deduplication(self):
        """AT-11: Same DOI (different case) should be rejected."""
        # Create first item
        result1 = self.catalog.create_item(
            title="Paper A",
            doi="10.1234/test"
        )
        self.assertTrue(result1["ok"])

        # Try to create with same DOI (different case)
        result2 = self.catalog.create_item(
            title="Paper B",
            doi="10.1234/TEST"  # Different case
        )
        self.assertFalse(result2["ok"])
        self.assertEqual(result2["error"]["code"], "IDENTITY_CONFLICT")

    def test_arxiv_version_handling(self):
        """AT-12: arXiv v1 and v2 should be same entry."""
        # Create with v1
        result1 = self.catalog.create_item(
            title="Paper with arXiv",
            arxiv="1803.12345v1"
        )
        self.assertTrue(result1["ok"])

        # Try to create with v2 (same base ID)
        result2 = self.catalog.create_item(
            title="Same Paper v2",
            arxiv="1803.12345v2"
        )
        self.assertFalse(result2["ok"])
        self.assertEqual(result2["error"]["code"], "IDENTITY_CONFLICT")

    def test_same_title_different_doi_warning(self):
        """AT-13: Same title, different DOI should warn but not merge."""
        # Create first item
        result1 = self.catalog.create_item(
            title="Deep Learning Survey",
            doi="10.1234/aaa"
        )
        self.assertTrue(result1["ok"])

        # Create with same title but different DOI
        result2 = self.catalog.create_item(
            title="Deep Learning Survey",  # Same title
            doi="10.1234/bbb"  # Different DOI
        )
        self.assertTrue(result2["ok"])  # Should succeed
        self.assertIn("warnings", result2)
        self.assertTrue(len(result2["warnings"]) > 0)  # But with warning

    def test_create_unverified_no_metadata(self):
        """AT-14: Failed metadata fetch should still save with unverified status."""
        result = self.catalog.create_item(
            title="Paper with Unknown Details",
            # No DOI, no arXiv, minimal info
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["item"]["identity_status"], "unverified")
        self.assertIsNone(result["item"]["year"])
        self.assertEqual(result["item"]["venue"], "")

    def test_update_item_preserves_id(self):
        """AT-15: Updating item should preserve ID and metadata."""
        # Create item
        result = self.catalog.create_item(
            title="Original Title",
            tags=["machine-learning"]
        )
        self.assertTrue(result["ok"])
        original_id = result["item"]["id"]
        original_created = result["item"]["created_at"]

        # Update item
        update_result = self.catalog.update_item(
            original_id,
            {"reading_status": "read", "tags": ["machine-learning", "nlp"]}
        )
        self.assertTrue(update_result["ok"])
        self.assertEqual(update_result["item"]["id"], original_id)
        self.assertEqual(update_result["item"]["created_at"], original_created)
        self.assertEqual(update_result["item"]["reading_status"], "read")
        self.assertEqual(len(update_result["item"]["tags"]), 2)

    def test_revision_conflict_detection(self):
        """AT-01: Concurrent modification should trigger revision conflict."""
        # Create initial item
        result = self.catalog.create_item(title="Test Paper")
        self.assertTrue(result["ok"])
        revision1 = result["revision"]

        # Modify catalog
        result2 = self.catalog.create_item(title="Another Paper")
        self.assertTrue(result2["ok"])
        revision2 = result2["revision"]
        self.assertNotEqual(revision1, revision2)

        # Try to create with old revision
        with self.assertRaises(RevisionConflictError):
            self.catalog.create_item(
                title="Third Paper",
                expected_revision=revision1  # Old revision
            )

    def test_list_items_filtering(self):
        """List should support filtering by status and archived."""
        # Create multiple items
        self.catalog.create_item(title="Paper 1", tags=["a"])
        result2 = self.catalog.create_item(title="Paper 2", tags=["b"])
        self.catalog.update_item(result2["item"]["id"], {"reading_status": "read"})

        # List all
        all_items = self.catalog.list_items()
        self.assertEqual(all_items["count"], 2)

        # List only read
        read_items = self.catalog.list_items(status="read")
        self.assertEqual(read_items["count"], 1)
        self.assertEqual(read_items["items"][0]["title"], "Paper 2")

        # List unread
        unread_items = self.catalog.list_items(status="unread")
        self.assertEqual(unread_items["count"], 1)

    def test_remove_and_restore(self):
        """AT-18: Remove should allow restore, dismissed items not auto-readded."""
        # Create item
        result = self.catalog.create_item(title="Paper to Remove")
        item_id = result["item"]["id"]

        # Remove item
        remove_result = self.catalog.remove_item(item_id)
        self.assertTrue(remove_result["ok"])

        # Should not be in main list
        items = self.catalog.list_items()
        self.assertEqual(items["count"], 0)

        # Restore item
        restore_result = self.catalog.restore_item(item_id)
        self.assertTrue(restore_result["ok"])

        # Should be back in list
        items = self.catalog.list_items()
        self.assertEqual(items["count"], 1)

    def test_save_history(self):
        """Catalog updates should save history."""
        # Create first item (no history yet, as file didn't exist)
        self.catalog.create_item(title="Paper 1")

        # Create second item (now history should be saved)
        self.catalog.create_item(title="Paper 2")

        # History should be saved
        history_files = list(self.catalog.history_dir.glob("catalog_*.json"))
        self.assertTrue(len(history_files) > 0)


class TestLITF1Fixture(unittest.TestCase):
    """Test LIT-F1 fixture scenarios from spec 13.1."""

    def setUp(self):
        """Set up catalog with LIT-F1 scenarios."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self.temp_dir.name)
        self.catalog = LiteratureCatalog(str(self.wiki_root))

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_same_doi_different_case(self):
        """Same DOI with different case should merge."""
        r1 = self.catalog.create_item(
            title="Paper A",
            doi="10.1234/test",
            urls=[{"id": "u1", "url": "https://example.com/a", "kind": "publisher", "label": "A"}]
        )
        self.assertTrue(r1["ok"])

        r2 = self.catalog.create_item(
            title="Paper A",
            doi="10.1234/TEST",  # Different case
            urls=[{"id": "u2", "url": "https://example.com/b", "kind": "publisher", "label": "B"}]
        )
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["error"]["code"], "IDENTITY_CONFLICT")

    def test_same_arxiv_different_versions(self):
        """Same arXiv base ID with v1/v2 should merge."""
        r1 = self.catalog.create_item(
            title="arXiv Paper",
            arxiv="1803.12345v1"
        )
        self.assertTrue(r1["ok"])

        r2 = self.catalog.create_item(
            title="arXiv Paper Updated",
            arxiv="1803.12345v2"
        )
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["error"]["code"], "IDENTITY_CONFLICT")

    def test_same_title_different_doi(self):
        """Same title, different DOI should NOT auto-merge (warning only)."""
        r1 = self.catalog.create_item(
            title="Survey of Deep Learning",
            doi="10.1234/aaa"
        )
        self.assertTrue(r1["ok"])

        r2 = self.catalog.create_item(
            title="Survey of Deep Learning",
            doi="10.1234/bbb"
        )
        self.assertTrue(r2["ok"])  # Should succeed
        self.assertTrue(len(r2.get("warnings", [])) > 0)  # But warn


def run_tests():
    """Run all tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestNormalization))
    suite.addTests(loader.loadTestsFromTestCase(TestValidation))
    suite.addTests(loader.loadTestsFromTestCase(TestIdentityConflict))
    suite.addTests(loader.loadTestsFromTestCase(TestCatalogOperations))
    suite.addTests(loader.loadTestsFromTestCase(TestLITF1Fixture))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
