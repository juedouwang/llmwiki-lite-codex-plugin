"""Research progress persistence, legacy migration and conflict regressions."""
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_progress as progress  # noqa: E402
from llmwiki_core import LLMWikiError  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from research_notebook import NotebookConflict, save as notebook_save  # noqa: E402
from research_records import write_record  # noqa: E402
from web_server import create_server  # noqa: E402


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llmwiki-progress-")
        self.root = Path(self.tmp.name)
        (self.root / "source").mkdir()
        self.home = str(self.root / "home")
        self.project = register_project(str(self.root / "source"), home=self.home, wiki_root=str(self.root / "wiki"))["project"]

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, **fields):
        return progress.update(self.project, {"action": "create", "revision": "", "task": {"title": "夜间实验", "status": "active", **fields}})

    def test_persistence_context_dates_and_history(self):
        result = self.create(start="2026-09-18", end="2026-09-20", checkpoint="基线已跑完", next_step="检查误差分布")
        task = result["tasks"][0]
        self.assertEqual(progress.load(self.project)["tasks"][0]["next_step"], "检查误差分布")
        task["status"] = "blocked"
        task["checkpoint"] = "缺夜间数据"
        new = progress.update(self.project, {"action": "update", "revision": result["revision"], "id": task["id"], "task": task})
        self.assertEqual(new["tasks"][0]["created_at"], task["created_at"])
        self.assertEqual(len(new["tasks"][0]["history"]), 2)
        self.assertEqual(new["tasks"][0]["history"][0]["checkpoint"], "基线已跑完")

    def test_no_fabricated_dates_and_read_does_not_write(self):
        self.assertEqual(progress.load(self.project)["tasks"], [])
        self.assertFalse((self.root / "wiki/.research-progress").exists())
        task = self.create()["tasks"][0]
        self.assertEqual(task["start"], "")
        self.assertEqual(task["end"], "")
        self.assertIsNone(task["completed_at"])
        self.assertNotIn("percent", task)

    def test_same_task_survives_across_days_without_copying(self):
        created = self.create(title="验证夜间数据", status="planned")
        task = created["tasks"][0]
        path = self.root / "wiki/.research-progress/tasks.json"
        before = path.read_bytes()
        progress.load_summary(self.project)
        self.assertEqual(path.read_bytes(), before)
        updated = progress.update(self.project, {"action": "update", "revision": created["revision"],
                                                 "id": task["id"], "task": dict(task, status="active")})
        again = progress.load_summary(self.project)["tasks"][0]
        self.assertEqual(len(updated["tasks"]), 1)
        self.assertEqual(again["id"], task["id"])
        self.assertEqual(again["title"], "验证夜间数据")
        self.assertEqual(again["status"], "active")

    def test_complete_reopen_and_unknown_completed_at(self):
        created = self.create(status="active")
        task = created["tasks"][0]
        done = progress.update(self.project, {"action": "update", "revision": created["revision"],
                                              "id": task["id"], "task": dict(task, status="done", title="完成一次")})
        first = done["tasks"][0]["completed_at"]
        self.assertTrue(first)
        titled = progress.update(self.project, {"action": "update", "revision": done["revision"],
                                                "id": task["id"], "task": dict(done["tasks"][0], title="改名仍完成")})
        self.assertEqual(titled["tasks"][0]["completed_at"], first)
        reopened = progress.update(self.project, {"action": "update", "revision": titled["revision"],
                                                  "id": task["id"], "task": dict(titled["tasks"][0], status="active")})
        self.assertIsNone(reopened["tasks"][0]["completed_at"])
        self.assertEqual(reopened["tasks"][0]["status"], "active")
        again = progress.update(self.project, {"action": "update", "revision": reopened["revision"],
                                               "id": task["id"], "task": dict(reopened["tasks"][0], status="done")})
        self.assertEqual(len(again["tasks"]), 1)
        self.assertNotEqual(again["tasks"][0]["completed_at"], first)
        self.assertEqual(again["tasks"][0]["history"][1]["status"], "done")
        self.assertEqual(again["tasks"][0]["history"][1]["completed_at"], first)
        path = self.root / "wiki/.research-progress/tasks.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["tasks"][0]["status"] = "done"
        data["tasks"][0]["completed_at"] = None
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        loaded = progress.load_summary(self.project)["tasks"][0]
        self.assertEqual(loaded["status"], "done")
        self.assertIsNone(loaded["completed_at"])

    def test_readonly_old_file_bytes_unchanged(self):
        payload = {
            "version": 1,
            "tasks": [{
                "id": "a" * 32,
                "title": "旧任务",
                "status": "done",
                "start": "",
                "end": "",
                "checkpoint": "手写停点",
                "next_step": "",
                "record_id": "",
                "created_at": "2026-09-01T00:00:00Z",
                "updated_at": "2026-09-01T00:00:00Z",
                "history": [{"at": "2026-09-01T00:00:00Z", "title": "旧任务", "status": "done",
                             "start": "", "end": "", "checkpoint": "手写停点", "next_step": "", "record_id": ""}],
            }],
        }
        path = self.root / "wiki/.research-progress/tasks.json"
        path.parent.mkdir()
        raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
        path.write_bytes(raw)
        loaded = progress.load_summary(self.project)["tasks"][0]
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(loaded["context_mode"]["checkpoint"], "manual")
        self.assertEqual(loaded["context_mode"]["next_step"], "auto")
        self.assertEqual(loaded["effective_context"]["checkpoint"], "手写停点")
        self.assertIsNone(loaded["completed_at"])

    def note(self, note_id="a" * 32, title="阶段成果：配准误差 0.046"):
        return notebook_save(
            self.project,
            note_id,
            {"revision": "", "document": {"title": title, "tags": ["实验"],
                                          "blocks": [{"id": "b1", "type": "markdown", "text": "改进 0.046 / 基线 0.082", "comments": []}]}},
        )["path"]

    def test_task_links_to_a_note_and_the_link_survives_a_reload_and_an_edit(self):
        """M-01: 阶段成果用笔记记录，任务可以关联该笔记 —— 且编辑任务不会把关联弄丢。"""
        record_id = self.note()
        result = self.create(record_id=record_id, status="active")
        task = result["tasks"][0]
        self.assertEqual(task["record_id"], record_id)
        self.assertEqual(progress.load(self.project)["tasks"][0]["record_id"], record_id)

        # The bug this guards: editing unrelated fields used to drop the link,
        # because record_id was not part of the fields written back.
        edited = dict(task, checkpoint="夜间数据还没验证")
        after = progress.update(self.project, {"action": "update", "revision": result["revision"], "id": task["id"], "task": edited})
        self.assertEqual(after["tasks"][0]["record_id"], record_id)
        self.assertEqual(after["tasks"][0]["checkpoint"], "夜间数据还没验证")
        self.assertEqual(progress.load(self.project)["tasks"][0]["record_id"], record_id)

    def test_link_change_is_kept_in_history_and_can_be_cleared(self):
        record_id = self.note()
        created = self.create(record_id=record_id)
        task = created["tasks"][0]
        cleared = progress.update(self.project, {"action": "update", "revision": created["revision"],
                                                "id": task["id"], "task": dict(task, record_id="")})
        self.assertEqual(cleared["tasks"][0]["record_id"], "")
        # The first history snapshot keeps the link, so a wrong link can be traced back.
        self.assertEqual(cleared["tasks"][0]["history"][0]["record_id"], record_id)

    def test_link_must_point_at_an_existing_record_under_records(self):
        before = progress.load(self.project)["tasks"]
        for bad in ("records/manual/" + "b" * 32 + ".md", "records/secret.md", "../../etc/passwd", "records/../../x.md"):
            with self.subTest(bad=bad):
                with self.assertRaises(LLMWikiError):
                    self.create(record_id=bad)
        self.assertEqual(progress.load(self.project)["tasks"], before)

    def test_load_offers_notes_as_link_targets(self):
        record_id = self.note(title="阶段成果：零点标定")
        targets = {item["id"]: item["title"] for item in progress.load(self.project)["records"]}
        self.assertEqual(targets.get(record_id), "阶段成果：零点标定")

    def test_legacy_migration_explicit_idempotent_and_preserves_source(self):
        write_record(self.project["source_root"], state_root=self.project["state_root"], title="讨论", understanding="原文", next_steps=["补数据", "跑消融"])
        files = {p: p.read_bytes() for p in (self.root / "wiki/records").rglob("*.md")}
        result = progress.load(self.project)
        self.assertEqual(result["tasks"], [])
        ids = [c["id"] for c in result["candidates"]]
        imported = progress.update(self.project, {"action": "import", "revision": "", "ids": ids, "completed": ids[:1]})
        self.assertEqual(imported["tasks"][0]["status"], "done")
        self.assertEqual(imported["tasks"][1]["status"], "planned")
        same = progress.update(self.project, {"action": "import", "revision": imported["revision"], "ids": ids})
        self.assertEqual(len(same["tasks"]), 2)
        self.assertEqual(progress.load(self.project)["candidates"], [])
        for path, raw in files.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_validation(self):
        for fields in ({"start": "2026-02-30", "end": "2026-03-01"}, {"start": "2026-09-20", "end": "2026-09-18"}, {"start": "2026-09-18"}, {"title": ""}, {"status": "70%"}):
            with self.assertRaises(LLMWikiError):
                self.create(**fields)

    def test_concurrent_writes_and_stale_revision(self):
        def attempt():
            try:
                self.create()
                return True
            except NotebookConflict:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(len(progress.load(self.project)["tasks"]), 1)

    def test_corrupt_file_never_overwritten(self):
        self.create()
        path = self.root / "wiki/.research-progress/tasks.json"
        path.write_text("broken", encoding="utf-8")
        with self.assertRaises(LLMWikiError):
            self.create()
        self.assertEqual(path.read_text(), "broken")

    def test_atomic_write_failure_preserves_file(self):
        original = self.create()
        path = self.root / "wiki/.research-progress/tasks.json"
        raw = path.read_bytes()
        with patch("research_notebook.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                progress.update(self.project, {"action": "create", "revision": original["revision"], "task": {"title": "第二个任务", "status": "planned"}})
        self.assertEqual(path.read_bytes(), raw)

    def test_symlink_escape(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "wiki/.research-progress"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink privilege unavailable")
        with self.assertRaises((LLMWikiError, ValueError)):
            self.create()
        self.assertEqual(list(outside.iterdir()), [])

    def test_http_security_and_json_roundtrip(self):
        server = create_server(self.home, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        host = f"127.0.0.1:{server.server_port}"
        path = f"/api/project/{self.project['id']}/progress"
        conn = HTTPConnection(host)
        payload = json.dumps({"revision": "", "action": "create", "task": {"title": "<script>alert(1)</script>", "status": "planned"}})
        headers = {"Content-Type": "application/json", "Origin": "http://" + host, "X-Notebook-Request": "1"}
        try:
            conn.request("POST", path, payload, {**headers, "Origin": "https://evil.example"})
            response = conn.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            conn.request("POST", path, payload, headers)
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            data = json.loads(response.read())
            self.assertEqual(data["tasks"][0]["title"], "<script>alert(1)</script>")
            conn.request("GET", path)
            response = conn.getresponse()
            self.assertEqual(json.loads(response.read())["revision"], data["revision"])
            conn.request("POST", path, payload, headers)
            response = conn.getresponse()
            self.assertEqual(response.status, 409)
            response.read()
        finally:
            conn.close()
            server.shutdown()
            server.server_close()
            worker.join()
    def test_progress_page_shows_where_you_left_off_before_javascript(self):
        """M-01 / A-02: 次日打开项目就知道上次做到哪、下一步做什么，并且能点回去。"""
        record_id = self.note()
        created = self.create(record_id=record_id, status="active",
                              checkpoint="基线已跑完，夜间数据没验证", next_step="检查误差分布")
        task = created["tasks"][0]
        progress.update(self.project, {"action": "create", "revision": created["revision"],
                                       "task": {"title": "还没开始的想法", "status": "planned"}})

        server = create_server(self.home, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            conn = HTTPConnection(f"127.0.0.1:{server.server_port}")
            conn.request("GET", f"/project/{self.project['id']}/todos")
            response = conn.getresponse()
            body = response.read().decode("utf-8")
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

        self.assertEqual(response.status, 200)
        self.assertIn("继续上次", body)
        self.assertIn("基线已跑完，夜间数据没验证", body)
        self.assertIn("检查误差分布", body)
        # Click-through back to the task itself, and to the note the task is about.
        self.assertIn(f"/todos#task-{task['id']}", body)
        self.assertIn(record_id, body)
        # A planned task is not "where you left off".
        self.assertNotIn("还没开始的想法", body)

    def test_knowledge_page_does_not_repeat_task_progress(self):
        self.create(title="以后再做", status="active")
        server = create_server(self.home, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            conn = HTTPConnection(f"127.0.0.1:{server.server_port}")
            conn.request("GET", f"/project/{self.project['id']}")
            response = conn.getresponse()
            body = response.read().decode("utf-8")
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
        self.assertEqual(response.status, 200)
        self.assertNotIn("<h2>继续上次</h2>", body)
        self.assertNotIn("以后再做", body)


if __name__ == "__main__":
    unittest.main()
