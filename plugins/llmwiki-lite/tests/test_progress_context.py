"""Automatic context ingest, field ownership, MCP and lightweight reads."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mcp_server  # noqa: E402
import research_progress as progress  # noqa: E402
from llmwiki_core import LLMWikiError  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from research_notebook import save as notebook_save  # noqa: E402
from research_records import write_record  # noqa: E402
from web_server import create_server  # noqa: E402


class ProgressContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llmwiki-progress-ctx-")
        self.root = Path(self.tmp.name)
        (self.root / "source").mkdir()
        self.home = str(self.root / "home")
        self._old_home = os.environ.get("LLMWIKI_HOME")
        os.environ["LLMWIKI_HOME"] = self.home
        self.project = register_project(str(self.root / "source"), home=self.home, wiki_root=str(self.root / "wiki"))["project"]

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("LLMWIKI_HOME", None)
        else:
            os.environ["LLMWIKI_HOME"] = self._old_home
        self.tmp.cleanup()

    def create(self, **fields):
        return progress.update(self.project, {"action": "create", "revision": "", "task": {"title": "夜间实验", "status": "active", **fields}})

    def record(self):
        return write_record(self.project["source_root"], state_root=self.project["state_root"], title="讨论", understanding="原文", next_steps=["补数据"])["record"]["id"]

    def write_auto(self, task_id, checkpoint="自动停点", next_step="自动下一步", source=None, base=""):
        return progress.write_context(self.project, {
            "task_id": task_id,
            "checkpoint": checkpoint,
            "next_step": next_step,
            "source_record_id": source or self.record(),
            "base_revision": base,
        })

    def test_manual_override_survives_auto_update(self):
        created = self.create(checkpoint="人工停点", next_step="人工下一步")
        task = created["tasks"][0]
        self.assertEqual(task["context_mode"]["checkpoint"], "manual")
        self.assertEqual(task["context_mode"]["next_step"], "manual")
        revision = created["revision"]
        auto = self.write_auto(task["id"])
        summary = progress.load_summary(self.project)["tasks"][0]
        self.assertEqual(summary["effective_context"]["checkpoint"], "人工停点")
        self.assertEqual(summary["effective_context"]["next_step"], "人工下一步")
        self.assertEqual(summary["auto_context"]["checkpoint"], "自动停点")
        self.assertEqual(progress.load_summary(self.project)["revision"], revision)
        self.assertEqual(summary["history"], task["history"])
        self.assertTrue(auto["changed"])

    def test_explicit_empty_is_manual_until_use_auto(self):
        created = self.create()
        task = created["tasks"][0]
        cleared = progress.update(self.project, {"action": "update", "revision": created["revision"],
                                                 "id": task["id"], "task": dict(task, next_step="", context_mode={"checkpoint": "auto", "next_step": "manual"})})
        self.assertEqual(cleared["tasks"][0]["context_mode"]["next_step"], "manual")
        auto = self.write_auto(task["id"], next_step="新的自动下一步")
        summary = progress.load_summary(self.project)["tasks"][0]
        self.assertEqual(summary["effective_context"]["next_step"], "")
        self.assertEqual(summary["auto_context"]["next_step"], "新的自动下一步")
        restored = progress.update(self.project, {"action": "update", "revision": cleared["revision"],
                                                  "id": task["id"], "task": dict(summary, next_step=summary["next_step"], context_mode={"checkpoint": "auto", "next_step": "auto"})})
        self.assertEqual(restored["tasks"][0]["effective_context"]["next_step"], "新的自动下一步")
        self.assertTrue(auto["changed"])

    def test_title_only_edit_does_not_claim_auto_fields(self):
        created = self.create()
        task = created["tasks"][0]
        self.write_auto(task["id"], checkpoint="自动停点", next_step="自动下一步")
        renamed = progress.update(self.project, {"action": "update", "revision": created["revision"],
                                                 "id": task["id"], "task": dict(task, title="只改标题")})
        item = renamed["tasks"][0]
        self.assertEqual(item["title"], "只改标题")
        self.assertEqual(item["context_mode"]["checkpoint"], "auto")
        self.assertEqual(item["effective_context"]["checkpoint"], "自动停点")

    def test_rejects_unknown_fields_and_cross_project_source(self):
        created = self.create()
        task = created["tasks"][0]
        source = self.record()
        with self.assertRaises(LLMWikiError):
            progress.write_context(self.project, {"task_id": task["id"], "checkpoint": "a", "next_step": "b",
                                                  "source_record_id": source, "base_revision": "", "status": "done"})
        (self.root / "other-source").mkdir()
        other = register_project(str(self.root / "other-source"), home=self.home, wiki_root=str(self.root / "other-wiki"))["project"]
        other_record = write_record(other["source_root"], state_root=other["state_root"], title="别的项目", understanding="不能借用")["record"]["id"]
        with self.assertRaises(LLMWikiError):
            progress.write_context(self.project, {"task_id": task["id"], "checkpoint": "a", "next_step": "b",
                                                  "source_record_id": other_record, "base_revision": ""})
        # A task id from another project must not be writable through this project.
        other_task = progress.update(other, {"action": "create", "revision": "", "task": {"title": "别处任务", "status": "active"}})["tasks"][0]
        with self.assertRaises(LLMWikiError):
            progress.write_context(self.project, {"task_id": other_task["id"], "checkpoint": "a", "next_step": "b",
                                                  "source_record_id": source, "base_revision": ""})
        # An automatic client must name its project: a blank value must not fall back
        # to whichever project happens to be selected and write there.
        for blank in ("", "   ", None):
            with self.assertRaises(LLMWikiError):
                progress.mcp_context_write(
                    project_root=blank,
                    state_root=self.project["state_root"],
                    task_id=task["id"],
                    checkpoint="串项目",
                    next_step="串项目",
                    source_record_id=source,
                    base_revision="",
                )
        missing = progress.load_summary(self.project)["tasks"][0]
        self.assertIsNone(missing["auto_context"])
        self.assertEqual(progress.load_summary(other)["tasks"][0].get("context_revision"), "")

    def test_idempotent_and_stale_revision(self):
        created = self.create()
        task = created["tasks"][0]
        source = self.record()
        first = self.write_auto(task["id"], source=source)
        same = self.write_auto(task["id"], source=source, base=first["context_revision"])
        self.assertFalse(same["changed"])
        self.assertEqual(same["generated_at"], first["generated_at"])
        newer = self.write_auto(task["id"], checkpoint="第二版", next_step="新下一步", source=source, base=first["context_revision"])
        with self.assertRaises(LLMWikiError):
            self.write_auto(task["id"], checkpoint="旧结果", next_step="旧下一步", source=source, base=first["context_revision"])
        current = progress.load_summary(self.project)["tasks"][0]
        self.assertEqual(current["auto_context"]["checkpoint"], "第二版")
        self.assertEqual(current["context_revision"], newer["context_revision"])

    def test_mcp_schema_and_dispatch(self):
        names = {item["name"] for item in mcp_server.TOOLS}
        self.assertIn("llmwiki_progress_get", names)
        self.assertIn("llmwiki_progress_context_write", names)
        created = self.create()
        task = created["tasks"][0]
        source = self.record()
        listed = mcp_server.dispatch("llmwiki_progress_get", {"project_root": self.project["source_root"]})
        self.assertEqual(listed["tasks"][0]["id"], task["id"])
        self.assertFalse(listed["truncated"])
        written = mcp_server.dispatch("llmwiki_progress_context_write", {
            "project_root": self.project["source_root"],
            "task_id": task["id"],
            "checkpoint": "MCP 停点",
            "next_step": "MCP 下一步",
            "source_record_id": source,
            "base_revision": "",
        })
        self.assertTrue(written["changed"])
        one = mcp_server.dispatch("llmwiki_progress_get", {"project_root": self.project["source_root"], "task_id": task["id"]})
        self.assertEqual(one["task"]["effective_context"]["checkpoint"], "MCP 停点")
        with self.assertRaises(LLMWikiError):
            mcp_server.dispatch("llmwiki_progress_context_write", {
                "project_root": self.project["source_root"],
                "task_id": task["id"],
                "checkpoint": "x",
                "next_step": "y",
                "source_record_id": source,
                "base_revision": "",
                "status": "done",
            })

    def test_source_edit_and_real_hook_do_not_change_tasks(self):
        # A-03: exercise the actual Hook subprocess, not a mocked event writer.
        created = self.create(start_date="2026-09-19", end_date="2026-09-20")
        for status in ("done", "blocked"):
            created = progress.update(self.project, {
                "action": "create", "revision": created["revision"],
                "task": {"title": f"任务 {status}", "status": status},
            })
        self.write_auto(created["tasks"][0]["id"])
        data_dir = Path(self.project["wiki_root"]) / ".research-progress"
        before = {name: (data_dir / name).read_bytes() for name in ("tasks.json", "contexts.json")}
        summary = progress.load_summary(self.project)
        source = Path(self.project["source_root"])
        changed = source / "experiment.py"
        changed.write_text("# completed/status/done in a path or source is not task state\n", encoding="utf-8")
        state = Path(self.project["state_root"])
        self.assertTrue((state / "config.json").is_file())
        event_file = state / "events.jsonl"
        old_lines = event_file.read_text(encoding="utf-8").splitlines() if event_file.exists() else []
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(Path(__file__).resolve().parents[1] / "scripts/record_change.py")],
            input=json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                              "cwd": str(source), "tool_input": {"file_path": str(changed)}}),
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            env={**os.environ, "LLMWIKI_HOME": self.home},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        new_lines = event_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(new_lines[:len(old_lines)], old_lines)
        self.assertEqual(len(new_lines), len(old_lines) + 1)
        event = json.loads(new_lines[-1])
        self.assertEqual(set(event), {"timestamp", "kind", "tool", "paths"})
        self.assertEqual(event["kind"], "file-change-hint")
        self.assertEqual(event["tool"], "Edit")
        self.assertEqual(event["paths"], ["experiment.py"])
        self.assertEqual(progress.load_summary(self.project), summary)
        for name, content in before.items():
            self.assertEqual((data_dir / name).read_bytes(), content)

    def test_summary_does_not_scan_records(self):
        created = self.create()
        task = created["tasks"][0]
        self.write_auto(task["id"])
        with patch("research_progress._all_records", side_effect=AssertionError("scanned records")):
            summary = progress.load_summary(self.project)
            home = progress.active_tasks(self.project)
        self.assertEqual(summary["tasks"][0]["id"], task["id"])
        self.assertEqual(home[0]["id"], task["id"])
        records = progress.load_records(self.project)
        self.assertIn("records", records)

    def test_corrupt_auto_file_does_not_block_manual_tasks(self):
        created = self.create()
        path = self.root / "wiki/.research-progress/contexts.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text("broken", encoding="utf-8")
        summary = progress.load_summary(self.project)
        self.assertTrue(summary["warning"])
        renamed = progress.update(self.project, {"action": "update", "revision": created["revision"],
                                                 "id": created["tasks"][0]["id"], "task": dict(created["tasks"][0], title="仍可保存")})
        self.assertEqual(renamed["tasks"][0]["title"], "仍可保存")
        self.assertEqual(path.read_text(), "broken")

    def test_http_summary_and_records_views(self):
        created = self.create()
        task = created["tasks"][0]
        self.write_auto(task["id"])
        notebook_save(self.project, "b" * 32, {"revision": "", "document": {"title": "笔记", "tags": [],
                      "blocks": [{"id": "b1", "type": "markdown", "text": "内容", "comments": []}]}})
        server = create_server(self.home, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        host = f"127.0.0.1:{server.server_port}"
        path = f"/api/project/{self.project['id']}/progress"
        conn = HTTPConnection(host)
        try:
            conn.request("GET", path + "?view=summary")
            summary = json.loads(conn.getresponse().read())
            self.assertNotIn("records", summary)
            self.assertEqual(summary["tasks"][0]["effective_context"]["checkpoint"], "自动停点")
            conn.request("GET", path + "?view=records")
            records = json.loads(conn.getresponse().read())
            self.assertIn("records", records)
            conn.request("GET", path)
            full = json.loads(conn.getresponse().read())
            self.assertIn("candidates", full)
        finally:
            conn.close()
            server.shutdown()
            server.server_close()
            worker.join()

    def test_project_list_links_top_task_without_nesting(self):
        created = self.create(title="列表入口任务")
        progress.update(self.project, {"action": "create", "revision": created["revision"],
                                       "task": {"title": "未开始", "status": "planned"}})
        server = create_server(self.home, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            conn = HTTPConnection(f"127.0.0.1:{server.server_port}")
            conn.request("GET", "/projects")
            body = conn.getresponse().read().decode("utf-8")
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
        self.assertIn("列表入口任务", body)
        self.assertIn("project-row-task", body)
        self.assertNotIn("<a class=\"project-row\"", body)
        self.assertNotIn("未开始", body)

    def test_performance_budget_is_measured(self):
        samples = []
        for index in range(5):
            source = self.root / f"perf-source-{index}"
            wiki = self.root / f"perf-wiki-{index}"
            source.mkdir()
            project = register_project(str(source), home=self.home, wiki_root=str(wiki), name=f"perf-{index}")["project"]
            now = "2026-09-19T00:00:00Z"
            tasks = []
            for item in range(100):
                tid = f"{index:02d}{item:02d}".ljust(32, "a")
                tasks.append({
                    "id": tid, "title": f"样本 {index}-{item}", "status": "active" if item < 3 else "planned",
                    "start": "", "end": "", "checkpoint": "", "next_step": "", "record_id": "",
                    "created_at": now, "updated_at": now, "completed_at": None,
                    "context_mode": {"checkpoint": "auto", "next_step": "auto"},
                    "history": [{"at": now, "title": f"样本 {index}-{item}", "status": "planned" if item >= 3 else "active",
                                 "start": "", "end": "", "checkpoint": "", "next_step": "", "record_id": ""}],
                })
            path = wiki / ".research-progress" / "tasks.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"version": 1, "tasks": tasks}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            samples.append(project)
        progress.load_summary(samples[0])
        times = []
        for _ in range(20):
            start = time.perf_counter()
            progress.load_summary(samples[0])
            times.append((time.perf_counter() - start) * 1000)
        times.sort()
        p95 = times[int(0.95 * (len(times) - 1))]
        latest = progress.load_summary(samples[0])
        task = latest["tasks"][0]
        saves = []
        for index in range(20):
            start = time.perf_counter()
            latest = progress.update(samples[0], {"action": "update", "revision": latest["revision"],
                                                  "id": task["id"], "task": {**{k: task[k] for k in ("title", "status", "start", "end", "checkpoint", "next_step", "record_id")}, "title": f"保存 {index}"}})
            task = latest["tasks"][0]
            saves.append((time.perf_counter() - start) * 1000)
        saves.sort()
        save_p95 = saves[int(0.95 * (len(saves) - 1))]
        print(f"progress summary P95={p95:.1f}ms save P95={save_p95:.1f}ms projects=5 tasks=100 machine=win32")
        self.assertLess(p95, 500)
        self.assertLess(save_p95, 500)

    def test_manual_paths_do_not_call_a_generator(self):
        called = []
        def fake(*args, **kwargs):
            called.append(1)
            time.sleep(30)
            raise AssertionError("generator called")
        with patch("research_progress.write_context", side_effect=fake):
            start = time.perf_counter()
            created = self.create()
            progress.load_summary(self.project)
            progress.active_tasks(self.project)
            progress.update(self.project, {"action": "update", "revision": created["revision"],
                                           "id": created["tasks"][0]["id"], "task": dict(created["tasks"][0], status="blocked")})
            self.assertLess(time.perf_counter() - start, 2)
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
