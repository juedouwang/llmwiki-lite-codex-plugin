"""Shared daily tasks and explicit delivery receipts; all state is disposable."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_tasks as daily  # noqa: E402
import research_progress as progress  # noqa: E402
from llmwiki_core import LLMWikiError  # noqa: E402
from llmwiki_registry import list_projects, register_project  # noqa: E402
from research_notebook import NotebookConflict, safe_file  # noqa: E402
from research_records import list_records, read_record, write_record  # noqa: E402
from research_reports import workspace  # noqa: E402


class DailyTasksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llmwiki-daily-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = str(self.root / "home")
        self.project = self.register("alpha")
        self.pid = self.project["id"]
        self.path = Path(self.project["wiki_root"]) / ".research-progress/tasks.json"
        self.day = "2026-09-22"

    def register(self, name):
        source = self.root / name
        source.mkdir()
        return register_project(str(source), name=name, home=self.home,
                                wiki_root=str(self.root / (name + "-wiki")))["project"]

    def loaded(self, project=None):
        return progress.load_summary(project or self.project)

    def call(self, action, *, actor="user", project=None, revision=None, **fields):
        project = project or self.project
        return daily.mutate(self.home, {"project_id": project["id"], "action": action,
                            "revision": self.loaded(project)["revision"] if revision is None else revision,
                            **fields}, actor=actor)

    def create(self, **fields):
        return self.call("create", task={"title": "测试任务", "scheduled_date": self.day, **fields})["task"]

    def plan_payload(self, **fields):
        return {"project_id": self.pid, "action": "plan", "revision": self.loaded()["revision"],
                "request_id": "plan-one", "task": {"title": "研究目标", "description": "仅规划，不推断结果",
                "start": self.day, "end": "2026-09-24"}, "subtasks": [
                    {"title": "准备输入", "scheduled_date": self.day},
                    {"title": "运行检查", "scheduled_date": "2026-09-23"}], **fields}

    def records(self, project=None):
        project = project or self.project
        return list_records(project.get("source_root", project["wiki_root"]),
                            state_root=project["state_root"], max_records=500)["records"]

    def record_bytes(self, project=None):
        root = Path((project or self.project)["wiki_root"])
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*.md")}

    def test_read_only_defaults_and_exact_owner_ids(self):
        before = set(self.root.rglob("*"))
        result = daily.list_day(self.home, self.day)
        self.assertEqual((result["tasks"], result["overdue"], result["completed"]), ([], [], []))
        self.assertEqual(result["revisions"], {"__workspace__": "", self.pid: ""})
        self.assertEqual(result["projects"], [{"id": self.pid, "name": "alpha"}])
        self.assertEqual(set(self.root.rglob("*")), before)
        for owner in (None, "alpha", self.project["source_root"], " " + self.pid, "missing"):
            with self.subTest(owner=owner), self.assertRaises(LLMWikiError):
                daily.mutate(self.home, {"project_id": owner, "action": "create", "revision": "",
                                        "task": {"title": "x", "scheduled_date": self.day}})

    def test_same_ids_and_bidirectional_patch(self):
        result = daily.mutate(self.home, self.plan_payload(), actor="agent")
        task = result["tasks"][0]
        goal = result["goal"]
        loaded = next(t for t in self.loaded()["tasks"] if t["id"] == task["id"])
        self.assertEqual(loaded["parent_id"], goal["id"])
        self.assertEqual(task["parent_title"], goal["title"])
        old = progress.update(self.project, {"action": "update", "id": task["id"],
            "revision": self.loaded()["revision"], "task": {"title": "旧入口修改", "priority": "high"}})
        day = daily.list_day(self.home, self.day)
        self.assertEqual(day["tasks"][0]["title"], "旧入口修改")
        self.assertEqual(day["tasks"][0]["revision"], old["revision"])
        self.call("update", id=task["id"], task={"description": "每日入口修改"})
        loaded = next(t for t in self.loaded()["tasks"] if t["id"] == task["id"])
        self.assertEqual(loaded["description"], "每日入口修改")
        self.assertEqual(loaded["parent_id"], goal["id"])
        self.assertEqual(len(list(Path(self.project["wiki_root"]).rglob("tasks.json"))), 1)

    def test_estimated_minutes_same_id_patch_and_validation(self):
        payload = self.plan_payload()
        payload["task"]["estimated_minutes"] = 120
        payload["subtasks"][0]["estimated_minutes"] = 60
        plan = daily.mutate(self.home, payload, actor="agent")
        self.assertEqual(plan["goal"]["estimated_minutes"], 120)
        self.assertEqual(plan["goal"]["history"][-1]["estimated_minutes"], 120)
        self.assertIsNone(plan["tasks"][1]["estimated_minutes"])
        task = plan["tasks"][0]
        self.assertEqual(task["estimated_minutes"], 60)
        self.assertEqual(daily.mutate(self.home, payload, actor="agent"), plan)
        old = progress.update(self.project, {"action": "update", "id": task["id"],
            "revision": self.loaded()["revision"], "task": {"title": "旧客户端修改"}})["task"]
        raw = next(t for t in json.loads(self.path.read_bytes())["tasks"] if t["id"] == task["id"])
        loaded = next(t for t in self.loaded()["tasks"] if t["id"] == task["id"])
        aggregated = daily.list_day(self.home, self.day)["tasks"][0]
        for item in (old, raw, loaded, aggregated):
            self.assertEqual(item["id"], task["id"])
            self.assertEqual(item["estimated_minutes"], 60)
            self.assertTrue(all(event["estimated_minutes"] == 60 for event in item["history"]))
        updated = self.call("update", actor="agent", id=task["id"], task={"estimated_minutes": 90})["task"]
        self.assertEqual(updated["estimated_minutes"], 90)
        self.assertEqual(updated["history"][-1]["estimated_minutes"], 90)
        self.assertEqual(updated["history"][0]["estimated_minutes"], 60)
        self.assertIsNone(self.create()["estimated_minutes"])
        for minutes in (None, 1, 1440):
            with self.subTest(valid=minutes):
                created = self.create(estimated_minutes=minutes)
                self.assertEqual(created["estimated_minutes"], minutes)
                self.assertEqual(created["history"][-1]["estimated_minutes"], minutes)
        before = self.path.read_bytes()
        for minutes in (True, False, 0, -1, 1441, 60.0, 1.5, "60", "", [], {}):
            invalid_plan = self.plan_payload(request_id="invalid-estimate")
            invalid_plan["subtasks"][1]["estimated_minutes"] = minutes
            attempts = [
                {"action": "create", "task": {"title": "无效", "scheduled_date": self.day,
                                            "estimated_minutes": minutes}},
                {"action": "update", "id": task["id"], "task": {"estimated_minutes": minutes}},
                invalid_plan,
            ]
            for attempt in attempts:
                with self.subTest(invalid=minutes, action=attempt["action"]):
                    with self.assertRaises(LLMWikiError):
                        daily.mutate(self.home, {"project_id": self.pid,
                                     "revision": self.loaded()["revision"], **attempt}, actor="agent")
                    self.assertEqual(before, self.path.read_bytes())
        cleared = self.call("update", id=task["id"], task={"estimated_minutes": None})["task"]
        self.assertIsNone(cleared["estimated_minutes"])
        self.assertIsNone(cleared["history"][-1]["estimated_minutes"])
        self.assertEqual(cleared["history"][0]["estimated_minutes"], 60)
        self.assertEqual(len(list(Path(self.project["wiki_root"]).rglob("tasks.json"))), 1)

    def test_day_partitions_use_scheduled_date_not_completion_date(self):
        today = self.create()
        overdue = self.create(title="逾期", scheduled_date="2026-09-20")
        done = self.create(title="当日已完成")
        past_done = self.create(title="过去已完成", scheduled_date="2026-09-21")
        self.create(title="未来", scheduled_date="2026-09-24")
        for task in (done, past_done):
            self.call("accept", id=task["id"])
        progress.update(self.project, {"action": "create", "revision": self.loaded()["revision"], "task": {"title": "无日期旧任务"}})
        other = self.register("beta")
        second = self.call("create", project=other, task={"title": "另一项目", "scheduled_date": self.day})["task"]
        result = daily.list_day(self.home, self.day)
        self.assertEqual({t["id"] for t in result["tasks"]}, {today["id"], second["id"]})
        self.assertEqual([t["id"] for t in result["overdue"]], [overdue["id"]])
        self.assertEqual([t["id"] for t in result["completed"]], [done["id"]])
        self.assertEqual(len(result["projects"]), 2)

    def test_plan_retry_is_idempotent_even_with_original_revision(self):
        payload = self.plan_payload()
        result = daily.mutate(self.home, payload, actor="agent")
        before = self.path.read_bytes()
        again = daily.mutate(self.home, payload, actor="agent")
        self.assertEqual(result, again)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(len(self.loaded()["tasks"]), 3)
        changed = deepcopy(payload)
        changed["subtasks"][0]["title"] = "不同请求"
        with self.assertRaises(LLMWikiError):
            daily.mutate(self.home, changed)
        self.assertEqual(before, self.path.read_bytes())

    def test_plan_validates_whole_batch_before_writing(self):
        invalid = [
            [{"title": "有效", "scheduled_date": self.day}, {"title": "无效", "scheduled_date": "2026-02-30"}],
            [{"title": "越界", "scheduled_date": "2026-09-25"}],
            [{"title": "已完成", "scheduled_date": self.day, "status": "done"}],
            [{"title": "伪造验收", "scheduled_date": self.day, "review_state": "accepted"}],
            [{"title": "非法状态", "scheduled_date": self.day, "status": []}],
            [None], [],
            [{"title": "x", "scheduled_date": self.day}] * 101,
        ]
        for subtasks in invalid:
            with self.subTest(subtasks=subtasks[:2]), self.assertRaises(LLMWikiError):
                daily.mutate(self.home, self.plan_payload(subtasks=subtasks))
            self.assertFalse(self.path.exists())
            self.assertEqual(self.records(), [])
        with patch.object(progress, "MAX_TASKS", 2), self.assertRaises(LLMWikiError):
            daily.mutate(self.home, self.plan_payload())
        self.assertFalse(self.path.exists())

    def test_append_to_existing_goal_and_strict_parent_store(self):
        plan = daily.mutate(self.home, self.plan_payload())
        goal = plan["goal"]
        appended = self.call("plan", parent_id=goal["id"], request_id="append-one",
                             subtasks=[{"title": "追加", "scheduled_date": "2026-09-24"}])
        self.assertEqual(appended["goal"]["id"], goal["id"])
        self.assertEqual(len(self.loaded()["tasks"]), 4)
        ordinary = self.create()
        foreign = self.register("foreign")
        for parent in ("a" * 32, ordinary["id"], foreign["id"]):
            with self.subTest(parent=parent), self.assertRaises(LLMWikiError):
                self.create(parent_id=parent)
        with self.assertRaises(LLMWikiError):
            self.call("create", project=foreign, task={"title": "跨库", "scheduled_date": self.day, "parent_id": goal["id"]})
        before = self.path.read_bytes()
        with self.assertRaises(LLMWikiError):
            self.call("delete", id=goal["id"])
        with self.assertRaises(LLMWikiError):
            self.call("update", id=goal["id"], task={"end": self.day})
        self.assertEqual(before, self.path.read_bytes())

    def test_agent_cannot_bypass_acceptance_in_either_entry(self):
        task = self.create()
        base = {"id": task["id"], "revision": self.loaded()["revision"]}
        attempts = [{"action": action} for action in ("accept", "complete", "restore")]
        attempts += [{"action": "update", "task": {"status": "done"}},
                     {"action": "create", "task": {"title": "伪造", "status": "done", "scheduled_date": self.day}},
                     {"action": "import", "ids": [], "completed": [task["id"]]}]
        for key, value in (("review_state", "pending"), ("delivery_summary", "伪造摘要"),
                           ("completion_record_id", "records/fake.md"), ("acceptance_record_id", "records/fake.md"),
                           ("completed_at", "2026-09-22T00:00:00Z"), ("completion_cycle", 8),
                           ("delivery_receipt", {}), ("acceptance_receipt", {})):
            attempts.append({"action": "update", "task": {key: value}})
        before = self.path.read_bytes()
        for attempt in attempts:
            payload = {**base, **attempt, "actor": "user"}
            with self.subTest(payload=payload), self.assertRaises(LLMWikiError):
                progress.update(self.project, payload, actor="agent")
            if payload["action"] != "import":
                with self.assertRaises(LLMWikiError):
                    daily.mutate(self.home, {**payload, "project_id": self.pid}, actor="agent")
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(self.records(), [])

    def test_submit_accept_real_records_and_retry_do_not_rewrite(self):
        task = self.create()
        payload = {"project_id": self.pid, "action": "submit", "id": task["id"],
                   "revision": self.loaded()["revision"], "summary": "完成输入整理；尚未运行实验，不声称结果。"}
        submitted = daily.mutate(self.home, payload, actor="agent")["task"]
        self.assertEqual((submitted["status"], submitted["review_state"]), ("planned", "pending"))
        self.assertEqual(len(self.records()), 1)
        record = read_record(self.project["source_root"], submitted["completion_record_id"], state_root=self.project["state_root"])["record"]
        self.assertIn(payload["summary"], record["content"])
        self.assertIn("不验证或推断科研结论", record["content"])
        self.assertIn(submitted["completion_record_id"], [r["id"] for r in progress.load_records(self.project)["records"]])
        before, notes = self.path.read_bytes(), self.record_bytes()
        daily.mutate(self.home, payload, actor="agent")
        self.call("submit", id=task["id"], actor="agent", summary=payload["summary"])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(notes, self.record_bytes())
        accept_payload = {"project_id": self.pid, "action": "accept", "id": task["id"], "revision": self.loaded()["revision"]}
        accepted = daily.mutate(self.home, accept_payload)["task"]
        self.assertEqual((accepted["status"], accepted["review_state"]), ("done", "accepted"))
        self.assertEqual(accepted["completion_record_id"], submitted["completion_record_id"])
        self.assertNotEqual(accepted["acceptance_record_id"], submitted["completion_record_id"])
        self.assertEqual(len(self.records()), 2)
        before, notes = self.path.read_bytes(), self.record_bytes()
        daily.mutate(self.home, accept_payload)
        self.call("accept", id=task["id"])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(notes, self.record_bytes())

    def test_submit_validation_and_live_status(self):
        task = self.create(status="active")
        before = self.path.read_bytes()
        for summary in ("", "   ", None, [], "x" * (progress.MAX_DESCRIPTION + 1)):
            with self.subTest(summary=str(summary)[:20]), self.assertRaises(LLMWikiError):
                self.call("submit", id=task["id"], summary=summary)
        self.assertEqual(before, self.path.read_bytes())
        submitted = self.call("submit", id=task["id"], summary="用户自行提交", actor="user")["task"]
        self.assertEqual(submitted["status"], "active")
        self.assertEqual(submitted["completion_record"]["actor"], "user")
        with self.assertRaises(LLMWikiError):
            self.call("submit", id=task["id"], summary="修订交付内容")
        self.assertEqual(len(self.records()), 1)
        rejected = self.call("reject", id=task["id"], reason="缺少跨场景验证")["task"]
        self.assertEqual((rejected["status"], rejected["review_state"]), ("active", "rejected"))
        self.assertEqual(len(self.records()), 2)
        self.call("update", id=task["id"], task={"status": "blocked"})
        self.assertEqual(self.call("submit", id=task["id"], summary="新的交付")["task"]["status"], "active")

    def test_reject_requires_user_and_keeps_each_delivery_cycle(self):
        task = self.create(status="active")
        first = self.call("submit", id=task["id"], summary="第一次交付", actor="agent")["task"]
        original = first["completion_record_id"]
        for actor, reason in (("agent", "不行"), ("user", "   ")):
            with self.assertRaises(LLMWikiError):
                self.call("reject", id=task["id"], reason=reason, actor=actor)
        revision = self.loaded()["revision"]
        payload = {"project_id": self.pid, "action": "reject", "id": task["id"],
                   "revision": revision, "reason": "需补上失败样本"}
        rejected = daily.mutate(self.home, payload)["task"]
        self.assertEqual((rejected["status"], rejected["review_state"]), ("active", "rejected"))
        self.assertEqual(rejected["completion_record_id"], original)
        self.assertTrue(rejected["rejection_record_id"])
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(daily.mutate(self.home, payload)["task"]["rejection_record_id"], rejected["rejection_record_id"])
        self.assertEqual(len(self.records()), 2)
        second = self.call("submit", id=task["id"], summary="补上失败样本", actor="agent")["task"]
        self.assertEqual(second["review_state"], "pending")
        self.assertNotEqual(second["completion_record_id"], original)
        accepted = self.call("accept", id=task["id"])["task"]
        self.assertEqual((accepted["status"], accepted["review_state"]), ("done", "accepted"))
        self.assertEqual(len(self.records()), 4)
        self.assertEqual(len(accepted["history"]), 5)
        self.assertEqual(accepted["history"][-1]["review_state"], "accepted")

    def test_legacy_complete_and_patch_done_use_shared_receipts(self):
        original = write_record(self.project["source_root"], "人工原记录", "用户写的内容", state_root=self.project["state_root"])["record"]["id"]
        task = self.create(record_id=original)
        self.call("submit", id=task["id"], summary="交付待验收", actor="agent")
        result = progress.update(self.project, {"action": "complete", "id": task["id"], "revision": self.loaded()["revision"]})
        accepted = result["tasks"][0]
        self.assertEqual(accepted["record_id"], original)
        self.assertEqual(accepted["review_state"], "accepted")
        self.assertEqual(daily.list_day(self.home, self.day)["completed"][0]["id"], task["id"])
        direct = self.create(title="普通任务")
        done = progress.update(self.project, {"action": "update", "id": direct["id"], "revision": self.loaded()["revision"], "task": {"status": "done"}})["task"]
        self.assertEqual(done["completion_record_id"], done["acceptance_record_id"])
        self.assertIn("用户明确确认", done["completion_record"]["summary"])
        self.assertEqual(len(self.records()), 4)

    def test_restore_preserves_history_and_records_but_new_cycle_gets_new_receipt(self):
        task = self.create(status="blocked")
        accepted = self.call("accept", id=task["id"])["task"]
        notes = self.record_bytes()
        restored = self.call("restore", id=task["id"])["task"]
        self.assertEqual(restored["status"], "blocked")
        self.assertEqual(restored["review_state"], "none")
        self.assertIsNone(restored["completed_at"])
        self.assertEqual(restored["completion_record_id"], accepted["completion_record_id"])
        self.assertEqual(restored["history"][:-1], accepted["history"])
        self.assertEqual(notes, self.record_bytes())
        again = self.call("accept", id=task["id"])["task"]
        self.assertNotEqual(again["completion_record_id"], accepted["completion_record_id"])
        self.assertEqual(len(self.records()), 2)
        with self.assertRaises(LLMWikiError):
            self.call("update", id=task["id"], task={"status": "active"}, actor="agent")
        patched = self.call("update", id=task["id"], task={"status": "active"})["task"]
        self.assertEqual(patched["review_state"], "none")
        self.assertEqual(patched["completion_record_id"], again["completion_record_id"])

    def test_workspace_without_any_registered_project_has_readable_receipts(self):
        home = str(self.root / "empty-home")
        owner = workspace(home)
        initial = daily.list_day(home, self.day)
        self.assertEqual(initial["projects"], [])
        self.assertFalse((Path(home) / "workspace").exists())
        create = {"project_id": "__workspace__", "action": "create", "revision": "", "task": {"title": "临时事项", "scheduled_date": self.day}}
        created = daily.mutate(home, create)
        task = created["task"]
        self.assertEqual(task["project_id"], "__workspace__")
        accepted = daily.mutate(home, {"project_id": "__workspace__", "action": "accept", "revision": created["revision"], "id": task["id"]})["task"]
        self.assertIn("用户明确确认", accepted["completion_record"]["summary"])
        record = read_record(owner["wiki_root"], accepted["completion_record_id"], state_root=owner["state_root"])["record"]
        self.assertIn(task["id"], record["content"])
        self.assertEqual(len(self.records(owner)), 1)
        self.assertEqual(list_projects(home)["projects"], [])
        self.assertEqual(daily.list_day(home, self.day)["completed"][0]["id"], task["id"])
        self.assertEqual(len(progress.load(owner)["records"]), 1)
        self.assertTrue((Path(owner["wiki_root"]) / ".research-progress/tasks.json").exists())

    def test_legacy_read_and_patch_preserve_new_fields_and_opaque_metadata(self):
        task = self.create()
        data = json.loads(self.path.read_bytes())
        raw = data["tasks"][0]
        for key in progress.DAILY_FIELDS:
            raw.pop(key, None)
            for event in raw["history"]:
                event.pop(key, None)
        raw["assistant_context"] = {"opaque": [1, 2]}
        data["extra"] = {"keep": True}
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = self.path.read_bytes()
        legacy = self.loaded()["tasks"][0]
        self.assertEqual(legacy["scheduled_date"], "")
        self.assertEqual(legacy["review_state"], "none")
        self.assertEqual(daily.list_day(self.home, self.day)["tasks"], [])
        self.assertEqual(before, self.path.read_bytes())
        self.call("update", id=task["id"], task={"scheduled_date": self.day})
        pending = self.call("submit", id=task["id"], summary="真实用户摘要")["task"]
        patch_fields = {key: pending[key] for key in progress.FIELDS}
        patch_fields["title"] = "旧客户端"
        updated = progress.update(self.project, {"action": "update", "id": task["id"], "revision": self.loaded()["revision"], "task": patch_fields})["task"]
        for key in ("scheduled_date", "review_state", "delivery_summary", "completion_record_id"):
            self.assertEqual(updated[key], pending[key])
        self.assertEqual(updated["assistant_context"], raw["assistant_context"])
        self.assertEqual(json.loads(self.path.read_bytes())["extra"], {"keep": True})

    def test_dates_parent_updates_and_corrupt_store_fail_closed(self):
        for day in ("20260922", "2026-9-22", "2026-02-30", "2026-09-22T00:00:00", "", None, 1, "２０２６-０９-２２"):
            with self.subTest(day=day), self.assertRaises(LLMWikiError):
                self.create(scheduled_date=day)
            if day is not None:
                with self.assertRaises(LLMWikiError):
                    daily.list_day(self.home, day)
        task = self.create()
        before = self.path.read_bytes()
        with self.assertRaises(LLMWikiError):
            self.call("update", id=task["id"], task={"scheduled_date": ""})
        self.assertEqual(before, self.path.read_bytes())
        self.path.write_text("broken", encoding="utf-8")
        with self.assertRaises(LLMWikiError):
            daily.mutate(self.home, {"action": "accept", "project_id": self.pid, "id": task["id"], "revision": ""})
        self.assertEqual(self.path.read_text(encoding="utf-8"), "broken")

    def test_revision_conflicts_and_concurrent_plan_retry(self):
        def create_once(_):
            try:
                return self.call("create", revision="", task={"title": "并发", "scheduled_date": self.day})["ok"]
            except NotebookConflict:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(create_once, range(2))), [False, True])
        payload = self.plan_payload()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: daily.mutate(self.home, payload, actor="agent"), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.loaded()["tasks"]), 4)
        with self.assertRaises(NotebookConflict):
            self.call("update", revision=payload["revision"], id=results[0]["tasks"][0]["id"], task={"title": "过期修改"})
        with self.assertRaises(NotebookConflict):
            self.call("submit", revision="stale", id="missing", summary="x")

    @unittest.skipUnless(os.name == "nt", "Windows extended path syntax")
    def test_safe_file_windows_extended_prefix_containment(self):
        relative = ".notebook-history/research-progress/.lock"
        for plain, extended in ((Path(r"C:\wiki"), Path(r"\\?\C:\wiki")),
                                (Path(r"\\server\share\wiki"), Path(r"\\?\UNC\server\share\wiki"))):
            for root, resolved in ((plain, extended), (extended, plain),
                                   (plain, plain), (extended, extended)):
                with self.subTest(root=root, resolved=resolved):
                    # Deterministic reproduction of resolve() keeping the prefix
                    # on just one side during concurrent mkdir/lock creation.
                    # Mock stat probes too: the UNC cases must never use a network.
                    with patch.object(Path, "resolve", side_effect=[root, resolved / relative]), \
                            patch.object(Path, "is_symlink", return_value=False), \
                            patch.object(Path, "exists", return_value=False):
                        self.assertEqual(safe_file(plain, relative), root / relative)

    @unittest.skipUnless(os.name == "nt", "Windows extended path syntax")
    def test_safe_file_windows_extended_prefix_escape_rejected(self):
        cases = ((Path(r"C:\wiki"), Path(r"\\?\C:\wiki-other\file")),
                 (Path(r"\\?\C:\wiki"), Path(r"C:\outside\file")),
                 (Path(r"C:\wiki"), Path(r"\\?\D:\wiki\file")),
                 (Path(r"\\server\share\wiki"), Path(r"\\?\UNC\server\other\wiki\file")),
                 (Path(r"\\?\UNC\server\share\wiki"), Path(r"\\server\share\outside\file")))
        for root, resolved in cases:
            with self.subTest(root=root, resolved=resolved):
                with patch.object(Path, "resolve", side_effect=[root, resolved]), \
                        patch.object(Path, "is_symlink", return_value=False), \
                        patch.object(Path, "exists", return_value=False), \
                        self.assertRaises((LLMWikiError, ValueError)):
                    safe_file(root, "link/file")

    @unittest.skipUnless(os.name == "nt", "Windows reparse points")
    def test_safe_file_windows_prefix_keeps_link_rejection(self):
        root = Path(r"C:\wiki")
        for symlink in (True, False):
            with self.subTest(symlink=symlink):
                # Even an in-root link/junction is forbidden, not just escapes.
                metadata = Mock(st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
                with patch.object(Path, "resolve", side_effect=[root, Path(r"\\?\C:\wiki\real\file")]), \
                        patch.object(Path, "is_symlink", return_value=symlink), \
                        patch.object(Path, "exists", return_value=True), \
                        patch.object(Path, "lstat", return_value=metadata), \
                        self.assertRaises(LLMWikiError):
                    safe_file(root, "link/file")

    def test_safe_file_rejects_parent_escape(self):
        for relative in ("../outside/file", str(self.root / "outside" / "file")):
            with self.subTest(relative=relative), self.assertRaises((LLMWikiError, ValueError)):
                safe_file(Path(self.project["wiki_root"]), relative)

    def test_cross_process_writers_share_lock_and_revision(self):
        script = """
import json, sys
sys.path.insert(0, sys.argv[1])
import daily_tasks
from llmwiki_core import LLMWikiError
sys.stdin.readline()
try:
    result = daily_tasks.mutate(sys.argv[2], json.loads(sys.argv[3]))
    print(json.dumps({"ok": result["ok"]}))
except LLMWikiError:
    print(json.dumps({"ok": False}))
"""
        payload = {"project_id": self.pid, "action": "create", "revision": "",
                   "task": {"title": "process", "scheduled_date": self.day}}
        command = [sys.executable, "-B", "-c", script, str(Path(progress.__file__).parent),
                   self.home, json.dumps(payload)]
        children = [subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) for _ in range(2)]
        try:
            for child in children:
                child.stdin.write("go\n")
                child.stdin.flush()
            results = []
            for child in children:
                stdout, stderr = child.communicate(timeout=30)
                self.assertEqual(child.returncode, 0, stderr)
                results.append(json.loads(stdout)["ok"])
            self.assertEqual(sorted(results), [False, True])
            self.assertEqual(len(self.loaded()["tasks"]), 1)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait()

    def test_preflight_capacity_failure_does_not_append_receipts(self):
        task = self.create()
        before = self.path.read_bytes()
        with patch.object(progress, "MAX_BYTES", len(before) + 100), self.assertRaises(LLMWikiError):
            self.call("submit", id=task["id"], summary="合法摘要")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.records(), [])

    def test_record_is_reused_after_interrupted_task_commit(self):
        task = self.create()
        original_atomic = progress._atomic

        def fail_tasks(path, raw):
            if path == self.path:
                raise OSError("simulated task replace failure")
            return original_atomic(path, raw)

        for action, fields in (("submit", {"summary": "交付已提交"}), ("accept", {})):
            with self.subTest(action=action):
                before = self.path.read_bytes()
                count = len(self.records())
                with patch.object(progress, "_atomic", side_effect=fail_tasks), self.assertRaises(OSError):
                    self.call(action, id=task["id"], **fields)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(len(self.records()), count + 1)
                notes = self.record_bytes()
                self.call(action, id=task["id"], **fields)
                self.assertEqual(self.record_bytes(), notes)
                self.assertEqual(len(self.records()), count + 1)

    def test_record_append_then_writer_error_is_idempotent_on_retry(self):
        task = self.create()
        original = progress.write_record

        def append_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("simulated failure after Markdown append")

        before = self.path.read_bytes()
        with patch.object(progress, "write_record", side_effect=append_then_fail), self.assertRaises(OSError):
            self.call("submit", id=task["id"], summary="可恢复提交")
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(len(self.records()), 1)
        result = self.call("submit", id=task["id"], summary="可恢复提交")["task"]
        self.assertEqual(result["completion_record_id"], self.records()[0]["id"])
        self.assertEqual(len(self.records()), 1)

    def test_delete_retains_record_and_plan_retry_never_resurrects_deleted_task(self):
        plan = daily.mutate(self.home, self.plan_payload())
        task = plan["tasks"][0]
        self.call("accept", id=task["id"])
        notes = self.record_bytes()
        self.call("delete", id=task["id"])
        self.assertEqual(notes, self.record_bytes())
        with self.assertRaises(LLMWikiError):
            daily.mutate(self.home, self.plan_payload(revision=""))
        self.assertEqual(len(self.loaded()["tasks"]), 2)


if __name__ == "__main__":
    unittest.main()
