"""Tests for the asynchronous Codex research-result hook."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import record_research_result as hook  # noqa: E402
from daily_tasks import list_day, mutate  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from research_records import list_records  # noqa: E402
from research_progress import load_summary  # noqa: E402


class ResearchResultHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="research-result-hook-")
        self.addCleanup(self.tmp.cleanup)
        self.home = str(Path(self.tmp.name) / "home")
        self.source = Path(self.tmp.name) / "source"
        self.source.mkdir()
        self.project = register_project(str(self.source), home=self.home)["project"]
        self.projects = {"projects": [self.project]}

    def payload(self, marker: dict, *, session="session-1"):
        raw = json.dumps(marker, ensure_ascii=False, separators=(",", ":"))
        message = "本轮工作已完成。\n\n<!-- llmwiki-research-result\n" + raw + "\n-->"
        return {
            "hook_event_name": "Stop",
            "session_id": session,
            "cwd": str(self.source),
            "last_assistant_message": message,
        }

    def run_hook(self, value):
        with patch.object(hook, "list_projects", return_value=self.projects):
            with patch.dict("os.environ", {"LLMWIKI_HOME": self.home}, clear=False):
                hook.handle(value)

    def test_independent_record_does_not_require_task(self):
        self.run_hook(self.payload({
            "version": 1,
            "title": "独立阶段性结论",
            "understanding": "低纹理区域的跟踪方案已完成小样本验证，边界条件仍需补充。",
            "evidence": ["results/summary.csv"],
            "conclusion": "可以进入跨场景验证。",
            "next_steps": ["补充夜间样本"],
            "tags": ["阶段性理解"],
        }))
        records = list_records(self.project["source_root"], state_root=self.project["state_root"])["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "独立阶段性结论")
        self.assertIn("research-result-captured", (Path(self.project["state_root"]) / "events.jsonl").read_text(encoding="utf-8"))

        # The same Stop delivery is idempotent.
        self.run_hook(self.payload({
            "version": 1,
            "title": "独立阶段性结论",
            "understanding": "低纹理区域的跟踪方案已完成小样本验证，边界条件仍需补充。",
            "evidence": ["results/summary.csv"],
            "conclusion": "可以进入跨场景验证。",
            "next_steps": ["补充夜间样本"],
            "tags": ["阶段性理解"],
        }))
        self.assertEqual(list_records(self.project["source_root"], state_root=self.project["state_root"])["count"], 1)

    def test_explicit_project_from_outside_project_writes_independent_record(self):
        outside = Path(self.tmp.name) / "general-chat"
        outside.mkdir()
        value = self.payload({"version": 1, "project_id": self.project["id"],
                              "title": "跨目录研究记录", "understanding": "该结果来自通用会话。"})
        value["cwd"] = str(outside)
        result = subprocess.run([sys.executable, "-I", "-B", str(Path(hook.__file__))],
                                input=json.dumps(value, ensure_ascii=False), text=True,
                                capture_output=True,
                                env={**os.environ, "LLMWIKI_HOME": self.home, "PYTHONUTF8": "1"},
                                timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = list_records(self.project["source_root"], state_root=self.project["state_root"])["records"]
        self.assertEqual([item["title"] for item in records], ["跨目录研究记录"])

    def test_task_delivery_is_pending_not_done(self):
        day = "2026-09-22"
        initial = list_day(self.home, day)
        created = mutate(self.home, {
            "project_id": self.project["id"],
            "action": "create",
            "revision": initial["revisions"][self.project["id"]],
            "task": {"title": "验证低纹理跟踪方案", "scheduled_date": day},
        }, actor="user")
        task = created["task"]
        self.run_hook(self.payload({
            "version": 1,
            "task_id": task["id"],
            "summary": "已完成小样本验证并保存结果表，跨场景验证仍待用户检查。",
            "title": "验证低纹理跟踪方案",
            "understanding": "已完成小样本验证并保存结果表。",
        }, session="session-task"))
        current = load_summary(self.project)
        saved = next(item for item in current["tasks"] if item["id"] == task["id"])
        self.assertEqual(saved["review_state"], "pending")
        self.assertNotEqual(saved["status"], "done")
        self.assertTrue(saved["completion_record_id"])

    def test_real_subprocess_stop_without_task_and_invalid_task_is_safe(self):
        script = Path(hook.__file__)
        env = {**os.environ, "LLMWIKI_HOME": self.home, "PYTHONUTF8": "1"}
        useful = self.payload({"version": 1, "title": "已验证的独立结果",
                               "understanding": "使用两组输入确认边界逻辑。",
                               "evidence": ["main.py 及测试输出"],
                               "next_steps": ["增加第三组输入"]}, session="real-process")
        for value in (useful, useful, self.payload({"version": 1, "task_id": "f" * 32,
                                                    "summary": "不存在的任务"}, session="invalid-task")):
            completed = subprocess.run([sys.executable, "-I", "-B", str(script)],
                                       input=json.dumps(value, ensure_ascii=False), text=True,
                                       capture_output=True, env=env, timeout=15, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        records = list_records(self.project["source_root"], state_root=self.project["state_root"])
        self.assertEqual(records["count"], 1)
        self.assertEqual(records["records"][0]["title"], "已验证的独立结果")

    def test_task_rejection_and_second_hook_delivery(self):
        day = "2026-09-23"
        initial = list_day(self.home, day)
        task = mutate(self.home, {"project_id": self.project["id"], "action": "create",
                                   "revision": initial["revisions"][self.project["id"]],
                                   "task": {"title": "检查算法边界", "scheduled_date": day}})["task"]
        def deliver(summary, session):
            payload = self.payload({"version": 1, "task_id": task["id"],
                                    "summary": summary, "evidence": ["测试输出"],
                                    "remaining": "待用户核查"}, session=session)
            process = subprocess.run([sys.executable, "-I", "-B", str(Path(hook.__file__))],
                                     input=json.dumps(payload, ensure_ascii=False), text=True,
                                     capture_output=True,
                                     env={**os.environ, "LLMWIKI_HOME": self.home, "PYTHONUTF8": "1"},
                                     timeout=15, check=False)
            self.assertEqual(process.returncode, 0, process.stderr)
        deliver("首轮验证", "cycle-1")
        first = next(t for t in load_summary(self.project)["tasks"] if t["id"] == task["id"])
        self.assertEqual(first["review_state"], "pending")
        reject = mutate(self.home, {"project_id": self.project["id"], "action": "reject",
                                    "revision": load_summary(self.project)["revision"],
                                    "id": task["id"], "reason": "需补负例"})["task"]
        self.assertEqual((reject["status"], reject["review_state"]), ("active", "rejected"))
        deliver("补充负例验证", "cycle-2")
        second = next(t for t in load_summary(self.project)["tasks"] if t["id"] == task["id"])
        self.assertNotEqual(second["completion_record_id"], first["completion_record_id"])
        self.assertEqual(second["review_state"], "pending")
        accepted = mutate(self.home, {"project_id": self.project["id"], "action": "accept",
                                      "revision": load_summary(self.project)["revision"],
                                      "id": task["id"]})["task"]
        self.assertEqual((accepted["status"], accepted["review_state"]), ("done", "accepted"))
        self.assertEqual(list_records(self.project["source_root"], state_root=self.project["state_root"])["count"], 4)

    def test_ordinary_stop_and_active_stop_are_ignored(self):
        self.run_hook({
            "hook_event_name": "Stop",
            "session_id": "ordinary",
            "cwd": str(self.source),
            "last_assistant_message": "这只是一个普通解释，没有科研结果。",
        })
        self.run_hook({
            "hook_event_name": "Stop",
            "stop_hook_active": True,
            "session_id": "active",
            "cwd": str(self.source),
            "last_assistant_message": "<!-- llmwiki-research-result\n{}\n-->",
        })
        self.assertEqual(list_records(self.project["source_root"], state_root=self.project["state_root"])["count"], 0)


if __name__ == "__main__":
    unittest.main()
