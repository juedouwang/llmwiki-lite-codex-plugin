"""M-02 采集机制的回归测试：授权闸门、注入块过滤、A/B 对账、幂等、重写与归档。

全部使用合成 fixture 写入 TemporaryDirectory；不读取任何真实会话目录
（C:\\Users\\lyn\\.codex 等一律不碰），不启用真实采集，也不创建计划任务。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import research_capture as capture  # noqa: E402
# 模块对外是宿主中立名（smoke_test 禁止 scripts/*.py 出现 "Codex" 字样）；
# 测试里用别名保留 M-02 交付说明中的叫法。
from research_capture import (  # noqa: E402
    RolloutCaptureAdapter as CodexAdapter,
    RolloutReader as CodexRolloutReader,
    ConsentRequired,
    CaptureInputError,
    open_store,
    project_for_cwd,
    read_consent,
    redact,
    write_consent,
)

INJECTED_AGENTS = (
    "# AGENTS.md instructions for E:\\demo\n\n"
    "<INSTRUCTIONS>\n只读仓库，不改用户数据。\n</INSTRUCTIONS>"
)
ENVIRONMENT_CONTEXT = (
    "<environment_context>\n  <cwd>E:\\demo</cwd>\n  <approval_policy>never</approval_policy>\n"
    "</environment_context>"
)


class CaptureAdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="llmwiki-capture-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.codex_home = self.root / "codex_home"
        self.source = self.root / "source"
        self.source.mkdir(parents=True)

    # ---- fixture 构造 ----

    def project(self, name="demo", source=None):
        return {
            "id": f"{name}-0123456789abcdef",
            "name": name,
            "source_root": str(source or self.source),
            "state_root": str(self.root / f"{name}-state"),
        }

    def store_for(self, project):
        store = open_store(project["state_root"])
        self.addCleanup(store.close)
        return store

    def enable(self, project, now="2026-01-01T00:00:00.000Z"):
        return write_consent(
            project,
            {
                "capture_enabled": True,
                "hosts": {"codex": True, "claude_code": False, "opencode": False},
            },
            now=now,
        )

    def session_meta(self, session_id, cwd, ts="2026-09-19T08:00:00.000Z", **extra):
        payload = {
            "session_id": session_id,
            "id": session_id,
            "timestamp": ts,
            "cwd": cwd,
            "originator": "codex_cli",
            "cli_version": "0.1.0",
            "source": "cli",
            "thread_source": extra.pop("thread_source", "user"),
            "model_provider": "openai",
            "history_mode": "persistent",
            "forked_from_id": extra.pop("forked_from_id", None),
        }
        return {"timestamp": ts, "type": "session_meta", "payload": payload}

    def user_via_response_item(self, ts, text, injected=True, extra_text=None):
        content = []
        if injected:
            content.append({"type": "input_text", "text": INJECTED_AGENTS})
            content.append({"type": "input_text", "text": ENVIRONMENT_CONTEXT})
        content.append({"type": "input_text", "text": text})
        if extra_text is not None:
            content.append({"type": "input_text", "text": extra_text})
        return {
            "timestamp": ts,
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": content},
        }

    def user_via_event_msg(self, ts, text):
        return {
            "timestamp": ts,
            "type": "event_msg",
            "payload": {"type": "user_message", "message": text},
        }

    def assistant_via_response_item(self, ts, text):
        return {
            "timestamp": ts,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        }

    def assistant_via_event_msg(self, ts, text):
        return {
            "timestamp": ts,
            "type": "event_msg",
            "payload": {"type": "agent_message", "message": text, "phase": "final_answer"},
        }

    def function_call(self, ts, name, call_id, arguments):
        return {
            "timestamp": ts,
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": name,
                "call_id": call_id,
                "arguments": arguments,
            },
        }

    def function_call_output(self, ts, call_id, output):
        return {
            "timestamp": ts,
            "type": "response_item",
            "payload": {"type": "function_call_output", "call_id": call_id, "output": output},
        }

    def noise(self, ts, kind="reasoning"):
        payloads = {
            "reasoning": {"type": "reasoning", "summary": ["思考中"]},
            "token_usage_record": {"type": "token_usage_record", "total_tokens": 123},
            "turn_context": {"type": "turn_context", "cwd": "E:\\demo"},
            "world_state": {"type": "world_state", "state": {}},
            "compacted": {"type": "compacted", "replacement": []},
        }
        return {"timestamp": ts, "type": kind, "payload": payloads[kind]}

    def write_records(self, path, records, *, trailing_newline=True, prefix_bytes=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in records
        )
        raw = blob.encode("utf-8")
        if prefix_bytes is not None:
            raw = raw[:prefix_bytes]
        elif not trailing_newline:
            raw = raw.rstrip(b"\n")
        path.write_bytes(raw)
        return raw

    def default_records(self, session_id, cwd, *, a_only=None, b_only=None):
        return [
            self.session_meta(session_id, cwd),
            self.noise("2026-09-19T08:00:01.000Z", "turn_context"),
            self.user_via_response_item("2026-09-19T08:01:00.000Z", "我们这周跑一下基线实验。"),
            self.user_via_event_msg("2026-09-19T08:01:00.000Z", "我们这周跑一下基线实验。"),
            self.assistant_via_response_item("2026-09-19T08:02:00.000Z", "好的，先记录基线指标。"),
            self.assistant_via_event_msg("2026-09-19T08:02:00.000Z", "好的，先记录基线指标。"),
            self.function_call(
                "2026-09-19T08:03:00.000Z",
                "shell",
                "call_0001",
                '{"command":"python run.py"}',
            ),
            self.function_call_output("2026-09-19T08:03:05.000Z", "call_0001", "ok"),
            self.noise("2026-09-19T08:04:00.000Z", "token_usage_record"),
            self.noise("2026-09-19T08:05:00.000Z", "world_state"),
            self.noise("2026-09-19T08:06:00.000Z", "compacted"),
        ] + ([self.user_via_response_item("2026-09-19T08:07:00.000Z", a_only)] if a_only else []) + (
            [self.user_via_event_msg("2026-09-19T08:08:00.000Z", b_only)] if b_only else []
        )

    def rollout_path(self, name="rollout-2026-09-19T08-00-00-abc.jsonl", archived=False):
        if archived:
            return self.codex_home / "archived_sessions" / name
        return self.codex_home / "sessions" / "2026" / "09" / "19" / name

    # ---- 数据库读取辅助 ----

    def rows(self, store, table, order="rowid"):
        with store.transaction() as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]

    def activities(self, store):
        return self.rows(store, "activities")

    def kinds(self, store):
        out = {}
        for row in self.activities(store):
            out[row["kind"]] = out.get(row["kind"], 0) + 1
        return out

    def texts(self, store):
        return [json.loads(row["evidence_json"])["text"] for row in self.activities(store)]

    # ---- 1. 未授权 ----

    def test_unauthorized_capture_is_rejected_and_writes_nothing(self):
        project = self.project()
        path = self.rollout_path()
        self.write_records(path, self.default_records("sess-unauth", str(self.source)))

        with self.assertRaises(ConsentRequired) as caught:
            capture.require_capture(project)
        self.assertEqual(caught.exception.code, "CONSENT_REQUIRED")

        store = self.store_for(project)
        report = CodexAdapter(store, self.codex_home, [project]).scan()

        self.assertEqual(len(report["skipped_unauthorized"]), 1)
        self.assertEqual(report["activities_written"], 0)
        for table in ("sessions", "activities", "cursors"):
            self.assertEqual(self.rows(store, table), [], f"{table} 不应有内容")
        # read_consent 不得为了读而写盘；未启用时磁盘上不该多出 consent.json
        self.assertFalse(capture.consent_path(project).exists())

    def test_consent_defaults_are_all_off_and_revision_grows(self):
        project = self.project()
        consent = read_consent(project)
        self.assertFalse(consent["capture_enabled"])
        self.assertEqual(consent["hosts"], {"codex": False, "claude_code": False, "opencode": False})
        self.assertIsNone(consent["authorized_at"])
        self.assertEqual(consent["revision_no"], 1)
        self.assertEqual(consent["session_scope"], "new_bound_sessions")
        self.assertEqual(
            consent["exclude_patterns"], [".env", ".env.*", "*.pem", "*.key", "**/secrets/**"]
        )

        ensured = capture.ensure_consent(project)
        self.assertFalse(ensured["capture_enabled"])
        self.assertTrue(capture.consent_path(project).is_file())

        first = self.enable(project, now="2026-02-02T03:04:05.000Z")
        self.assertEqual(first["revision_no"], 2)
        self.assertEqual(first["authorized_at"], "2026-02-02T03:04:05.000Z")

        second = write_consent(project, {"capture_enabled": False})
        self.assertEqual(second["revision_no"], 3)
        # 授权时间只在首次真正启用时写入，之后关闭也不清空
        self.assertEqual(second["authorized_at"], "2026-02-02T03:04:05.000Z")

        with self.assertRaises(CaptureInputError):
            write_consent(project, {"capture_enabled": "yes"})
        with self.assertRaises(CaptureInputError):
            write_consent(project, {"unknown_field": True})

    # ---- 2. 授权后解析三类消息 ----

    def test_authorized_parses_three_kinds_and_ignores_injected_blocks(self):
        project = self.project()
        session_id = "sess-authorized"
        mentioned = "帮我核对 E:\\demo 里的 AGENTS.md 是否需要更新。"
        records = self.default_records(session_id, str(self.source))
        records.insert(
            2, self.user_via_response_item("2026-09-19T08:00:30.000Z", mentioned)
        )
        # A/B 两条路径都给出这条消息；提到 AGENTS.md 但不以注入块开头，必须保留且只留一条
        records.append(self.user_via_event_msg("2026-09-19T08:00:30.000Z", mentioned))
        self.write_records(self.rollout_path(), records)

        self.enable(project)
        store = self.store_for(project)
        report = CodexAdapter(store, self.codex_home, [project]).scan()

        counts = self.kinds(store)
        self.assertEqual(counts.get("user_message"), 2)
        self.assertEqual(counts.get("assistant_message"), 1)
        self.assertEqual(counts.get("tool_call"), 1)
        self.assertEqual(report["activities_written"], 4)

        texts = self.texts(store)
        for text in texts:
            self.assertNotIn("# AGENTS.md instructions", text)
            self.assertNotIn("<environment_context>", text)
            self.assertNotIn("<INSTRUCTIONS>", text)
        self.assertEqual(texts.count(mentioned), 1, "A/B 同一消息只应入账一次")
        self.assertIn("我们这周跑一下基线实验。", texts)

    # ---- 3. A/B 对账缺口 ----

    def test_ab_reconcile_gaps_are_reported_honestly(self):
        project = self.project()
        records = self.default_records(
            "sess-gap",
            str(self.source),
            a_only="这条只出现在 response_item 路径",
            b_only="这条只出现在 event_msg 路径",
        )
        self.write_records(self.rollout_path(), records)
        self.enable(project)
        store = self.store_for(project)
        report = CodexAdapter(store, self.codex_home, [project]).scan()

        user = report["reconcile"]["user_message"]
        self.assertEqual(user["response_item"], 2)
        self.assertEqual(user["event_msg"], 2)
        self.assertEqual(user["matched"], 1)
        self.assertEqual(user["response_item_only"], 1)
        self.assertEqual(user["event_msg_only"], 1)
        assistant = report["reconcile"]["assistant_message"]
        self.assertEqual(assistant["matched"], 1)
        self.assertEqual(assistant["response_item_only"], 0)
        self.assertEqual(assistant["event_msg_only"], 0)
        # 3 条用户正文都入账，缺口只是标注，不是丢弃
        self.assertEqual(self.kinds(store).get("user_message"), 3)

    # ---- 4. 幂等 ----

    def test_reingesting_same_messages_ten_times_keeps_one_activity_each(self):
        project = self.project()
        path = self.rollout_path()
        records = self.default_records("sess-idem", str(self.source))
        self.write_records(path, records)
        self.enable(project)
        store = self.store_for(project)
        adapter = CodexAdapter(store, self.codex_home, [project])

        first = adapter.scan()
        self.assertEqual(first["activities_written"], 3)
        sessions = self.rows(store, "sessions")
        self.assertEqual(len(sessions), 1)
        session_id = sessions[0]["id"]

        parsed = CodexRolloutReader().read(path, session_id="sess-idem")
        messages = parsed.messages
        self.assertEqual(len(messages), 3)
        for _ in range(10):
            adapter.ingest_messages(
                project,
                session_id,
                f"codex_rollout:sess-idem:{path.name}",
                messages,
                generation=1,
                position={"offset": parsed.next_offset},
            )
        self.assertEqual(len(self.activities(store)), 3)
        self.assertEqual(len(self.rows(store, "cursors")), 1)

    # ---- 5. 半截最后一行 ----

    def test_truncated_last_line_is_skipped_then_captured_once(self):
        project = self.project()
        path = self.rollout_path()
        records = self.default_records("sess-trunc", str(self.source))
        blob = "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in records
        ).encode("utf-8")
        cut = len(blob) // 2
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob[:cut])

        reader = CodexRolloutReader()
        partial = reader.read(path, session_id="sess-trunc")
        self.assertTrue(partial.truncated)
        self.assertLess(partial.next_offset, len(blob))
        self.assertLess(len(partial.messages), 3, "半截行不得产出消息")

        path.write_bytes(blob)
        tail = reader.read(path, offset=partial.next_offset, session_id="sess-trunc")
        self.assertFalse(tail.truncated)
        self.assertGreater(len(tail.messages), 0)
        whole = reader.read(path, session_id="sess-trunc")
        self.assertFalse(whole.truncated)
        self.assertEqual(len(whole.messages), 3)
        # 分两轮读到的正文并集必须等于整文件读到的正文，一条不丢
        self.assertEqual(
            {message["text"] for message in partial.messages + tail.messages},
            {message["text"] for message in whole.messages},
        )

        # 落到账本上：两轮扫描后仍是 3 条活动，最后一行没有被重复计
        self.enable(project)
        store = self.store_for(project)
        adapter = CodexAdapter(store, self.codex_home, [project])
        path.write_bytes(blob[:cut])
        first = adapter.scan()
        self.assertEqual(first["activities_written"], len(partial.messages))
        self.assertEqual(len(first["truncated"]), 1)
        path.write_bytes(blob)
        second = adapter.scan()
        self.assertEqual(second["activities_written"], 3 - len(partial.messages))
        self.assertEqual(len(self.activities(store)), 3)
        self.assertEqual(second["truncated"], [])

    # ---- 6. 中文 UTF-8 跨 chunk ----

    def test_chinese_utf8_split_across_chunks(self):
        project = self.project()
        path = self.rollout_path()
        chinese = "低纹理点跟踪：这周把基线重跑一遍，记录误差 0.046。"
        records = [
            self.session_meta("sess-cjk", str(self.source)),
            self.user_via_response_item("2026-09-19T10:00:00.000Z", chinese, injected=False),
        ]
        blob = "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in records
        ).encode("utf-8")
        # 在第一行内部、一个三字节汉字的中间切断
        marker = "低纹理".encode("utf-8")
        cut = blob.index(marker) + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob[:cut])

        reader = CodexRolloutReader()
        chunk = reader.read(path, session_id="sess-cjk")
        self.assertTrue(chunk.truncated)
        self.assertEqual(chunk.messages, [])
        self.assertEqual(chunk.bad_lines, 0)

        path.write_bytes(blob)
        tail = reader.read(path, offset=chunk.next_offset, session_id="sess-cjk")
        self.assertEqual(len(tail.messages), 1)
        self.assertEqual(tail.messages[0]["text"], chinese)
        # 全量重读结果一致，说明跨 chunk 拆分没有损坏正文
        self.assertEqual(reader.read(path, session_id="sess-cjk").messages[0]["text"], chinese)

        self.enable(project)
        store = self.store_for(project)
        CodexAdapter(store, self.codex_home, [project]).scan()
        self.assertIn(chinese, self.texts(store))

    # ---- 7. 脱敏 ----

    def test_secrets_are_redacted_before_persist(self):
        project = self.project()
        path = self.rollout_path()
        secret_key = "AKIAIOSFODNN7EXAMPLE"
        token = "sk-abcdefghijklmnopqrstuvwxyz012345"
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234\n-----END RSA PRIVATE KEY-----"
        )
        body = f"临时环境变量：{secret_key}，另外备份了一把 {token}\n{pem}"
        records = [
            self.session_meta("sess-secret", str(self.source)),
            self.user_via_response_item("2026-09-19T11:00:00.000Z", body, injected=False),
        ]
        self.write_records(path, records)

        text, count = redact(body)
        self.assertEqual(count, 3)
        self.assertNotIn(secret_key, text)
        self.assertNotIn(token, text)
        self.assertNotIn("MIIEowIBAAKCAQEA1234", text)
        self.assertEqual(text.count("[REDACTED]"), 3)

        self.enable(project)
        store = self.store_for(project)
        report = CodexAdapter(store, self.codex_home, [project]).scan()
        self.assertEqual(report["redacted_count"], 3)
        self.assertTrue(report["activities_written"] >= 1)

        stored = json.dumps(self.activities(store), ensure_ascii=False)
        self.assertNotIn(secret_key, stored)
        self.assertNotIn(token, stored)
        self.assertNotIn("MIIEowIBAAKCAQEA1234", stored)
        self.assertIn("[REDACTED]", stored)

        # 落盘后的物理文件里也不能出现原文（WAL 一并检查）
        for suffix in ("", "-wal", "-journal"):
            db_file = capture.consent_path(project).parent / f"runtime.sqlite3{suffix}"
            if db_file.exists():
                blob = db_file.read_bytes()
                self.assertNotIn(secret_key.encode(), blob)
                self.assertNotIn(token.encode(), blob)

    # ---- 8. 未归属 cwd ----

    def test_unassigned_cwd_is_not_attributed_to_any_project(self):
        project = self.project()
        outside = self.root / "elsewhere"
        outside.mkdir()
        self.write_records(self.rollout_path(), self.default_records("sess-out", str(outside)))
        self.enable(project)
        store = self.store_for(project)
        report = CodexAdapter(store, self.codex_home, [project]).scan()

        self.assertEqual(len(report["unassigned"]), 1)
        self.assertEqual(report["unassigned"][0]["cwd"], str(outside))
        self.assertEqual(report["activities_written"], 0)
        self.assertEqual(self.rows(store, "sessions"), [])
        self.assertEqual(self.rows(store, "activities"), [])

    # ---- 9. cwd 归属匹配 ----

    def test_project_for_cwd_verbatim_chinese_nested_and_miss(self):
        plain = self.root / "plain"
        (plain / "nested" / "deep").mkdir(parents=True)
        chinese_root = self.root / "研究生文件" / "20_Areas" / "学校科研项目" / "低纹理" / "点跟踪"
        chinese_root.mkdir(parents=True)

        outer = self.project("outer", plain)
        inner = self.project("inner", plain / "nested")
        chinese_project = self.project("cjk", chinese_root)
        projects = [outer, inner, chinese_project]

        # verbatim 前缀必须先剥离
        verbatim = "\\\\?\\" + str(plain / "nested" / "deep")
        hit = project_for_cwd(verbatim, projects)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["id"], inner["id"])

        # 嵌套项目取最长前缀
        self.assertEqual(project_for_cwd(str(plain / "nested" / "deep"), projects)["id"], inner["id"])
        self.assertEqual(project_for_cwd(str(plain / "other"), projects)["id"], outer["id"])

        # 中文路径原样保留
        cjk = project_for_cwd(str(chinese_root), projects)
        self.assertEqual(cjk["id"], chinese_project["id"])

        # 不命中不猜
        self.assertIsNone(project_for_cwd(str(self.root / "unknown"), projects))
        self.assertIsNone(project_for_cwd(None, projects))
        self.assertIsNone(project_for_cwd(str(plain), []))

    # ---- 10. 归档移动 ----

    def test_archived_move_keeps_one_session(self):
        project = self.project()
        active = self.rollout_path()
        self.write_records(active, self.default_records("sess-arch", str(self.source)))
        self.enable(project)
        store = self.store_for(project)
        adapter = CodexAdapter(store, self.codex_home, [project])

        first = adapter.scan()
        self.assertEqual(len(self.rows(store, "sessions")), 1)
        before = len(self.activities(store))
        self.assertEqual(first["activities_written"], before)

        archived = self.rollout_path(archived=True)
        archived.parent.mkdir(parents=True, exist_ok=True)
        active.replace(archived)

        second = adapter.scan()
        sessions = self.rows(store, "sessions")
        self.assertEqual(len(sessions), 1, "归档移动不能被当成第二个会话")
        self.assertEqual(sessions[0]["host_session_id"], "sess-arch")
        self.assertEqual(len(self.activities(store)), before)
        self.assertEqual(second["activities_written"], 0)
        self.assertEqual(second["rewrites"], [])
        self.assertEqual(len(second["files"]), 1)

    # ---- 11. 文件被重写 ----

    def test_rewritten_file_is_rescanned_without_loss_or_duplication(self):
        project = self.project()
        path = self.rollout_path()
        kept = self.user_via_event_msg("2026-09-19T12:00:00.000Z", "第一条消息：保留。")
        long_noise = self.noise("2026-09-19T12:00:30.000Z", "reasoning")
        long_noise["payload"]["summary"] = ["x" * 2000]
        original = [
            self.session_meta("sess-rewrite", str(self.source)),
            kept,
            long_noise,
            self.user_via_event_msg("2026-09-19T12:01:00.000Z", "第二条消息：会被重写挤掉。"),
        ]
        self.write_records(path, original)
        self.enable(project)
        store = self.store_for(project)
        adapter = CodexAdapter(store, self.codex_home, [project])

        adapter.scan()
        self.assertEqual(len(self.activities(store)), 2)
        size_before = path.stat().st_size

        rewritten = [
            self.session_meta("sess-rewrite", str(self.source)),
            kept,
            self.user_via_event_msg("2026-09-19T12:02:00.000Z", "第三条消息：重写后新增。"),
        ]
        self.write_records(path, rewritten)
        self.assertLess(path.stat().st_size, size_before)

        report = adapter.scan()
        self.assertEqual(len(report["rewrites"]), 1, "偏移大于长度必须判定为重写")
        self.assertEqual(report["rewrites"][0]["generation"], 2)
        texts = self.texts(store)
        self.assertEqual(len(texts), 3, "重写后应保留旧内容并补上新内容，且不重复")
        self.assertIn("第一条消息：保留。", texts)
        self.assertIn("第二条消息：会被重写挤掉。", texts)
        self.assertIn("第三条消息：重写后新增。", texts)

    # ---- 12. 跨午夜 ----

    def test_cross_midnight_occurred_at_keeps_real_time(self):
        project = self.project()
        path = self.rollout_path()
        records = [
            self.session_meta("sess-midnight", str(self.source), ts="2026-09-18T23:57:00.000Z"),
            self.user_via_event_msg("2026-09-18T23:58:00.000Z", "午夜前这一条。"),
            self.user_via_event_msg("2026-09-19T00:05:00.000Z", "午夜后这一条。"),
        ]
        self.write_records(path, records)
        self.enable(project, now="2026-09-18T00:00:00.000Z")
        store = self.store_for(project)
        report = CodexAdapter(store, self.codex_home, [project]).scan()
        self.assertEqual(report["activities_written"], 2)

        occurred = sorted(row["occurred_at"] for row in self.activities(store))
        self.assertEqual(
            occurred,
            ["2026-09-18T23:58:00.000Z", "2026-09-19T00:05:00.000Z"],
            "证据时间必须是事件真实时间，不能算到处理当天",
        )


    # ---- 13. 授权排除项 ----

    def test_excluded_paths_in_tool_calls_are_not_ingested(self):
        project = self.project()
        path = self.rollout_path()
        records = [
            self.session_meta("sess-exclude", str(self.source)),
            self.function_call(
                "2026-09-19T13:00:00.000Z", "shell", "call_env", '{"path":"E:/repo/.env"}'
            ),
            self.function_call(
                "2026-09-19T13:01:00.000Z", "shell", "call_pem", '{"path":"certs/server.pem"}'
            ),
            self.function_call(
                "2026-09-19T13:02:00.000Z", "shell", "call_ok", '{"path":"src/pipeline.py"}'
            ),
        ]
        self.write_records(path, records)
        self.enable(project)
        store = self.store_for(project)
        report = CodexAdapter(store, self.codex_home, [project]).scan()

        self.assertEqual(report["activities_written"], 1)
        self.assertEqual(len(report["excluded_by_pattern"]), 2)
        self.assertEqual(
            sorted(item["pattern"] for item in report["excluded_by_pattern"]),
            ["*.pem", ".env"],
        )
        self.assertIn("src/pipeline.py", self.texts(store)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
