"""Real loopback HTTP tests through the production create_server routes."""
import http.client
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from test_literature_support import Fixture
from literature_catalog import LiteratureCatalogError
from literature_catalog_web import (
    literature_catalog_list_page, literature_detail_page,
    apply_migration, rollback_migration,
)


def isolated_server(home):
    from web_server import create_server
    server = create_server(home, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class WebTests(Fixture):
    def setUp(self):
        super().setUp()
        self.server, self.thread = isolated_server(self.home)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.api = f"/api/project/{self.pid}/literature/"
        self.base = f"/project/{self.pid}/literature"
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request_http(self, path, data=None, *, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        self.addCleanup(conn.close)
        payload = raw if raw is not None else (json.dumps(data) if data is not None else None)
        hdr = {"Origin": self.origin, "Content-Type": "application/json", "X-Literature-Request": "1"}
        hdr.update(headers or {})
        conn.request("POST" if payload is not None else "GET", path, body=payload, headers=hdr)
        response = conn.getresponse()
        content = response.read().decode()
        return response.status, (json.loads(content) if "application/json" in response.getheader("Content-Type", "") else content), dict(response.headers)

    def test_http_manual_roundtrip_restore_restart(self):
        status, a, _ = self.request_http(self.api + "add", {"locator": "10.1234/Paper", "request_id": "a" * 32})
        self.assertEqual(status, 200)
        self.assertEqual(a["action"], "created")
        self.assertIn(a["item_id"], self.request_http(self.base)[1])
        status, detail, _ = self.request_http(a["url"])
        self.assertEqual(status, 200)
        self.assertIn("10.1234/Paper", detail)
        status, b, _ = self.request_http(self.api + f"item/{a['item_id']}/update", {"title": "Human", "locator": "https://doi.org/10.1234/paper", "authors": [], "year": None, "expected_item_revision": a["item_revision"]})
        self.assertEqual(status, 200)
        self.assertEqual(self.item(a)["title"], "Human")
        status, _, _ = self.request_http(self.api + f"item/{a['item_id']}/delete", {"expected_item_revision": b["item_revision"]})
        self.assertEqual(status, 200)
        status, c, _ = self.request_http(self.api + "add", {"locator": "10.1234/paper", "request_id": "b" * 32})
        self.assertEqual((status, c["action"], c["item_id"]), (200, "restored", a["item_id"]))
        # A fresh catalog instance reads persisted data (no process cache).
        from literature_catalog import LiteratureCatalog
        self.assertEqual(LiteratureCatalog(self.project["wiki_root"]).get_item(a["item_id"])["title"], "Human")

    def test_http_security_error_envelope_and_size(self):
        data = {"locator": "10.1234/test", "request_id": "a" * 32}
        for headers in ({"Origin": "http://evil.test"}, {"Host": "evil.test"}, {"X-Literature-Request": "0"}):
            status, value, _ = self.request_http(self.api + "add", data, headers=headers)
            self.assertEqual(status, 403)
            self.assertEqual(value["error"]["code"], "FORBIDDEN")
        for bad in ({**data, "locator": "javascript:alert(1)"}, {**data, "wiki_root": "C:/elsewhere"}):
            self.assertEqual(self.request_http(self.api + "add", bad)[0], 400)
        self.assertEqual(self.request_http(self.api + "add", raw="not json")[0], 400)
        self.assertEqual(self.request_http(self.api + "add", raw='[1,2]')[0], 400)
        self.assertEqual(self.request_http(self.api + "add", raw="", headers={"Content-Length": str(2 * 1024 * 1024 + 1)})[0], 413)
        self.assertEqual(self.catalog.list_items()["count"], 0)
        for action, data in (("item/" + "1" * 32 + "/update", {}), ("item/" + "1" * 32 + "/delete", {}), ("migrate/apply", {}), ("migrate/rollback", {}), ("collection/retry", {})):
            self.assertEqual(self.request_http(self.api + action, data, headers={"Origin": "http://evil.test"})[0], 403)

    def test_conflict_not_other_item_and_legacy_edit_redirect(self):
        a = self.collect()
        self.collect("10.1234/unrelated")
        payload = {"title": "One", "locator": "10.1234/test", "authors": [], "year": 2026, "expected_item_revision": a["item_revision"]}
        path = self.api + f"item/{a['item_id']}/update"
        self.assertEqual(self.request_http(path, payload)[0], 200)
        self.assertEqual(self.request_http(path, payload)[0], 409)
        status, _, headers = self.request_http(a["url"] + "/edit")
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].endswith("?edit=1"))
        self.assertEqual(self.request_http(self.base + "/item/" + "0" * 32)[0], 404)

    def test_list_no_scan_stat_network_and_paging(self):
        # Seed only a temporary catalog; list must not stat any attachments.
        a = self.collect(title="Matching", authors=["Somebody"])
        data = self.catalog._load_catalog()
        import copy
        data["items"] = []
        template = self.item(a)
        for i in range(61):
            item = copy.deepcopy(template)
            item["id"] = f"{i:032x}"
            item["attachments"] = [{"path": f"missing-{i}.pdf"}]
            data["items"].append(item)
        self.catalog._save_catalog(data)
        with patch.object(Path, "rglob", side_effect=AssertionError("scan")), patch("socket.create_connection", side_effect=AssertionError("network")), patch("subprocess.Popen", side_effect=AssertionError("process")):
            body = literature_catalog_list_page(self.home, self.pid, {"q": ["Somebody"]})
        self.assertEqual(body.count('data-item href='), 50)
        self.assertIn("下一页", body)
        self.assertEqual(literature_catalog_list_page(self.home, self.pid, {"page": ["2"]}).count('data-item href='), 11)
        self.assertNotIn(data["items"][0]["created_at"], body)
        self.assertNotIn("migration scan", body)

    def test_missing_and_explicit_reading_links_html_escaping(self):
        source, wiki = Path(self.project["source_root"]), Path(self.project["wiki_root"])
        (source / "p.pdf").write_bytes(b"%PDF-1.0 test")
        (wiki / "n.md").write_text('---\npaper_file: p.pdf\n---\n# Notes', encoding="utf-8")
        a = self.collect(title='<script>alert("x")</script>', paper_file="p.pdf", reading_note_paths=["n.md"], source={"summary": '<img src=x onerror="alert(1)">'})
        body = literature_detail_page(self.home, self.pid, a["item_id"])
        self.assertIn("/literature/read/p.pdf", body)
        self.assertIn("/page/n.md", body)
        self.assertIn("/literature/compare/p.pdf?note=wiki%3An.md", body)
        self.assertNotIn('<script>alert("x")</script>', body)
        self.assertIn("&lt;script&gt;", body)
        (source / "p.pdf").unlink()
        body = literature_detail_page(self.home, self.pid, a["item_id"])
        self.assertIn("原文不可用", body)
        self.assertNotIn("/literature/read/p.pdf", body)
        self.assertIn("/page/n.md", body)

    def test_empty_list_never_imports_files(self):
        source = Path(self.project["source_root"])
        (source / "manual.pdf").write_bytes(b"not a paper")
        body = literature_catalog_list_page(self.home, self.pid)
        self.assertIn("还没有收藏文献", body)
        self.assertNotIn("manual.pdf", body)
        self.assertFalse(self.catalog.catalog_path.exists())

    def test_explicit_import_http_one_read_idempotent_rollback(self):
        source = Path(self.project["source_root"])
        for name in ("paper.pdf", "manual.pdf"):
            (source / name).write_bytes(name.encode())
        before = (source / "paper.pdf").read_bytes()
        status, imported, _ = self.request_http(self.api + "migrate/apply", {"selected_paths": ["paper.pdf"]})
        self.assertEqual(status, 200)
        self.assertEqual(len(imported["imported"]), 1)
        result = apply_migration(self.catalog, source, ["paper.pdf"])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(self.catalog.list_items()["count"], 1)
        self.assertTrue(rollback_migration(self.catalog, imported["migration_id"])["ok"])
        self.assertEqual(self.catalog.list_items()["count"], 0)
        self.assertEqual((source / "paper.pdf").read_bytes(), before)
        self.assertTrue((source / "manual.pdf").exists())
        self.assertFalse(apply_migration(self.catalog, source, ["../outside.pdf"])["ok"])
        with self.assertRaises(LiteratureCatalogError):
            rollback_migration(self.catalog, "../outside")

    def test_strict_compare_no_fuzzy_scan(self):
        from literature_web import _catalog_reading_notes, literature_compare_page
        source, wiki = Path(self.project["source_root"]), Path(self.project["wiki_root"])
        (source / "p.pdf").write_bytes(b"%PDF-test")
        (wiki / "n.md").write_text('---\npaper_file: p.pdf\n---\n# Note', encoding="utf-8")
        self.collect(paper_file="p.pdf", reading_note_paths=["n.md"])
        with patch.object(Path, "rglob", side_effect=AssertionError("scan")):
            self.assertEqual(len(_catalog_reading_notes(self.project, {"path": "p.pdf"})), 1)
        with self.assertRaises(LiteratureCatalogError):
            from literature_catalog import safe_path
            safe_path(source, "C:/elsewhere")
        (wiki / "n.md").write_text('---\npaper_file: other/p.pdf\n---\n# Note', encoding="utf-8")
        from llmwiki_core import LLMWikiError
        with self.assertRaises(LLMWikiError):
            literature_compare_page(self.home, self.pid, "p.pdf", "wiki:n.md")


if __name__ == "__main__":
    unittest.main()
