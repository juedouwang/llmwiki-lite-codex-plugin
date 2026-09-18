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
from research_notebook import NotebookConflict  # noqa: E402
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
        self.assertNotIn("percent", task)

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


if __name__ == "__main__":
    unittest.main()
