"""Tests for the smart-capture SessionStart/Stop hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import capture_hook  # noqa: E402
from llmwiki_core import LLMWikiError  # noqa: E402
from llmwiki_registry import load_settings, register_project, update_settings  # noqa: E402
from research_records import write_record  # noqa: E402

RECORD_TOOL = "mcp__plugin_llmwiki-lite_llmwiki__llmwiki_record_write"


class CaptureHookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="capture-hook-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = str(self.root / "home")
        self.source = self.root / "source"
        self.source.mkdir()
        self.project = register_project(str(self.source), name="点云配准", home=self.home)["project"]
        env = patch.dict(os.environ, {"LLMWIKI_HOME": self.home}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(capture_hook.MODE_ENV, None)
        self.transcript = self.root / "session.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        self.clock = 1_800_000_000.0

    # ----- fixtures -------------------------------------------------------

    def append(self, entries):
        with self.transcript.open("a", encoding="utf-8", newline="\n") as handle:
            for entry in entries:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def claude(self, *, prompt=None, tools=(), text=""):
        entries = []
        if prompt is not None:
            entries.append({"type": "user", "message": {"role": "user", "content": prompt}})
        for name in tools:
            entries.append({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t", "name": name, "input": {}}]}})
            entries.append({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}})
        if text:
            entries.append({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": text}]}})
        self.append(entries)

    def rollout(self, *, turn=True, tools=(), text=""):
        entries = [{"type": "event_msg", "payload": {"type": "task_started"}}] if turn else []
        for name in tools:
            call = {"type": "function_call", "name": name, "arguments": "{}", "call_id": "c"}
            if name.startswith("llmwiki_"):
                call["namespace"] = "mcp__llmwiki"
            entries.append({"type": "response_item", "payload": call})
            entries.append({"type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "c", "output": "ok"}})
        if text:
            entries.append({"type": "response_item", "payload": {
                "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}})
        self.append(entries)

    def start(self, *, source="startup", cwd=None, session="s1"):
        return capture_hook.session_start({
            "hook_event_name": "SessionStart",
            "session_id": session,
            "transcript_path": str(self.transcript),
            "cwd": str(cwd or self.source),
            "source": source,
        }, home=self.home)

    def stop(self, message="已完成。", *, active=False, cwd=None, session="s1"):
        return capture_hook.stop({
            "hook_event_name": "Stop",
            "session_id": session,
            "transcript_path": str(self.transcript),
            "cwd": str(cwd or self.source),
            "stop_hook_active": active,
            "last_assistant_message": message,
        }, home=self.home, now=self.clock)

    def state(self, session="s1"):
        return capture_hook.load_state(capture_hook._state_path(self.project, session))

    # ----- SessionStart ---------------------------------------------------

    def test_session_start_injects_rules_and_recall(self):
        write_record(
            str(self.source),
            state_root=self.project["state_root"],
            project_id=self.project["id"],
            title="粗配准改用 FPFH+RANSAC",
            understanding="初始位姿误差大时 ICP 容易陷入局部最优，改为 FPFH+RANSAC 做粗配准。",
            next_steps=["在数据集 B 上复测 RMSE"],
            tags=["决策", "点云配准"],
        )
        write_record(
            str(self.source),
            state_root=self.project["state_root"],
            project_id=self.project["id"],
            title="用户验收：相机出深度图",
            understanding="用户明确确认此任务完成。此记录仅留存用户操作。",
            tags=["task-receipt"],
        )
        output = self.start()
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("点云配准", context)
        self.assertIn(str(self.source), context)
        self.assertIn(self.project["id"], context)
        self.assertIn("llmwiki_record_write", context)
        self.assertIn("粗配准改用 FPFH+RANSAC〔决策〕", context)
        self.assertIn("在数据集 B 上复测 RMSE", context)
        self.assertNotIn("用户验收", context)
        self.assertNotIn("{{", context)
        self.assertLessEqual(len(context), capture_hook.MAX_CONTEXT_CHARS)

    def test_record_written_as_the_rules_say_reaches_recall(self):
        # A project whose Wiki lives outside the source tree, as in real use.
        import mcp_server

        source = self.root / "external-source"
        source.mkdir()
        wiki = self.root / "vault" / "external"
        project = register_project(str(source), name="外部知识库项目", wiki_root=str(wiki), home=self.home)["project"]
        # The injected rules pass project_root and project_id only, never state_root.
        mcp_server.dispatch("llmwiki_record_write", {
            "project_root": str(source),
            "project_id": project["id"],
            "title": "体素下采样单位是毫米",
            "understanding": "点云坐标是毫米时 voxel_size 要按毫米设置，否则几乎不降采样。",
            "tags": ["踩坑"],
        })
        self.assertTrue(list(wiki.glob("records/*/*/*.md")))
        self.assertFalse((source / "wiki").exists())
        listed = mcp_server.dispatch("llmwiki_record_list", {"project_root": str(source)})
        self.assertEqual(listed["count"], 1)
        context = self.start(cwd=source)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("体素下采样单位是毫米〔踩坑〕", context)

    def test_session_start_without_records_invites_first_record(self):
        context = self.start()["hookSpecificOutput"]["additionalContext"]
        self.assertIn("暂无可用于接续的科研记录", context)

    def test_session_start_outside_project_is_silent(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.assertIsNone(self.start(cwd=elsewhere))

    def test_session_start_from_wiki_directory_resolves_project(self):
        wiki = Path(self.project["wiki_root"])
        wiki.mkdir(parents=True, exist_ok=True)
        self.assertIsNotNone(self.start(cwd=wiki))

    def test_rules_template_has_no_unknown_placeholders(self):
        text = capture_hook.rules_text(self.project)
        self.assertNotIn("{{", text)
        self.assertIn(self.project["id"], text)

    def test_session_state_is_pruned_after_ttl(self):
        stale = capture_hook._sessions_dir(self.project) / "stale.json"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("{}", encoding="utf-8")
        old = time.time() - capture_hook.SESSION_TTL_SECONDS - 60
        os.utime(stale, (old, old))
        self.start()
        self.assertFalse(stale.exists())

    # ----- modes ----------------------------------------------------------

    def test_capture_mode_setting_is_validated(self):
        self.assertEqual(load_settings(self.home)["capture_mode"], "auto")
        with self.assertRaises(LLMWikiError):
            update_settings(home=self.home, capture_mode="always")
        update_settings(home=self.home, capture_mode="passive")
        self.assertEqual(load_settings(self.home)["capture_mode"], "passive")

    def test_off_mode_silences_start_and_stop(self):
        update_settings(home=self.home, capture_mode="off")
        self.assertIsNone(self.start())
        self.claude(prompt="排查", tools=["Read"] * 20, text="根因找到了。")
        self.assertIsNone(self.stop())
        with patch.dict(os.environ, {capture_hook.MODE_ENV: "auto"}):
            self.assertIsNotNone(self.start())

    def test_passive_mode_injects_rules_but_never_blocks(self):
        update_settings(home=self.home, capture_mode="passive")
        self.assertIsNotNone(self.start())
        self.claude(prompt="排查", tools=["Read"] * 20, text="根因找到了。")
        self.assertIsNone(self.stop())

    # ----- Stop checkpoints -----------------------------------------------

    def test_small_turn_does_not_block(self):
        self.start()
        self.claude(prompt="这个参数什么意思？", tools=["Read", "Grep"], text="它控制体素大小。")
        self.assertIsNone(self.stop())

    def test_substantial_work_blocks_once_then_backs_off(self):
        self.start()
        self.claude(prompt="排查配准发散", tools=["Read"] * 7, text="根因是单位不一致。")
        output = self.stop()
        self.assertEqual(output["decision"], "block")
        self.assertIn(str(self.source), output["reason"])
        self.assertIn(self.project["id"], output["reason"])
        self.assertIn("llmwiki_record_write", output["reason"])
        self.assertIn("（本段无需记录）", output["reason"])

        # The model reviews and finds nothing; the continuation must not block again.
        self.claude(text="（本段无需记录）")
        self.assertIsNone(self.stop("（本段无需记录）", active=True))
        state = self.state()
        self.assertEqual(state["backoff"], 1)
        self.assertEqual(state["offset"], self.transcript.stat().st_size)

        # The threshold doubled: seven more tool calls are not enough.
        self.claude(prompt="继续", tools=["Read"] * 7, text="好了。")
        self.assertIsNone(self.stop())
        # Enough calls, but the doubled spacing has not elapsed yet.
        self.claude(prompt="再看看", tools=["Read"] * 6, text="好了。")
        self.assertIsNone(self.stop())
        self.clock += 2 * capture_hook.NUDGE_SPACING_SECONDS + 1
        self.assertEqual(self.stop()["decision"], "block")

    def test_record_call_resets_checkpoint_without_block(self):
        self.start()
        self.claude(prompt="跑实验", tools=["Bash"] * 6 + [RECORD_TOOL], text="📝 已记录：实验结果")
        self.assertIsNone(self.stop())
        state = self.state()
        self.assertEqual(state["records"], 1)
        self.assertEqual(state["offset"], self.transcript.stat().st_size)
        self.claude(prompt="再改一下", tools=["Edit"] * 3, text="改好了。")
        self.assertIsNone(self.stop())

    def test_record_after_checkpoint_resets_backoff(self):
        self.start()
        self.claude(prompt="排查", tools=["Read"] * 7, text="找到了。")
        self.assertIsNotNone(self.stop())
        self.claude(tools=[RECORD_TOOL], text="📝 已记录：单位不一致导致配准发散")
        self.assertIsNone(self.stop("📝 已记录：单位不一致导致配准发散", active=True))
        state = self.state()
        self.assertEqual(state["backoff"], 0)
        self.assertEqual(state["records"], 1)
        self.assertFalse(state["nudged"])

    def test_foreign_continuation_neither_blocks_nor_resets(self):
        self.start()
        self.claude(prompt="排查", tools=["Read"] * 7, text="找到了。")
        before = self.state()["offset"]
        self.assertIsNone(self.stop(active=True))
        self.assertEqual(self.state()["offset"], before)

    def test_rollout_transcript_counts_turns_and_mcp_record(self):
        self.start()
        self.rollout(tools=["exec_command"] * 7, text="实验跑完了，mAP 0.61。")
        activity = capture_hook.scan_transcript(str(self.transcript), 0)
        self.assertEqual(activity.tool_calls, 7)
        self.assertEqual(activity.turns, 1)
        self.assertGreater(activity.reply_chars, 0)
        self.assertEqual(self.stop()["decision"], "block")
        self.rollout(turn=True, tools=["llmwiki_record_write"], text="📝 已记录：mAP 0.61 基线")
        self.assertIsNone(self.stop(active=True))
        self.assertEqual(self.state()["backoff"], 0)

    def user_turn(self, text):
        self.append([
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": text}]}},
        ])

    def test_scheduled_automation_turn_is_skipped(self):
        self.start()
        self.user_turn("<heartbeat><automation_id>a1</automation_id>维护日报</heartbeat>")
        self.rollout(turn=False, tools=["exec_command"] * 20, text="日报已生成。")
        self.assertIsNone(self.stop("日报已生成。"))
        self.assertEqual(self.state()["offset"], self.transcript.stat().st_size)
        # The next real user turn is checked normally.
        self.user_turn("帮我排查配准发散")
        self.rollout(turn=False, tools=["exec_command"] * 7, text="根因是单位不一致。")
        self.assertEqual(self.stop("根因是单位不一致。")["decision"], "block")

    def test_long_discussion_without_tools_blocks(self):
        self.start()
        for question in ("方法A的问题？", "那方法B呢", "所以选哪个"):
            self.claude(prompt=question, text="分析" * 500)
        self.assertEqual(self.stop("结论：选方法B。")["decision"], "block")

    def test_question_defers_checkpoint_until_answer(self):
        self.start()
        self.claude(prompt="排查", tools=["Read"] * 8, text="要我直接改吗？")
        self.assertIsNone(self.stop("找到原因了，要我直接改吗？"))
        self.claude(prompt="改吧", tools=["Edit"], text="改好了。")
        self.assertEqual(self.stop("改好了。")["decision"], "block")

    def test_result_marker_counts_as_recorded(self):
        self.start()
        self.claude(prompt="交付", tools=["Bash"] * 8, text="完成。")
        message = "完成。\n<!-- llmwiki-research-result\n{\"version\":1}\n-->"
        self.assertIsNone(self.stop(message))
        self.assertEqual(self.state()["offset"], self.transcript.stat().st_size)

    def test_stop_without_session_start_looks_back(self):
        self.claude(prompt="排查", tools=["Read"] * 7, text="找到了。")
        self.assertEqual(self.stop()["decision"], "block")

    def test_stop_outside_project_or_without_session_is_silent(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.claude(prompt="排查", tools=["Read"] * 20, text="找到了。")
        self.assertIsNone(self.stop(cwd=elsewhere))
        self.assertIsNone(self.stop(session=""))

    # ----- transcript scanning --------------------------------------------

    def test_scan_skips_partial_line_and_rescans_rewritten_file(self):
        self.claude(prompt="排查", tools=["Read"] * 2)
        complete = self.transcript.stat().st_size
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write('{"type": "assistant", "message": {"content": [{"type": "tool_use"')
        activity = capture_hook.scan_transcript(str(self.transcript), 0)
        self.assertEqual(activity.tool_calls, 2)
        self.assertEqual(activity.end, complete)
        rescanned = capture_hook.scan_transcript(str(self.transcript), complete * 10)
        self.assertEqual(rescanned.tool_calls, 2)

    def test_scan_reads_only_the_tail_window(self):
        self.claude(prompt="旧工作", tools=["Read"] * 30)
        with patch.object(capture_hook, "MAX_SCAN_BYTES", 600):
            activity = capture_hook.scan_transcript(str(self.transcript), 0)
        self.assertLess(activity.tool_calls, 30)
        self.assertEqual(activity.end, self.transcript.stat().st_size)

    def test_unrecognised_transcript_falls_back_to_growth(self):
        self.start()
        self.append([{"kind": "future-format", "body": "x" * 200}] * 3)
        with patch.object(capture_hook, "MIN_UNKNOWN_BYTES", 5000):
            self.assertIsNone(self.stop())
            self.append([{"kind": "future-format", "body": "x" * 200}] * 30)
            output = self.stop()
        self.assertEqual(output["decision"], "block")
        self.assertNotIn("0 次工具调用", output["reason"])

    def test_scan_tolerates_missing_transcript(self):
        activity = capture_hook.scan_transcript(str(self.root / "missing.jsonl"), 5)
        self.assertEqual((activity.tool_calls, activity.end), (0, 5))
        self.assertEqual(capture_hook.scan_transcript(None, 0).tool_calls, 0)

    # ----- process boundary -----------------------------------------------

    def run_script(self, payload: bytes):
        env = dict(os.environ, LLMWIKI_HOME=self.home)
        return subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPTS / "capture_hook.py")],
            input=payload, capture_output=True, env=env, timeout=60,
        )

    def test_process_is_fail_open(self):
        for payload in (b"", b"not json", b"[]", json.dumps({"hook_event_name": "Stop"}).encode()):
            result = self.run_script(payload)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")

    def test_process_emits_ascii_json(self):
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "p1",
            "transcript_path": str(self.transcript),
            "cwd": str(self.source),
            "source": "startup",
        }
        result = self.run_script(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(result.returncode, 0)
        result.stdout.decode("ascii")
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("点云配准", context)


if __name__ == "__main__":
    unittest.main()
