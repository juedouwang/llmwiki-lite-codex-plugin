"""All fixtures live in a temporary registered project, never real research data."""

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import knowledge_maintenance as km  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from web_server import create_server  # noqa: E402


class KnowledgeFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="llmwiki-knowledge-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = str(self.root / "home")
        self.source = self.root / "source"
        self.source.mkdir()
        self.p = register_project(
            str(self.source),
            home=self.home,
            wiki_root=str(self.root / "wiki"),
            state_root=str(self.root / "state"),
        )["project"]
        self.wiki = Path(self.p["wiki_root"])
        (self.source / "main.py").write_text(
            "def compute():\n    return 42\n", encoding="utf-8"
        )

    def plan(self):
        return km.knowledge_plan("manual", self.p["id"], home=self.home)["run_id"]

    def read(self, run, view="changes", **kw):
        items, cursor = [], None
        while True:
            data = km.knowledge_sources(run, view, cursor=cursor, home=self.home, **kw)
            items.extend(data["items"])
            cursor = data["next_cursor"]
            if cursor is None:
                return items

    def action(
        self,
        run,
        mode="create",
        relative="knowledge/compute.md",
        text="# 计算\n返回 42。",
    ):
        items = self.read(run)
        self.read(run, "catalog")
        base = None
        if mode != "create":
            base = self.read(run, "page", page_path=relative)[0]["base_sha256"]
        i = next(i for i in items if i["locator"] == "source:main.py")
        return {
            "action_id": uuid4().hex,
            "mode": mode,
            "page_path": relative,
            "base_sha256": base,
            "content": text,
            "reason": "根据项目函数定义。",
            "evidence_refs": [
                {k: i[k] for k in ("source_id", "locator", "revision")}
                | {"excerpt": "return 42"}
            ],
        }

    def finish(self, run, actions=None):
        ids = list({i["source_id"] for i in self.read(run)})
        return km.knowledge_finish(run, "reviewed", ids, actions or [], home=self.home)

    def proposal(self):
        target = self.wiki / "architecture.md"
        target.write_bytes(b"# Architecture\r\nSync.  \r\n")
        run = self.plan()
        action = self.action(
            run, "replace", "architecture.md", "# Architecture\nAsync."
        )
        self.finish(run, [action])
        return km.proposal_detail(self.p, action["action_id"])

    def decide(self, p, action):
        return km.decide(
            self.p,
            p["id"],
            {
                "action": action,
                "expected_base_sha256": p["base_sha256"],
                "expected_proposal_sha256": p["proposal_sha256"],
            },
        )


class KnowledgeTests(KnowledgeFixture):
    def test_read_without_state_is_side_effect_free(self):
        self.assertEqual(km.maintenance_status(self.p, self.home)["pending_count"], 0)
        self.assertFalse(km.path(self.p, "state.json").exists())
        with self.assertRaises(Exception):
            km.knowledge_plan("manual", "", home=self.home)

    def test_create_finish_idempotent_and_independent_baseline(self):
        manifest = Path(self.p["state_root"]) / "manifest.json"
        original = manifest.read_bytes() if manifest.exists() else None
        run = self.plan()
        action = self.action(run)
        result = self.finish(run, [action])
        self.assertEqual(result["created"], 1)
        content = (self.wiki / "knowledge/compute.md").read_bytes()
        self.assertEqual(km.knowledge_finish(run, "reviewed", home=self.home), result)
        self.assertEqual((self.wiki / "knowledge/compute.md").read_bytes(), content)
        self.assertIsNone(self.plan())
        self.assertEqual(manifest.read_bytes() if manifest.exists() else None, original)

    def test_append_preserves_raw_prefix_and_index_outside_region(self):
        target = self.wiki / "architecture.md"
        before = b"# Original\r\nTrailing  \r\n"
        target.write_bytes(before)
        index = self.wiki / "index.md"
        prefix = b"# HUMAN  \r\n"
        suffix = b"\r\nUSER FOOTER  "
        index.write_bytes(
            prefix
            + b"<!-- llmwiki:index:start -->old<!-- llmwiki:index:end -->"
            + suffix
        )
        run = self.plan()
        action = self.action(run, "append", "architecture.md", "补充。")
        self.assertEqual(self.finish(run, [action])["appended"], 1)
        self.assertTrue(target.read_bytes().startswith(before))
        self.assertTrue(index.read_bytes().startswith(prefix))
        self.assertTrue(index.read_bytes().endswith(suffix))

    def test_replace_requires_confirmation_keep_idempotent(self):
        p = self.proposal()
        original = (self.wiki / "architecture.md").read_bytes()
        self.assertEqual(p["status"], "pending")
        self.assertIsNone(km.state(self.p)["last_updated_at"])
        first = self.decide(p, "keep")
        self.assertEqual(self.decide(p, "keep"), first)
        self.assertEqual((self.wiki / "architecture.md").read_bytes(), original)
        self.assertEqual(km.proposal_list(self.p)["total"], 0)

    def test_accept_only_selected_and_repeat(self):
        p = self.proposal()
        first = self.decide(p, "accept")
        self.assertEqual(self.decide(p, "accept"), first)
        self.assertEqual(
            (self.wiki / "architecture.md").read_text(encoding="utf-8"),
            p["proposed_text"],
        )

    def test_page_stale_get_readonly_keep_still_allowed(self):
        p = self.proposal()
        (self.wiki / "architecture.md").write_text("My note", encoding="utf-8")
        detail = km.proposal_detail(self.p, p["id"])
        self.assertEqual(detail["effective_status"], "stale")
        self.assertEqual(km._proposal(self.p, p["id"])["status"], "pending")
        with self.assertRaises(km.KnowledgeError) as c:
            self.decide(p, "accept")
        self.assertEqual(c.exception.code, "STALE_PAGE")
        self.decide(p, "keep")
        self.assertEqual((self.wiki / "architecture.md").read_text(), "My note")

    def test_evidence_stale_refuses_apply_and_recheck(self):
        p = self.proposal()
        (self.source / "main.py").write_text("return 0", encoding="utf-8")
        with self.assertRaises(km.KnowledgeError) as c:
            self.decide(p, "accept")
        self.assertEqual(c.exception.code, "STALE_EVIDENCE")
        self.assertIn("architecture.md", km.state(self.p)["recheck_pages"])
        self.assertIsNotNone(self.plan())

    def test_rejected_same_evidence_cannot_change_wording(self):
        p = self.proposal()
        self.decide(p, "keep")
        # An unrelated new source permits a run but must not lift the rejected key.
        (self.source / "other.py").write_text("unrelated", encoding="utf-8")
        run = self.plan()
        self.read(run)
        self.read(run, "catalog")
        context = self.read(run, "source", locator="source:main.py")[0]
        page = self.read(run, "page", page_path="architecture.md")[0]
        action = {
            "action_id": uuid4().hex,
            "mode": "replace",
            "page_path": "architecture.md",
            "base_sha256": page["base_sha256"],
            "content": "Changed wording",
            "reason": "same evidence",
            "evidence_refs": [
                {k: context[k] for k in ("source_id", "locator", "revision")}
                | {"excerpt": "return 42"}
            ],
        }
        self.assertEqual(self.finish(run, [action])["rejected_suppressed"], 1)
        self.assertEqual(km.proposal_list(self.p)["total"], 0)

    def test_source_and_output_exclusion(self):
        (self.source / ".env").write_text("API_KEY=private", encoding="utf-8")
        report = self.wiki / "records/reports/daily-x/draft.md"
        report.parent.mkdir(parents=True)
        report.write_text("not evidence", encoding="utf-8")
        record = self.wiki / "records/manual/note.md"
        record.parent.mkdir(parents=True)
        record.write_text("# Notes\nUseful evidence", encoding="utf-8")
        (self.wiki / "old.md").write_text("not evidence", encoding="utf-8")
        run = self.plan()
        locators = {i["locator"] for i in self.read(run)}
        self.assertIn("record:records/manual/note.md", locators)
        self.assertEqual(locators, {"source:main.py", "record:records/manual/note.md"})

    def test_deleted_not_repeated_and_failed_not_advanced(self):
        run = self.plan()
        km.knowledge_finish(run, "failed", home=self.home)
        self.assertFalse(km.state(self.p)["sources"])
        run = self.plan()
        self.finish(run)
        (self.source / "main.py").unlink()
        run = self.plan()
        items = self.read(run)
        self.assertEqual(items[0]["revision"], "deleted")
        self.assertIn("return 42", items[0]["text"])
        self.finish(run)
        self.assertIsNone(self.plan())

    def test_batch_and_pagination_limits(self):
        (self.source / "main.py").write_text("x" * 60000, encoding="utf-8")
        for i in range(25):
            (self.source / f"a{i:02}.py").write_text("x", encoding="utf-8")
        run = self.plan()
        self.assertEqual(len(self.read(run)), 20)
        self.assertEqual(self.finish(run)["remaining"], 6)
        run = self.plan()
        response = km.knowledge_sources(run, "changes", home=self.home)
        self.assertLessEqual(sum(len(i["text"]) for i in response["items"]), 20000)
        while all(i["complete"] for i in response["items"]):
            response = km.knowledge_sources(
                run, "changes", cursor=response["next_cursor"], home=self.home
            )
        with self.assertRaises(km.KnowledgeError):
            km.knowledge_finish(
                run,
                "reviewed",
                [next(i["source_id"] for i in response["items"] if not i["complete"])],
                home=self.home,
            )
        self.finish(run)
        self.assertIsNone(self.plan())

    def test_forged_source_rejects_whole_submission(self):
        run = self.plan()
        action = self.action(run)
        action["evidence_refs"][0]["excerpt"] = "not in source"
        with self.assertRaises(km.KnowledgeError):
            self.finish(run, [action])
        self.assertFalse((self.wiki / "knowledge/compute.md").exists())
        self.assertFalse(km.state(self.p)["sources"])

    def test_forbidden_paths(self):
        for target in [
            "../x.md",
            "records/x.md",
            "literature/x.md",
            "papers/x.md",
            ".private/x.md",
            "index.md",
        ]:
            with self.subTest(target=target), self.assertRaises(Exception):
                km.page_target(self.p, target)
        note = self.wiki / "reading.md"
        note.write_text("---\npaper_file: paper.pdf\n---", encoding="utf-8")
        with self.assertRaises(km.KnowledgeError):
            km.page_target(self.p, "reading.md")

    def test_append_crash_recovery_never_duplicates(self):
        target = self.wiki / "architecture.md"
        target.write_bytes(b"# Original\r\n")
        run = self.plan()
        action = self.action(run, "append", "architecture.md", "Added")
        with patch.object(km, "_update_index", side_effect=OSError("disk")):
            self.assertEqual(self.finish(run, [action])["failed"], 1)
        after = target.read_bytes()
        self.assertEqual(self.finish(run, [action])["appended"], 1)
        self.assertEqual(target.read_bytes(), after)

    def test_mcp_protocol_zero_action_review(self):
        from mcp_server import dispatch
        run = dispatch("llmwiki_knowledge_plan", {"trigger": "manual", "project_id": self.p["id"], "home": self.home})
        page = dispatch("llmwiki_knowledge_sources", {"run_id": run["run_id"], "view": "changes", "home": self.home})
        result = dispatch("llmwiki_knowledge_finish", {"run_id": run["run_id"], "outcome": "reviewed", "reviewed_source_ids": [i["source_id"] for i in page["items"]], "actions": [], "home": self.home})
        self.assertEqual(result["created"], 0)
        self.assertIsNone(km.state(self.p)["last_updated_at"])

    def test_one_stale_page_keeps_independent_success(self):
        target = self.wiki / "architecture.md"
        target.write_text("# Original", encoding="utf-8")
        run = self.plan()
        stale = self.action(run, "append", "architecture.md", "Addition")
        good = self.action(run, "create", "knowledge/compute.md", "# Compute\nReturns 42.")
        target.write_text("# Human edit", encoding="utf-8")
        result = self.finish(run, [stale, good])
        self.assertEqual((result["stale"], result["created"]), (1, 1))
        self.assertEqual(target.read_text(encoding="utf-8"), "# Human edit")
        self.assertTrue((self.wiki / "knowledge/compute.md").exists())
        self.assertNotIn("source:main.py", km.state(self.p)["sources"])

    def test_expired_intent_recovers_without_duplicate_append(self):
        target = self.wiki / "architecture.md"
        target.write_bytes(b"# Original\r\n")
        run = self.plan()
        action = self.action(run, "append", "architecture.md", "Added")
        with patch.object(km, "_update_index", side_effect=OSError("disk")):
            self.assertEqual(self.finish(run, [action])["failed"], 1)
        after = target.read_bytes()
        frozen = km.read_json(km.path(self.p, f"runs/{run}.json"))
        frozen["expires_at"] = "2020-01-01T00:00:00+00:00"
        km.write_json(km.path(self.p, f"runs/{run}.json"), frozen)
        self.assertIsNone(self.plan())
        self.assertEqual(target.read_bytes(), after)
        self.assertEqual(
            km.state(self.p)["proposal_index"][action["action_id"]]["status"], "applied"
        )

    def test_stale_append_requeues_unchanged_evidence(self):
        target = self.wiki / "architecture.md"
        target.write_text("# Old", encoding="utf-8")
        run = self.plan()
        action = self.action(run, "append", "architecture.md", "Addition")
        target.write_text("# Human edit", encoding="utf-8")
        self.assertEqual(self.finish(run, [action])["stale"], 1)
        saved = km.state(self.p)
        self.assertIn(
            "source:main.py", saved["page_sources"]["architecture.md"]["locators"]
        )
        # A prior successful source check must not suppress the explicit page recheck.
        source = km.source_item(self.p, "source:main.py")
        saved["sources"]["source:main.py"] = {
            "checked_revision": source["revision"],
            "text": source["text"],
        }
        km.save_state(self.p, saved)
        self.assertIsNotNone(self.plan())

    def test_deleted_daily_entry_is_evidence_not_deleted_whole_file(self):
        from research_records import write_record

        kw = {
            "project_root": str(self.source),
            "state_root": self.p["state_root"],
            "recorded_at": "2026-09-19T00:00:00Z",
        }
        first = write_record(title="First", understanding="one", **kw)["record"]
        write_record(title="Second", understanding="two", **kw)
        run = self.plan()
        self.finish(run)
        locator = "record:" + first["id"]
        path = self.wiki / first["path"]
        raw = path.read_text(encoding="utf-8")
        # Remove the first complete entry, preserving a valid daily file and the second entry.
        start, end = raw.index("## 00:00｜First"), raw.index("## 00:00｜Second")
        path.write_text(raw[:start] + raw[end:], encoding="utf-8")
        items, _ = km.scan(self.p, km.state(self.p)["sources"])
        self.assertEqual(items[locator]["revision"], "deleted")
        self.assertTrue(km._evidence_current(self.p, [items[locator]]))

    def test_concurrent_accept_is_idempotent(self):
        from concurrent.futures import ThreadPoolExecutor

        proposal = self.proposal()
        payload = {
            "action": "accept",
            "expected_base_sha256": proposal["base_sha256"],
            "expected_proposal_sha256": proposal["proposal_sha256"],
        }
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(lambda _: km.decide(self.p, proposal["id"], payload), range(2))
            )
        self.assertEqual(results[0]["status"], results[1]["status"])
        self.assertEqual(
            (self.wiki / "architecture.md").read_text(encoding="utf-8"),
            "# Architecture\nAsync.",
        )

    def test_sanitized_comparison(self):
        p = self.proposal()
        self.assertNotIn("<script", p["base_html"])
        self.assertIn("Architecture", p["proposed_html"])


class KnowledgeHTTPTests(KnowledgeFixture):
    def test_routes_sameorigin_page_and_cross_project(self):
        p = self.proposal()
        server = create_server(self.home, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        route = f"/api/project/{self.p['id']}/knowledge-maintenance"
        with urlopen(base + route) as r:
            self.assertEqual(json.load(r)["pending_count"], 1)
        with urlopen(base + f"/project/{self.p['id']}") as r:
            self.assertIn("km-pending", r.read().decode())
        payload = json.dumps(
            {
                "action": "keep",
                "expected_base_sha256": p["base_sha256"],
                "expected_proposal_sha256": p["proposal_sha256"],
            }
        ).encode()
        with self.assertRaises(HTTPError) as c:
            urlopen(
                Request(
                    base + route + "/proposals/" + p["id"],
                    payload,
                    {"Content-Type": "application/json"},
                )
            )
        self.assertEqual(c.exception.code, 403)
        other_source = self.root / "other-source"
        other_source.mkdir()
        other = register_project(str(other_source), home=self.home)["project"]
        with self.assertRaises(HTTPError) as cross:
            urlopen(base + f"/api/project/{other['id']}/knowledge-maintenance/proposals/{p['id']}")
        self.assertEqual(cross.exception.code, 404)
        headers = {
            "Content-Type": "application/json",
            "Origin": base,
            "X-Notebook-Request": "1",
        }
        with urlopen(
            Request(base + route + "/proposals/" + p["id"], payload, headers)
        ) as r:
            self.assertEqual(json.load(r)["status"], "rejected")
        with patch.object(km, "scan", side_effect=AssertionError("must not scan")):
            with urlopen(base + route) as r:
                self.assertEqual(json.load(r)["pending_count"], 0)


if __name__ == "__main__":
    unittest.main()
