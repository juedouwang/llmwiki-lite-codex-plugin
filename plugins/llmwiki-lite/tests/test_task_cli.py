"""One-shot CLI contracts, executable documentation, and isolated backend checks."""

from __future__ import annotations

import contextlib
import copy
import importlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
SCRIPTS = PLUGIN / "scripts"
CLI = SCRIPTS / "task_cli.py"
SKILL = PLUGIN / "skills/llmwiki-task-planning/SKILL.md"
sys.path.insert(0, str(SCRIPTS))
import task_cli as cli  # noqa: E402
from llmwiki_core import LLMWikiError  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from research_notebook import NotebookConflict  # noqa: E402


def invoke(argv, stdin=""):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch.object(sys, "stdin", io.StringIO(stdin)):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def documents():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    section = readme.split("## 任务规划 Skill 与一次性 CLI\n", 1)[1].split("\n## ", 1)[0]
    return {"skill": SKILL.read_text(encoding="utf-8"), "readme": section}


def examples():
    return [(name, json.loads(block)) for name, text in documents().items()
            for block in re.findall(r"```json\s*\n(.*?)\n```", text, re.S)]


def substitute(value, replacements):
    if isinstance(value, dict):
        return {key: substitute(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, replacements) for item in value]
    return replacements.get(value, value) if isinstance(value, str) else value


class CLIContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llmwiki-task-cli-")
        self.addCleanup(self.tmp.cleanup)
        self.home = str(Path(self.tmp.name) / "home")
        self.project = {"id": "project-exact-id", "name": "中文项目", "source_root": self.tmp.name}
        self.day = date.today().isoformat()
        self.snapshot = {
            "ok": True, "date": self.day,
            "tasks": [{"id": "t", "project_id": self.project["id"], "project_name": "中文项目",
                       "parent_title": "父目标", "revision": "r1"}],
            "overdue": [{"id": "old", "project_id": "__workspace__", "project_name": "工作台",
                         "parent_title": "", "revision": "w1"}],
            "completed": [{"id": "done", "project_id": self.project["id"], "project_name": "中文项目",
                           "parent_title": "父目标", "revision": "r1"}],
            "revisions": {self.project["id"]: "r1", "__workspace__": "w1"},
        }
        self.backend = types.ModuleType("daily_tasks")
        self.backend.list_day = Mock(return_value=self.snapshot)
        self.backend.mutate = Mock(return_value={"ok": True, "revision": "r2"})
        backend_patch = patch.dict(sys.modules, {"daily_tasks": self.backend})
        backend_patch.start()
        self.addCleanup(backend_patch.stop)
        registry_patch = patch.object(cli, "list_projects", return_value={"ok": True, "projects": [self.project]})
        self.registry = registry_patch.start()
        self.addCleanup(registry_patch.stop)
        self.payload = {"project_id": self.project["id"], "action": "create", "revision": "r1",
                        "task": {"title": "验证中文输入", "scheduled_date": self.day}}

    def write(self, payload, **kwargs):
        text = json.dumps(payload, ensure_ascii=False)
        return invoke(["--home", self.home, "write", "--args-file", "-"], kwargs.get("text", text))

    def assert_error(self, result, code=2):
        actual, stdout, stderr = result
        self.assertEqual(actual, code, stderr)
        self.assertEqual(stdout, "")
        error = json.loads(stderr)
        self.assertFalse(error["ok"])
        self.assertNotIn("Traceback", stderr)
        return error

    def replacements(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        return {"<HOME>": self.home, "<PROJECT_ID>": self.project["id"], "<LOCAL_TODAY>": self.day,
                "<PLAN_START>": self.day, "<PLAN_END>": tomorrow, "<DAY_1>": self.day, "<DAY_2>": tomorrow,
                "<REVISION>": "r1", "<TASK_ID>": "existing-task", "<REQUEST_ID>": "same-logical-plan"}

    def test_list_preserves_public_partitions_and_revisions(self):
        result = invoke(["list", "--home", self.home, "--date", self.day])
        self.assertEqual(result[0], 0, result[2])
        self.assertEqual(json.loads(result[1]), self.snapshot)
        self.backend.list_day.assert_called_once_with(home=self.home, day=self.day)
        self.backend.mutate.assert_not_called()

    def test_default_date_and_home_are_delegated_not_fabricated(self):
        self.assertEqual(invoke(["list"])[0], 0)
        self.backend.list_day.assert_called_once_with(home=None, day=None)

    def test_home_before_or_after_subcommand(self):
        for argv in (["--home", self.home, "list"], ["list", "--home", self.home]):
            with self.subTest(argv=argv):
                self.assertEqual(invoke(argv)[0], 0)
                self.backend.list_day.assert_called_with(home=self.home, day=None)

    def test_invalid_dates_never_reach_backend(self):
        for value in ("today", "", f"{date.today().year}-02-30", "20000101", "2000-W01-1", "2000-1-1"):
            with self.subTest(value=value):
                self.assert_error(invoke(["list", "--date", value]))
        self.backend.list_day.assert_not_called()

    def test_projects_works_without_daily_backend(self):
        with patch.dict(sys.modules, {"daily_tasks": None}):
            code, stdout, stderr = invoke(["projects", "--home", self.home])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["projects"], [self.project])
        self.registry.assert_called_once_with(home=self.home)

    def test_progress_reads_existing_summary_and_exact_identity(self):
        summary = {"ok": True, "revision": "r1", "tasks": [{"id": "existing", "checkpoint": "实测证据"}]}
        with patch("research_progress.load_summary", return_value=summary) as load:
            code, stdout, stderr = invoke(["progress", "--home", self.home, "--project-id", self.project["id"]])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout), {**summary, "project_id": self.project["id"], "project_name": self.project["name"]})
        load.assert_called_once_with(self.project)
        self.backend.mutate.assert_not_called()

    def test_name_path_whitespace_or_unknown_id_is_not_resolved(self):
        for value in (self.project["name"], self.tmp.name, "unknown", " " + self.project["id"]):
            with self.subTest(value=value):
                self.assert_error(self.write({**self.payload, "project_id": value}))
                self.assert_error(invoke(["progress", "--project-id", value]))
        self.backend.mutate.assert_not_called()

    def test_stdin_bom_unicode_and_empty_initial_revision_are_preserved(self):
        payload = {**self.payload, "revision": ""}
        code, stdout, stderr = self.write(payload, text="\ufeff" + json.dumps(payload, ensure_ascii=False))
        self.assertEqual(code, 0, stderr)
        self.assertTrue(json.loads(stdout)["ok"])
        self.backend.mutate.assert_called_once_with(self.home, payload, actor="agent")
        self.backend.list_day.assert_not_called()

    def test_utf8_bom_file_with_spaces(self):
        path = Path(self.tmp.name) / "任务 参数.json"
        path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8-sig")
        code, _, stderr = invoke(["write", "--args-file", str(path), "--home", self.home])
        self.assertEqual(code, 0, stderr)
        self.backend.mutate.assert_called_once_with(self.home, self.payload, actor="agent")

    def test_workspace_does_not_require_or_create_registered_project(self):
        payload = {**self.payload, "project_id": "__workspace__"}
        self.assertEqual(self.write(payload)[0], 0)
        self.registry.assert_not_called()
        self.backend.mutate.assert_called_once_with(self.home, payload, actor="agent")

    def test_all_allowed_actions_always_use_agent(self):
        for action in ("create", "update", "delete", "plan", "submit", "restore"):
            with self.subTest(action=action):
                payload = {**self.payload, "action": action, "id": "known-task"}
                self.assertEqual(self.write(payload)[0], 0)
                self.backend.mutate.assert_called_with(self.home, payload, actor="agent")
        self.assertEqual(self.backend.mutate.call_count, 6)

    def test_plan_is_a_single_unmodified_call_and_does_not_refresh_revision(self):
        payload = substitute(next(item["payload"] for _, item in examples() if item.get("payload", {}).get("action") == "plan"), self.replacements())
        original = copy.deepcopy(payload)
        self.assertEqual(self.write(payload)[0], 0)
        self.backend.mutate.assert_called_once_with(self.home, original, actor="agent")
        self.backend.list_day.assert_not_called()
        self.assertEqual(payload, original)

    def test_submit_returns_pending_record_link_without_accepting(self):
        payload = {"project_id": self.project["id"], "revision": "r1", "action": "submit",
                   "id": "known-task", "summary": "已验证小样本，尚有失败样本待排查。"}
        pending = {"ok": True, "task": {"status": "active", "review_state": "pending",
                                        "completion_record_id": "records/receipt.md"}}
        self.backend.mutate.return_value = pending
        code, stdout, stderr = self.write(payload)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout), pending)
        self.backend.mutate.assert_called_once_with(self.home, payload, actor="agent")

    def test_accept_complete_and_actor_override_are_rejected(self):
        for payload in ({**self.payload, "action": "accept"}, {**self.payload, "action": "complete"},
                        {**self.payload, "actor": "user"}, {**self.payload, "actor": "agent"}):
            with self.subTest(payload=payload):
                self.assert_error(self.write(payload))
        self.backend.mutate.assert_not_called()

    def test_done_is_rejected_in_all_mutation_shapes(self):
        for payload in ({**self.payload, "task": {"title": "x", "status": "done"}},
                        {**self.payload, "action": "update", "task": {"status": "done"}},
                        {**self.payload, "status": "done"},
                        {**self.payload, "action": "plan", "subtasks": [{"title": "x", "status": "done"}]}):
            with self.subTest(payload=payload):
                self.assert_error(self.write(payload))
        self.backend.mutate.assert_not_called()

    def test_invalid_envelopes_are_rejected_before_backend(self):
        for payload in ([], None, "text", {}, {"payload": self.payload},
                        {**self.payload, "action": []}, {**self.payload, "project_id": " "},
                        {**self.payload, "project_id": None}, {**self.payload, "revision": None},
                        {**self.payload, "revision": 0}, {key: value for key, value in self.payload.items() if key != "revision"}):
            with self.subTest(payload=payload):
                self.assert_error(self.write(payload))
        self.backend.mutate.assert_not_called()

    def test_invalid_duplicate_and_nonfinite_json_is_rejected(self):
        for text in ("", "{broken", "{}{}", '{"action":"create","action":"complete"}',
                     '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{"task":{"status":"active","status":"done"}}'):
            with self.subTest(text=text):
                self.assert_error(self.write({}, text=text))
        self.backend.mutate.assert_not_called()

    def test_missing_or_invalid_file_is_reported_without_traceback(self):
        self.assert_error(invoke(["write", "--args-file", str(Path(self.tmp.name) / "missing.json")]), 1)
        path = Path(self.tmp.name) / "invalid.json"
        path.write_bytes(b"\xff")
        self.assert_error(invoke(["write", "--args-file", str(path)]))
        self.backend.mutate.assert_not_called()

    def test_usage_errors_are_json_and_do_not_mutate(self):
        for argv in ([], ["unknown"], ["write"], ["write", "--args-file", "-", "--actor", "user"], ["progress"]):
            with self.subTest(argv=argv):
                self.assert_error(invoke(argv))
        self.backend.mutate.assert_not_called()

    def test_conflict_is_not_retried_and_revision_is_not_replaced(self):
        self.backend.mutate.side_effect = NotebookConflict("revision conflict; reload")
        error = self.assert_error(self.write(self.payload), 1)
        self.assertEqual(error["error_type"], "NotebookConflict")
        self.backend.mutate.assert_called_once_with(self.home, self.payload, actor="agent")
        self.backend.list_day.assert_not_called()

    def test_backend_validation_and_returned_failures_stay_failures(self):
        self.backend.mutate.side_effect = LLMWikiError("subtask outside goal range")
        self.assert_error(self.write(self.payload), 1)
        self.backend.mutate.side_effect = None
        self.backend.mutate.return_value = {"ok": False, "error": "backend rejected"}
        self.assertEqual(self.assert_error(self.write(self.payload), 1)["error"], "backend rejected")

    def test_missing_or_incomplete_backend_fails_closed(self):
        for backend in (None, types.ModuleType("daily_tasks")):
            with self.subTest(backend=backend), patch.dict(sys.modules, {"daily_tasks": backend}):
                error = self.assert_error(self.write(self.payload), 1)
                self.assertEqual(error["error_type"], "BackendUnavailable")
        self.backend.mutate.assert_not_called()

    def test_no_process_or_window_launch_for_commands(self):
        with patch("subprocess.Popen", side_effect=AssertionError("must stay in process")), patch("webbrowser.open", side_effect=AssertionError("must not open UI")):
            self.assertEqual(invoke(["list"])[0], 0)
            self.assertEqual(self.write(self.payload)[0], 0)

    def test_documented_json_templates_execute_as_direct_payloads(self):
        seen = set()
        for name, template in examples():
            value = substitute(template, self.replacements())
            with self.subTest(document=name, template=template):
                self.assertNotRegex(json.dumps(value), r"<[A-Z_]+>")
                if "day" in value:
                    self.assertEqual(invoke(["--home", value["home"], "list", "--date", value["day"]])[0], 0)
                    continue
                payload = value.get("payload", value)
                self.assertEqual(self.write(payload)[0], 0)
                self.backend.mutate.assert_called_with(self.home, payload, actor="agent")
                seen.add((name, payload["action"]))
        self.assertTrue({("skill", "plan"), ("skill", "submit"), ("skill", "create"), ("readme", "create")} <= seen)

    def test_documented_cli_commands_use_existing_entrypoint_and_valid_arguments(self):
        replacements = {**self.replacements(), "<PAYLOAD_JSON_FILE>": str(Path(self.tmp.name) / "payload.json")}
        Path(replacements["<PAYLOAD_JSON_FILE>"]).write_text(json.dumps(self.payload), encoding="utf-8")
        with patch("research_progress.load_summary", return_value={"ok": True, "revision": "r1", "tasks": []}):
            for name, text in documents().items():
                for line in text.splitlines():
                    if not line.startswith("python -B plugins/llmwiki-lite/scripts/task_cli.py "):
                        continue
                    args = [substitute(item, replacements) for item in shlex.split(line)]
                    with self.subTest(document=name, command=line):
                        self.assertEqual((REPO / args[2]).resolve(), CLI.resolve())
                        result = invoke(args[3:], json.dumps(self.payload))
                        self.assertEqual(result[0], 0, result[2])


@unittest.skipUnless((SCRIPTS / "daily_tasks.py").is_file(), "waiting for daily_tasks backend API")
class CLIBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llmwiki-task-cli-integration-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = str(self.root / "home")
        source = self.root / "source"
        source.mkdir()
        self.project = register_project(str(source), home=self.home, wiki_root=str(self.root / "wiki"))["project"]
        self.backend = importlib.import_module("daily_tasks")
        self.day = date.today().isoformat()

    def run_cli(self, *argv, payload=None, expected=0):
        code, stdout, stderr = invoke(["--home", self.home, *argv], json.dumps(payload, ensure_ascii=False) if payload is not None else "")
        self.assertEqual(code, expected, stderr or stdout)
        self.assertEqual(stdout if expected else stderr, "")
        return json.loads(stderr if expected else stdout)

    def revision(self, project_id=None):
        return self.run_cli("list", "--date", self.day)["revisions"][project_id or self.project["id"]]

    def mutate(self, action, project_id=None, expected=0, **fields):
        pid = project_id or self.project["id"]
        return self.run_cli("write", "--args-file", "-", expected=expected,
                            payload={"project_id": pid, "action": action, "revision": self.revision(pid), **fields})

    def test_real_readers_are_side_effect_free_and_return_exact_ids(self):
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        projects = self.run_cli("projects")
        self.assertEqual(projects["projects"][0]["id"], self.project["id"])
        summary = self.run_cli("progress", "--project-id", self.project["id"])
        self.assertEqual(summary["tasks"], [])
        daily = self.run_cli("list", "--date", self.day)
        self.assertIn("__workspace__", daily["revisions"])
        after = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_create_update_submit_and_delete_share_task_identity(self):
        self.mutate("create", task={"title": "可交付实验", "scheduled_date": self.day})
        task = self.run_cli("list", "--date", self.day)["tasks"][0]
        self.mutate("update", id=task["id"], task={"description": "产出结果表；验收：命令和输入可复现。"})
        self.mutate("submit", id=task["id"], summary="小样本命令已验证；全量实验未执行。")
        current = self.run_cli("list", "--date", self.day)["tasks"][0]
        self.assertEqual(current["id"], task["id"])
        self.assertEqual(current["review_state"], "pending")
        self.assertNotEqual(current["status"], "done")
        record = current["completion_record_id"]
        self.assertTrue((Path(self.project["wiki_root"]) / record.split("#", 1)[0]).is_file())
        progress = self.run_cli("progress", "--project-id", self.project["id"])
        self.assertEqual(progress["tasks"][0]["id"], task["id"])
        self.mutate("delete", id=task["id"])
        self.assertEqual(self.run_cli("list", "--date", self.day)["tasks"], [])

    def test_plan_is_atomic_and_same_request_replay_is_idempotent(self):
        next_day = (date.today() + timedelta(days=1)).isoformat()
        payload = {"project_id": self.project["id"], "action": "plan", "revision": self.revision(),
                   "request_id": "one-logical-plan", "task": {"title": "父目标", "start": self.day, "end": next_day},
                   "subtasks": [{"title": "第一项", "scheduled_date": self.day}, {"title": "第二项", "scheduled_date": next_day}]}
        first = self.run_cli("write", "--args-file", "-", payload=payload)
        second = self.run_cli("write", "--args-file", "-", payload=payload)
        self.assertEqual(first["revision"], second["revision"])
        tasks = self.run_cli("progress", "--project-id", self.project["id"])["tasks"]
        self.assertEqual(len(tasks), 3)
        goals = [task for task in tasks if task.get("kind") == "goal"]
        self.assertEqual(len(goals), 1)
        self.assertEqual({task["parent_id"] for task in tasks if task.get("kind") != "goal"}, {goals[0]["id"]})
        today = self.run_cli("list", "--date", self.day)["tasks"][0]
        self.assertEqual(today["parent_title"], "父目标")
        snapshot = self.run_cli("progress", "--project-id", self.project["id"])
        bad = {**payload, "request_id": "bad-plan", "revision": self.revision(),
               "subtasks": [payload["subtasks"][0], {"title": "invalid", "scheduled_date": "not-a-date"}]}
        self.run_cli("write", "--args-file", "-", payload=bad, expected=1)
        self.assertEqual(self.run_cli("progress", "--project-id", self.project["id"]), snapshot)

    def test_conflict_does_not_overwrite_or_retry(self):
        stale = self.revision()
        self.mutate("create", task={"title": "保留已有项", "scheduled_date": self.day})
        self.run_cli("write", "--args-file", "-", expected=1,
                     payload={"project_id": self.project["id"], "action": "create", "revision": stale,
                              "task": {"title": "冲突不写入", "scheduled_date": self.day}})
        self.assertEqual([task["title"] for task in self.run_cli("list", "--date", self.day)["tasks"]], ["保留已有项"])

    def test_workspace_stdin_write_in_fresh_process_from_other_directory(self):
        payload = {"project_id": "__workspace__", "action": "create", "revision": self.revision("__workspace__"),
                   "task": {"title": "临时中文任务", "scheduled_date": self.day}}
        env = {**os.environ, "LLMWIKI_HOME": self.home, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-B", str(CLI), "write", "--args-file", "-"],
                                input=json.dumps(payload, ensure_ascii=False), text=True, encoding="utf-8",
                                capture_output=True, cwd=self.root, env=env, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        tasks = self.run_cli("list", "--date", self.day)["tasks"]
        self.assertEqual(tasks[0]["title"], "临时中文任务")
        self.assertEqual(tasks[0]["project_id"], "__workspace__")
        self.assertEqual(len(self.run_cli("projects")["projects"]), 1)

    def test_documented_json_against_real_backend(self):
        next_day = (date.today() + timedelta(days=1)).isoformat()
        self.mutate("create", task={"title": "待交付项", "scheduled_date": self.day})
        task_id = self.run_cli("list", "--date", self.day)["tasks"][0]["id"]
        replacements = {"<HOME>": self.home, "<PROJECT_ID>": self.project["id"], "<LOCAL_TODAY>": self.day,
                        "<PLAN_START>": self.day, "<PLAN_END>": next_day, "<DAY_1>": self.day, "<DAY_2>": next_day,
                        "<TASK_ID>": task_id, "<REQUEST_ID>": "documentation-plan"}
        for name, template in examples():
            with self.subTest(document=name, template=template):
                value = substitute(template, replacements)
                if "day" in value:
                    self.run_cli("list", "--date", value["day"])
                    continue
                payload = value.get("payload", value)
                payload["revision"] = self.revision(payload["project_id"])
                self.run_cli("write", "--args-file", "-", payload=payload)


if __name__ == "__main__":
    unittest.main()
