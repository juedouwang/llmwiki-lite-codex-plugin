"""Deadline/priority/description compatibility; every fixture is a temporary project."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_progress as progress  # noqa: E402
from llmwiki_core import LLMWikiError  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from research_notebook import NotebookConflict  # noqa: E402
from research_records import write_record  # noqa: E402


class ProgressWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="progress-workbench-unit-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = str(self.root / "home")
        source = self.root / "source"
        source.mkdir()
        self.project = register_project(str(source), home=self.home, wiki_root=str(self.root / "wiki"))["project"]
        self.path = Path(self.project["wiki_root"]) / ".research-progress/tasks.json"

    def load(self):
        return progress.load_summary(self.project)

    def create(self, **fields):
        result = progress.update(self.project, {"action": "create", "revision": self.load()["revision"], "task": {"title": "临时任务", **fields}})
        return result["tasks"][-1]

    def update(self, task, fields=None, action="update", **extra):
        result = progress.update(self.project, {"action": action, "revision": self.load()["revision"], "id": task["id"], "task": fields or {}, **extra})
        return next((t for t in result["tasks"] if t["id"] == task["id"]), None)

    def legacy(self, **extra):
        task = self.create(status="blocked", start="2026-09-01", end="2026-09-20", checkpoint="原始停点", next_step="继续验证")
        data = json.loads(self.path.read_bytes())
        raw = data["tasks"][0]
        raw.update(extra)
        raw["assistant_context"] = {"producer": "legacy", "nested": ["原文", {"status": "not-semantic"}]}
        data["assistant_context"] = {"opaque": [1, 2, 3]}
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return task, copy.deepcopy(raw)

    def auto(self, task, **fields):
        source = write_record(self.project["source_root"], state_root=self.project["state_root"], title="上下文", understanding="临时原文")["record"]["id"]
        return progress.write_context(self.project, {"task_id": task["id"], "checkpoint": "助手停点", "next_step": "助手下一步", "source_record_id": source, "base_revision": "", **fields})

    def test_minimal_create_and_ddl_without_start(self):
        task = self.create(description="描述", priority="high", ddl="2026-09-30")
        self.assertEqual((task["status"], task["priority"], task["ddl"]), ("planned", "high", "2026-09-30"))
        self.assertEqual((task["start"], task["end"]), ("", ""))
        self.assertIsNone(task["completed_at"])

    def test_individual_legacy_dates_allowed(self):
        for field in ("start", "end"):
            with self.subTest(field=field):
                task = self.create(**{field: "2026-09-20"})
                self.assertEqual(task["ddl"], "2026-09-20")

    def test_read_only_migration_does_not_rewrite_tasks_or_history(self):
        task, raw = self.legacy()
        before = self.path.read_bytes()
        for _ in range(3):
            value = self.load()["tasks"][0]
            self.assertEqual(value["description"], "原始停点\n\n下一步：继续验证")
            self.assertEqual(value["ddl"], raw["end"])
            self.assertEqual(value["priority"], "medium")
            self.assertEqual(value["history"], raw["history"])
            self.assertEqual(value["assistant_context"], raw["assistant_context"])
            self.assertEqual(value["status"], task["status"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_patch_preserves_every_legacy_field_and_assistant_context(self):
        task, raw = self.legacy(checkpoint="  原始停点  ")
        self.update(task, {"description": "统一新描述", "priority": "high", "ddl": "2026-08-01"})
        data = json.loads(self.path.read_bytes())
        saved = data["tasks"][0]
        for key in ("checkpoint", "next_step", "start", "end", "status", "assistant_context", "context_mode", "created_at", "id"):
            self.assertEqual(saved[key], raw[key], key)
        self.assertEqual(saved["history"][:-1], raw["history"])
        self.assertEqual(data["assistant_context"], {"opaque": [1, 2, 3]})
        self.assertIsNone(saved["completed_at"])

    def test_clear_legacy_deadline_is_explicit_and_keeps_old_interval(self):
        task, raw = self.legacy()
        result = self.update(task, {"ddl": ""})
        self.assertEqual(result["ddl"], "")
        self.assertEqual((result["start"], result["end"]), (raw["start"], raw["end"]))
        self.assertEqual(self.load()["tasks"][0]["ddl"], "")

    def test_clear_automatic_description_remains_empty(self):
        task = self.create(status="active")
        self.auto(task)
        self.assertIn("助手停点", self.load()["tasks"][0]["description"])
        result = self.update(task, {"description": ""})
        self.assertEqual(result["description"], "")
        self.assertEqual(result["description_source"], "manual")
        self.assertEqual(result["effective_context"]["checkpoint"], "助手停点")
        self.assertEqual(self.load()["tasks"][0]["description"], "")

    def test_automatic_context_stays_independent(self):
        task = self.create(status="active", description="用户描述")
        before = self.path.read_bytes()
        self.auto(task)
        current = self.load()["tasks"][0]
        self.assertEqual(current["description"], "用户描述")
        self.assertEqual(current["auto_context"]["checkpoint"], "助手停点")
        self.assertEqual(self.path.read_bytes(), before)
        context_file = self.path.with_name("contexts.json")
        before_context = context_file.read_bytes()
        self.update(task, {"description": "修改描述", "priority": "low"})
        self.assertEqual(context_file.read_bytes(), before_context)

    def test_priority_only_patch_keeps_automatic_description_dynamic(self):
        task = self.create(status="active")
        self.auto(task)
        result = self.update(task, {"priority": "high"})
        self.assertEqual(result["description_source"], "legacy")
        self.assertNotIn("description", json.loads(self.path.read_bytes())["tasks"][0])
        self.assertIn("助手下一步", result["description"])
        self.assertEqual(result["context_mode"], task["context_mode"])

    def test_old_full_client_does_not_erase_new_fields(self):
        task = self.create(description="新描述", priority="high", ddl="2026-10-01")
        old = {k: task[k] for k in progress.FIELDS}
        old["title"] = "旧客户端改名"
        result = self.update(task, old)
        self.assertEqual((result["description"], result["priority"], result["ddl"]), ("新描述", "high", "2026-10-01"))

    def test_markdown_whitespace_preserved(self):
        markdown = "    indented code\n\n![截图](../assets/example.png)\n"
        task = self.create(description=markdown)
        self.assertEqual(task["description"], markdown)
        self.assertEqual(self.update(task, {"priority": "low"})["description"], markdown)

    def test_completion_and_restore_are_explicit_idempotent(self):
        task = self.create(status="blocked", description="待完成")
        edited = self.update(task, {"priority": "high", "ddl": "2020-01-01"})
        self.assertEqual(edited["status"], "blocked")
        done = self.update(task, action="complete")
        self.assertEqual(done["status"], "done")
        self.assertTrue(done["completed_at"])
        again = self.update(task, action="complete")
        self.assertEqual(again["completed_at"], done["completed_at"])
        self.assertEqual(len(again["history"]), len(done["history"]))
        restored = self.update(task, action="restore")
        self.assertEqual(restored["status"], "blocked")
        self.assertIsNone(restored["completed_at"])
        self.assertEqual(restored["description"], "待完成")

    def test_legacy_done_not_backfilled_and_restore_is_todo(self):
        task, _ = self.legacy(status="done", completed_at=None)
        edited = self.update(task, {"priority": "low"})
        self.assertIsNone(edited["completed_at"])
        self.assertEqual(edited["status"], "done")
        restored = self.update(task, action="restore")
        self.assertEqual(restored["status"], "planned")

    def test_delete_conflict_then_delete_keeps_context_and_other_task(self):
        task = self.create(status="active")
        self.auto(task)
        second = self.create(title="另一个任务")
        before = self.path.read_bytes()
        contexts = self.path.with_name("contexts.json").read_bytes()
        with self.assertRaises(NotebookConflict):
            self.update(task, action="delete", revision="stale")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsNone(self.update(task, action="delete"))
        self.assertEqual([t["id"] for t in self.load()["tasks"]], [second["id"]])
        self.assertEqual(self.path.with_name("contexts.json").read_bytes(), contexts)

    def test_cross_project_task_cannot_be_deleted_or_edited(self):
        task = self.create()
        source = self.root / "other"
        source.mkdir()
        other = register_project(str(source), home=self.home, wiki_root=str(self.root / "other-wiki"))["project"]
        before = self.path.read_bytes()
        for action in ("update", "delete", "complete", "restore"):
            with self.subTest(action=action), self.assertRaises(LLMWikiError):
                progress.update(other, {"action": action, "revision": "", "id": task["id"], "task": {"title": "越界"}})
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_new_fields_preserve_file(self):
        task = self.create()
        invalid = [{"priority": p} for p in ("urgent", "", None, 1)] + [{"ddl": d} for d in ("2026-02-30", "20260921", "2026-9-21", None)] + [{"description": d} for d in (None, ["x"], "a\0b", "x" * (progress.MAX_DESCRIPTION + 1))]
        before = self.path.read_bytes()
        for fields in invalid:
            with self.subTest(field=next(iter(fields))), self.assertRaises(LLMWikiError):
                self.update(task, fields)
            self.assertEqual(self.path.read_bytes(), before)

    def test_priority_sort_before_deadline_and_done_excluded(self):
        low = self.create(title="低", priority="low", ddl="2020-01-01")
        high = self.create(title="高", priority="high", ddl="2030-01-01")
        medium = self.create(title="中")
        self.create(title="已完成高优先级", priority="high", status="done")
        self.assertEqual([t["id"] for t in progress.sort_todo(self.load()["tasks"])], [high["id"], medium["id"], low["id"]])

    def test_new_history_includes_description_priority_ddl(self):
        task = self.create(description="原描述", priority="low", ddl="2026-10-01")
        updated = self.update(task, {"description": "新描述", "priority": "high", "ddl": ""})
        self.assertEqual(updated["history"][0]["description"], "原描述")
        self.assertEqual(updated["history"][-1]["description"], "新描述")
        self.assertEqual(updated["history"][-1]["priority"], "high")
        self.assertEqual(updated["history"][-1]["ddl"], "")

    def test_resume_includes_new_planned_but_not_untouched_legacy_ideas(self):
        old = self.create(title="旧想法", status="planned")
        ongoing = self.create(title="进行中的旧任务", status="active")
        new = self.create(title="新模型任务", description="描述", priority="high", ddl="")
        before = self.path.read_bytes()
        selected = progress.active_tasks(self.project)
        self.assertEqual([t["id"] for t in selected], [new["id"], ongoing["id"]])
        self.assertNotIn(old["id"], [t["id"] for t in selected])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(new["status"], "planned")
        # An explicit edit brings an old idea into recent work without changing its status.
        self.update(old, {"priority": "high"})
        self.assertIn(old["id"], [t["id"] for t in progress.active_tasks(self.project)])
        self.assertEqual(self.load()["tasks"][0]["status"], "planned")

    def test_resume_priority_then_recent_human_edit_and_excludes_done(self):
        a = self.create(title="较早", description="a", priority="high")
        b = self.create(title="最近", description="b", priority="high")
        c = self.create(title="低优先级", description="c", priority="low")
        finished = self.create(title="已完成", description="d", priority="high", status="done")
        data = json.loads(self.path.read_bytes())
        for i, task in enumerate(data["tasks"]):
            task["updated_at"] = f"2026-09-21T00:00:0{i}Z"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual([t["id"] for t in progress.active_tasks(self.project)], [b["id"], a["id"], c["id"]])
        self.assertNotIn(finished["id"], [t["id"] for t in progress.active_tasks(self.project)])

    def test_ssr_renders_one_description_without_old_inputs(self):
        self.create(title="SSR 新任务", description="统一描述 <script>bad()</script>", priority="high")
        self.create(title="未操作的旧想法", status="planned")
        before = self.path.read_bytes()
        html = progress.page(self.home, self.project["id"])
        self.assertIn("SSR 新任务", html)
        self.assertIn("统一描述 &lt;script&gt;bad()&lt;/script&gt;", html)
        self.assertNotIn("未操作的旧想法", html)
        for field in ("checkpoint", "next_step", "start", "end", "status"):
            self.assertNotIn(f'name="{field}"', html)
        self.assertEqual(self.path.read_bytes(), before)

    def test_new_task_file_corruption_is_never_overwritten(self):
        task = self.create()
        data = json.loads(self.path.read_bytes())
        data["tasks"][0]["priority"] = "invalid"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(LLMWikiError):
            progress.update(self.project, {"action": "delete", "id": task["id"], "revision": ""})
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
