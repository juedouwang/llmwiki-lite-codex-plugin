"""D-02/D-03 catalog contract. Supersedes old conflict-on-duplicate assumptions."""
import copy
import json
import multiprocessing
from pathlib import Path
import time
from unittest.mock import patch
import unittest

from test_literature_support import Fixture
from literature_catalog import (
    LiteratureCatalog, LiteratureCatalogError, RevisionConflictError, _normalize_doi,
    _normalize_arxiv, _normalize_url, parse_locator, project_for, item_revision,
    source_ref, short_lock, literature_collect, safe_path,
)


def _writer(home, pid, count, queue):
    try:
        for i in range(5):
            literature_collect(pid, f"10.4321/{count}-{i}", f"{count * 10 + i:032x}", home=home)
        queue.put(True)
    except Exception:
        import traceback
        queue.put(traceback.format_exc())


class CatalogTests(Fixture):
    def test_normalization(self):
        for value in ("doi:10.1234/Test", "DOI:10.1234/TEST", "https://DOI.org/10.1234/Test", "http://dx.doi.org/10.1234/test"):
            self.assertEqual(_normalize_doi(value), "10.1234/test")
        for value in ("arXiv:1803.12345v2", "https://arxiv.org/pdf/1803.12345v3.pdf", "http://arxiv.org/abs/1803.12345"):
            self.assertEqual(_normalize_arxiv(value), "1803.12345")
        self.assertEqual(_normalize_arxiv("arxiv:cs/0703001v4"), "cs/0703001")
        self.assertEqual(_normalize_url("HTTPS://Example.COM:443/Path/?x=a%20b&x=2&utm_source=abc#f"), "https://example.com/Path/?x=a%20b&x=2")
        self.assertNotEqual(_normalize_url("http://example.com/Path"), _normalize_url("https://example.com/path/"))
        for url in ("javascript:alert(1)", "https://x:bad", "https:///", "https://user:pass@x/", "https://x/a\nb", "file:///tmp/x", "https://x:99999/", "https://x\\evil"):
            self.assertEqual(_normalize_url(url), "", url)
            with self.assertRaises(LiteratureCatalogError):
                parse_locator(url)

    def test_get_is_read_only_unknown_fields_preserved(self):
        self.assertEqual(self.catalog.list_items()["count"], 0)
        self.assertFalse(self.catalog.literature_dir.exists())
        result = self.collect(title="old title")
        data = self.catalog._load_catalog()
        data["future_field"] = {"keep": 7}
        del data["items"][0]["manual_fields"]
        del data["items"][0]["title_is_placeholder"]
        self.catalog.catalog_path.write_text(json.dumps(data), encoding="utf-8")
        before = self.catalog.catalog_path.read_bytes()
        self.catalog.list_items()
        self.catalog.get_item(result["item_id"])
        self.assertEqual(before, self.catalog.catalog_path.read_bytes())
        self.collect("10.1234/other")
        self.assertEqual(self.catalog._load_catalog()["future_field"], {"keep": 7})
        self.assertEqual(self.item(result)["title"], "old title")

    def test_project_independence_and_overlap_rejected(self):
        a = self.collect(title="Alpha")
        b = self.collect(project_id=self.other["id"], title="Beta")
        self.catalog.remove_item(a["item_id"], expected_item_revision=a["item_revision"])
        self.assertEqual(LiteratureCatalog(self.other["wiki_root"]).get_item(b["item_id"])["title"], "Beta")
        from llmwiki_registry import register_project
        src = self.root / "overlap"
        src.mkdir()
        register_project(str(src), name="overlap", wiki_root=str(Path(self.project["wiki_root"]) / "nested"), state_root=str(self.root / "separate"), home=self.home)
        with self.assertRaisesRegex(LiteratureCatalogError, "重叠"):
            project_for(self.pid, self.home)

    def test_duplicate_upsert_placeholder_and_request_dedup(self):
        a = self.collect("arXiv:1803.12345v1")
        self.assertTrue(self.item(a)["title_is_placeholder"])
        b = self.collect("https://arxiv.org/pdf/1803.12345v2.pdf", title="Actual title")
        self.assertEqual(a["item_id"], b["item_id"])
        self.assertEqual(self.item(b)["title"], "Actual title")
        same = literature_collect(self.pid, "https://arxiv.org/pdf/1803.12345v2.pdf", f"{self.request:032x}", title="Actual title", home=self.home)
        self.assertEqual(same["action"], "unchanged")
        self.assertEqual(same["item_revision"], b["item_revision"])
        self.assertEqual(len(self.item(b)["source_refs"]), 2)
        self.assertTrue(self.item(b)["urls"][0]["url"].endswith("v1"))

    def test_all_identities_checked_active_and_dismissed(self):
        a = self.collect("10.1234/a")
        b = self.collect("1803.12345")
        before = self.catalog.catalog_path.read_bytes()
        with self.assertRaises(LiteratureCatalogError) as ctx:
            self.collect("10.1234/a", arxiv="1803.12345")
        self.assertEqual(ctx.exception.code, "IDENTITY_CONFLICT")
        self.assertEqual(before, self.catalog.catalog_path.read_bytes())
        self.catalog.remove_item(b["item_id"], expected_item_revision=b["item_revision"])
        with self.assertRaises(LiteratureCatalogError):
            self.collect("10.1234/a", arxiv="1803.12345")
        self.assertEqual(self.catalog.list_items()["count"], 1)
        self.assertIsNotNone(self.item(a))

    def test_conflicting_strong_identity_and_same_title(self):
        self.collect("10.1234/a", arxiv="1803.12345", title="Same")
        with self.assertRaises(LiteratureCatalogError):
            self.collect("1803.12345v3", doi="10.1234/b")
        self.collect("10.1234/c", title="Same")
        self.assertEqual(self.catalog.list_items()["count"], 2)

    def test_manual_clear_protection_and_per_item_conflict(self):
        a = self.collect(title="Human", authors=["Human"], year=2026)
        self.collect("10.1234/other")
        a = self.catalog.update_item(a["item_id"], {"title": "Edited", "authors": [], "year": None}, expected_item_revision=a["item_revision"])
        before = a["item_revision"]
        ref = source_ref("record", "records/day.md", "v1", summary="evidence")
        self.catalog.upsert("10.1234/test", title="Model", authors=["Robot"], year=2025, source_refs=[ref])
        item = self.item(a)
        self.assertEqual((item["title"], item["authors"], item["year"]), ("Edited", [], None))
        with self.assertRaises(RevisionConflictError):
            self.catalog.update_item(a["item_id"], {"title": "stale"}, expected_item_revision=before)
        with self.assertRaises(RevisionConflictError):
            self.catalog.remove_item(a["item_id"], expected_item_revision=before)
        for key, value in (("id", "hack"), ("source_refs", []), ("archived", True), ("reading_status", "read")):
            with self.assertRaises(LiteratureCatalogError):
                self.catalog.update_item(a["item_id"], {key: value}, expected_item_revision=item_revision(item))

    def test_edit_address_preserves_auxiliary_and_rejects_identity_change(self):
        a = self.collect("https://example.com/one", doi="10.1234/test")
        self.collect("https://example.com/two", doi="10.1234/test")
        item = self.item(a)
        changed = self.catalog.update_item(a["item_id"], {"locator": "https://example.com/new"}, expected_item_revision=item_revision(item))
        self.assertEqual([u["url"] for u in changed["item"]["urls"]], ["https://example.com/new", "https://example.com/two"])
        with self.assertRaises(LiteratureCatalogError):
            self.catalog.update_item(a["item_id"], {"locator": "10.1234/another"}, expected_item_revision=changed["item_revision"])

    def test_remove_restore_and_late_automatic_result(self):
        source = Path(self.project["source_root"]) / "paper.pdf"
        source.write_bytes(b"%PDF-test")
        note = Path(self.project["wiki_root"]) / "note.md"
        note.write_text('---\npaper_file: paper.pdf\n---\nHuman note', encoding="utf-8")
        a = self.collect(paper_file="paper.pdf", reading_note_paths=["note.md"])
        hashes = (source.read_bytes(), note.read_bytes())
        self.catalog.remove_item(a["item_id"], expected_item_revision=a["item_revision"])
        self.assertEqual(self.catalog._load_catalog()["dismissed_candidates"][0]["item"]["id"], a["item_id"])
        self.assertEqual(self.catalog.remove_item(a["item_id"], expected_item_revision=a["item_revision"])["action"], "unchanged")
        self.assertEqual(self.catalog.upsert("10.1234/test")["reason"], "dismissed")
        b = self.collect()
        self.assertEqual((b["action"], b["item_id"]), ("restored", a["item_id"]))
        self.assertEqual(len(self.item(b)["attachments"]), 1)
        self.assertEqual((source.read_bytes(), note.read_bytes()), hashes)

    def test_paths_bindings_and_request_boundary(self):
        for path in ("../secret.pdf", "C:/other/paper.pdf", "/outside.pdf", "x:stream"):
            with self.assertRaises(LiteratureCatalogError):
                self.collect(paper_file=path)
        source = Path(self.project["source_root"]) / "p.pdf"
        source.write_bytes(b"pdf")
        wiki = Path(self.project["wiki_root"])
        (wiki / "note.md").write_text('---\npaper_file: other/p.pdf\n---\nNote', encoding="utf-8")
        with self.assertRaises(LiteratureCatalogError):
            self.collect(paper_file="p.pdf", reading_note_paths=["note.md"])
        for project in ("", "alpha", self.project["source_root"]):
            with self.assertRaises(LiteratureCatalogError):
                literature_collect(project, "10.1234/a", "a" * 32, home=self.home)
        for request in ("../x", "A" * 32, "1"):
            with self.assertRaises(LiteratureCatalogError):
                literature_collect(self.pid, "10.1234/a", request, home=self.home)
        for fields in ({"authors": "Alice"}, {"year": True}, {"year": 99}, {"title": ""}, {"title": "a" * 1001}, {"source": {"summary": "x" * 1001}}):
            with self.assertRaises(LiteratureCatalogError):
                self.collect(**fields)

    def test_limit_preserves_existing_sources_and_reports_unsaved(self):
        a = self.collect()
        data = self.catalog._load_catalog()
        data["items"][0]["source_refs"] = [source_ref("manual", f"manual:{i}", str(i)) for i in range(100)]
        self.catalog._save_catalog(data)
        original = copy.deepcopy(data["items"][0]["source_refs"])
        result = self.collect()
        self.assertEqual(result["warnings"][0]["code"], "limit_reached")
        self.assertEqual(self.item(a)["source_refs"], original)
        self.assertEqual(result["action"], "unchanged")

    def test_all_material_caps_preserve_existing_and_reject_oversized_file(self):
        a = self.collect()
        data = self.catalog._load_catalog()
        item = data["items"][0]
        item["urls"] = [{"url": f"https://example.com/{i}"} for i in range(20)]
        item["attachments"] = [{"path": f"old-{i}.pdf"} for i in range(20)]
        item["reading_note_paths"] = [f"old-{i}.md" for i in range(20)]
        self.catalog._save_catalog(data)
        before = copy.deepcopy(item)
        result = self.catalog.upsert("https://example.com/new", doi="10.1234/test", attachments=[{"path": "new.pdf"}], reading_note_paths=["new.md"])
        self.assertEqual({w["field"] for w in result["warnings"]}, {"urls", "attachments", "reading_note_paths"})
        current = self.item(a)
        for key in ("urls", "attachments", "reading_note_paths"):
            self.assertEqual(current[key], before[key])
        oversized = Path(self.project["source_root"]) / "large.pdf"
        with oversized.open("wb") as stream:
            stream.truncate(50 * 1024 * 1024 + 1)
        with self.assertRaises(LiteratureCatalogError):
            self.collect(paper_file="large.pdf")
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(LiteratureCatalogError):
                safe_path(self.project["source_root"], "linked.pdf")

    def test_two_processes_do_not_lose_items(self):
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        workers = [context.Process(target=_writer, args=(self.home, self.pid, i, queue)) for i in (1, 2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(20)
            self.assertEqual(worker.exitcode, 0)
        for _ in workers:
            result = queue.get(timeout=2)
            self.assertIs(result, True, result)
        self.assertEqual(self.catalog.list_items()["count"], 10)
        self.assertFalse(list(self.catalog.literature_dir.glob("*.tmp")))

    def test_lock_initialization_never_writes_into_locked_byte(self):
        lock = self.catalog.literature_dir / ".empty-lock"
        with short_lock(lock):
            self.assertEqual(lock.stat().st_size, 0)
            with self.assertRaises(LiteratureCatalogError) as ctx:
                with short_lock(lock, timeout=.025):
                    pass
            self.assertEqual(ctx.exception.code, "LOCK_TIMEOUT")
        self.assertEqual(lock.stat().st_size, 0)

    @unittest.skipUnless(__import__("os").name == "nt", "Windows path spelling regression")
    def test_windows_extended_prefix_not_false_escape(self):
        original = Path.resolve
        target = Path(self.project["wiki_root"]) / ".literature" / "catalog.json"
        def extended(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            return Path("\\\\?\\" + str(value)) if path == target else value
        with patch.object(Path, "resolve", extended):
            self.assertEqual(safe_path(self.project["wiki_root"], ".literature/catalog.json"), target)
        for value in ("\nhttps://example.com", "https://example.com\t", "\x00doi:10.1234/a"):
            with self.assertRaises(LiteratureCatalogError):
                parse_locator(value)

    def test_lock_timeout_bounded(self):
        lock = self.catalog.literature_dir / ".lock"
        with short_lock(lock):
            start = time.monotonic()
            with self.assertRaises(LiteratureCatalogError) as ctx:
                with short_lock(lock, timeout=.05):
                    pass
            self.assertEqual(ctx.exception.code, "LOCK_TIMEOUT")
            self.assertLess(time.monotonic() - start, .5)

    def test_no_network_discovery_or_subprocess(self):
        with patch("socket.create_connection", side_effect=AssertionError("network")), patch("subprocess.Popen", side_effect=AssertionError("process")), patch.object(Path, "rglob", side_effect=AssertionError("scan")):
            self.collect()
            self.catalog.list_items(limit=50)


if __name__ == "__main__":
    unittest.main()
