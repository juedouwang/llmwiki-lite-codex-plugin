"""Notebook storage, API security, concurrency and compatibility regression tests."""

from __future__ import annotations
import base64
import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_notebook as nb  # noqa: E402
from llmwiki_core import LLMWikiError  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from markdown_renderer import render_markdown  # noqa: E402
from research_records import list_records, read_record, write_record  # noqa: E402
from web_server import create_server  # noqa: E402
from research_web_ui import todos_page, record_view  # noqa: E402

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="


def document(text="观察 **结果**"):
    return {
        "title": "实验记录",
        "tags": ["实验"],
        "blocks": [
            {"id": "block1", "type": "markdown", "text": text, "comments": ["待验证"]}
        ],
    }


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="llmwiki-notebook-")
        self.root = Path(self.tmp.name)
        source = self.root / "source"
        source.mkdir()
        self.home = str(self.root / "home")
        self.project = register_project(
            str(source), home=self.home, wiki_root=str(self.root / "wiki")
        )["project"]
        self.nid = "a" * 32

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, doc=None, rev=""):
        return nb.save(
            self.project, self.nid, {"document": doc or document(), "revision": rev}
        )

    def save_at(self, time, doc=None, rev=""):
        with patch.object(nb, "datetime") as clock:
            clock.now.return_value = datetime.fromisoformat(time)
            clock.fromisoformat.side_effect = datetime.fromisoformat
            return self.save(doc, rev)

    def test_timestamps_preserved_and_changed_by_server(self):
        first = "2026-09-16T10:00:00+08:00"
        second = "2026-09-16T10:01:02+08:00"
        doc = document()
        doc["created_at"] = "forged"
        doc["blocks"][0]["created_at"] = "forged"
        result = self.save_at(first, doc)
        times = result["timestamps"]
        self.assertEqual(
            datetime.fromisoformat(times["created_at"]), datetime.fromisoformat(first)
        )
        self.assertEqual(times["blocks"][0]["created_at"], times["created_at"])
        self.assertEqual(
            nb.load(self.project, self.nid)["document"]["created_at"],
            times["created_at"],
        )
        doc["blocks"][0]["comments"].append("新增批注")
        result = self.save_at(second, doc, result["revision"])
        current = nb.load(self.project, self.nid)["document"]
        self.assertEqual(current["created_at"], times["created_at"])
        self.assertEqual(current["blocks"][0]["created_at"], times["created_at"])
        self.assertEqual(
            datetime.fromisoformat(current["blocks"][0]["updated_at"]),
            datetime.fromisoformat(second),
        )
        raw = nb._path(self.project, self.nid).read_text(encoding="utf-8")
        self.assertIn("updated_at:", raw)
        self.assertNotIn("最近修改：", raw)
        self.assertNotIn("创建：", raw)

    def test_legacy_generated_times_hidden_without_touching_user_text(self):
        user_text = "创建：用户手写的实验时间 · 最近修改：这是实验数据"
        self.save(document(user_text))
        doc = nb.load(self.project, self.nid)["document"]
        line = f"创建：{doc['created_at']} · 最近修改：{doc['updated_at']}"
        body = nb.markdown(doc, self.project["id"])
        body = body.replace("# 实验记录\n", f"# 实验记录\n\n{line}\n", 1)
        body = body.replace(user_text, line + "\n\n" + user_text, 1)
        state = {"document": doc, "body_sha": nb._revision(body.encode("utf-8"))}
        marker = base64.b64encode(json.dumps(state).encode()).decode()
        raw = (body + f"\n<!-- llmwiki-notebook-v1:{marker} -->\n").encode("utf-8")
        path = nb._path(self.project, self.nid)
        path.write_bytes(raw)
        read = read_record(
            self.project["source_root"],
            f"records/manual/{self.nid}.md",
            state_root=self.project["state_root"],
        )
        self.assertNotIn(line, read["record"]["content"])
        self.assertIn(user_text, read["record"]["content"])
        html = render_markdown(
            raw.decode(), self.project["id"], f"records/manual/{self.nid}.md"
        )
        self.assertNotIn(line, html)
        self.assertIn(user_text, html)
        self.assertIn('<details class="frontmatter"><summary>笔记信息', html)
        self.assertNotIn('<details class="frontmatter" open>', html)
        self.assertEqual(path.read_bytes(), raw)
        external = raw.decode().replace(user_text, "外部修改了正文")
        self.assertEqual(nb.reader_markdown(external, self.project["id"]), external)
        result = self.save(doc, nb._revision(raw))
        self.assertNotIn(line, path.read_text(encoding="utf-8"))
        self.assertEqual(result["timestamps"]["created_at"], doc["created_at"])
        self.assertEqual(
            nb.load(self.project, self.nid)["document"]["blocks"][0]["text"], user_text
        )

    def test_unchanged_moved_and_copied_blocks(self):
        first = self.save_at("2026-09-16T10:00:00+08:00")
        doc = nb.load(self.project, self.nid)["document"]
        original = dict(doc["blocks"][0])
        doc["blocks"].insert(0, {**original, "id": "copy"})
        result = self.save_at("2026-09-16T11:00:00+08:00", doc, first["revision"])
        blocks = nb.load(self.project, self.nid)["document"]["blocks"]
        self.assertEqual(blocks[1], original)
        self.assertEqual(blocks[0]["created_at"], result["updated_at"])
        doc["blocks"].reverse()
        self.save_at("2026-09-16T12:00:00+08:00", doc, result["revision"])
        self.assertEqual(
            nb.load(self.project, self.nid)["document"]["blocks"], blocks[::-1]
        )

    def test_legacy_block_times_are_not_invented(self):
        first = self.save_at("2026-09-16T10:00:00+08:00")
        doc = nb.load(self.project, self.nid)["document"]
        for block in doc["blocks"]:
            block.pop("created_at")
            block.pop("updated_at")
        # Simulate a v1 file written before per-block timestamps existed.
        body = nb.markdown(doc, self.project["id"])
        state = {"document": doc, "body_sha": nb._revision(body.encode("utf-8"))}
        marker = base64.b64encode(json.dumps(state).encode()).decode()
        raw = (body + f"\n<!-- llmwiki-notebook-v1:{marker} -->\n").encode("utf-8")
        nb._path(self.project, self.nid).write_bytes(raw)
        loaded = nb.load(self.project, self.nid)
        self.assertIsNone(loaded["document"]["blocks"][0]["created_at"])
        unchanged = self.save_at(
            "2026-09-16T11:00:00+08:00", loaded["document"], loaded["revision"]
        )
        self.assertIsNone(unchanged["timestamps"]["blocks"][0]["updated_at"])
        loaded["document"]["blocks"][0]["text"] = "新修改"
        result = self.save_at(
            "2026-09-16T12:00:00+08:00", loaded["document"], unchanged["revision"]
        )
        self.assertIsNone(result["timestamps"]["blocks"][0]["created_at"])
        self.assertEqual(
            result["timestamps"]["created_at"], first["timestamps"]["created_at"]
        )
        self.assertEqual(
            result["timestamps"]["blocks"][0]["updated_at"], result["updated_at"]
        )

    def test_restoring_history_does_not_roll_back_time(self):
        first = self.save_at("2026-09-16T10:00:00+08:00")
        second = self.save_at(
            "2026-09-16T11:00:00+08:00", document("新内容"), first["revision"]
        )
        old = nb.history(self.project, self.nid, first["revision"])["document"]
        result = self.save_at("2026-09-16T12:00:00+08:00", old, second["revision"])
        restored = nb.load(self.project, self.nid)["document"]
        self.assertEqual(restored["created_at"], old["created_at"])
        self.assertEqual(
            restored["blocks"][0]["created_at"], old["blocks"][0]["created_at"]
        )
        self.assertEqual(restored["blocks"][0]["updated_at"], result["updated_at"])
        self.assertNotEqual(restored["updated_at"], old["updated_at"])

    def test_roundtrip_and_chinese_search(self):
        self.save()
        note = nb.load(self.project, self.nid)
        self.assertEqual(note["document"]["blocks"][0]["comments"], ["待验证"])
        listing = list_records(
            self.project["source_root"],
            state_root=self.project["state_root"],
            query="观察",
        )
        self.assertEqual(listing["count"], 1)
        record = read_record(
            self.project["source_root"],
            listing["records"][0]["id"],
            state_root=self.project["state_root"],
        )
        self.assertNotIn("llmwiki-notebook-v1", record["record"]["content"])
        raw = nb._path(self.project, self.nid).read_text(encoding="utf-8")
        self.assertNotIn(
            "llmwiki-notebook-v1",
            render_markdown(raw, self.project["id"], "records/manual/a.md"),
        )

    def test_history_and_restore(self):
        first = self.save()
        second = self.save(document("新的结果"), first["revision"])
        old = nb.history(self.project, self.nid, first["revision"])["document"]
        self.assertEqual(len(nb.history(self.project, self.nid)["versions"]), 1)
        self.save(old, second["revision"])
        self.assertEqual(
            nb.load(self.project, self.nid)["document"]["blocks"][0]["text"],
            "观察 **结果**",
        )
        self.assertEqual(len(nb.history(self.project, self.nid)["versions"]), 2)

    def test_stale_revision_never_overwrites(self):
        first = self.save()
        self.save(document("获胜的编辑"), first["revision"])
        with self.assertRaises(nb.NotebookConflict):
            self.save(document("旧的编辑"), first["revision"])
        self.assertEqual(
            nb.load(self.project, self.nid)["document"]["blocks"][0]["text"],
            "获胜的编辑",
        )

    def test_external_edits_preserved(self):
        self.save()
        path = nb._path(self.project, self.nid)
        path.write_text(
            path.read_text(encoding="utf-8").replace("# 实验记录", "# 外部修改", 1),
            encoding="utf-8",
        )
        raw = path.read_bytes()
        with self.assertRaises(nb.NotebookConflict):
            nb.load(self.project, self.nid)
        with self.assertRaises(nb.NotebookConflict):
            self.save(rev=nb._revision(raw))
        self.assertEqual(path.read_bytes(), raw)

    def test_external_rewrite_without_marker_is_a_conflict_not_corruption(self):
        """A file rewritten by another program is a conflict with a way out, not damage."""
        self.save()
        path = nb._path(self.project, self.nid)
        path.write_text("# 别的工具重写了这篇笔记\n\n正文还在。\n", encoding="utf-8")
        raw = path.read_bytes()
        with self.assertRaises(nb.NotebookConflict) as caught:
            nb.load(self.project, self.nid)
        self.assertIn("另存为新笔记", str(caught.exception))
        self.assertNotIn("损坏", str(caught.exception))
        self.assertEqual(path.read_bytes(), raw)

    def test_parallel_writers(self):
        first = self.save()

        def update(i):
            try:
                self.save(document(str(i)), first["revision"])
                return True
            except nb.NotebookConflict:
                return False

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(update, range(4))), 1)

    def test_image_dedup_and_markdown(self):
        one = nb.upload(self.project, {"data": PNG})
        self.assertEqual(one, nb.upload(self.project, {"data": PNG}))
        doc = document()
        doc["blocks"].append(
            {"id": "img", "type": "image", "image": one["image"], "text": "截图说明"}
        )
        self.save(doc)
        raw = nb._path(self.project, self.nid).read_text(encoding="utf-8")
        self.assertIn("../assets/" + one["image"], raw)
        self.assertEqual(
            nb.image_path(self.project, one["image"]).read_bytes(),
            base64.b64decode(PNG),
        )

    def test_invalid_images_rejected(self):
        for encoded in (
            "bad!",
            base64.b64encode(b"<svg onload='alert(1)'>").decode(),
            base64.b64encode(b"<html>fake png</html>").decode(),
            "",
        ):
            with self.subTest(encoded=encoded), self.assertRaises(LLMWikiError):
                nb.upload(self.project, {"data": encoded})

    def test_path_traversal_rejected(self):
        for nid in ("../foo", "/foo", "A" * 32, "a" * 33):
            with self.assertRaises(LLMWikiError):
                nb.load(self.project, nid)
        for name in ("../../config.json", "/x.png", "a.svg"):
            with self.assertRaises(LLMWikiError):
                nb.image_path(self.project, name)

    def test_symlink_rejected(self):
        root = Path(self.project["wiki_root"])
        (root / "records").mkdir(exist_ok=True)
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (root / "records/manual").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Windows symlink permission unavailable")
        with self.assertRaises((LLMWikiError, ValueError)):
            self.save()
        self.assertEqual(list(outside.iterdir()), [])

    def test_invalid_blocks_and_limits(self):
        for doc in (
            {"blocks": [None]},
            {"blocks": [{"id": "x", "type": "html"}]},
            {"blocks": [{"id": "x", "type": "markdown"}] * 2},
            {"blocks": [] * 0, "tags": "bad"},
            {"blocks": [], "title": "a" * 201},
        ):
            with self.assertRaises(LLMWikiError):
                nb.validate(doc)
        with self.assertRaises(LLMWikiError):
            self.save(
                {"blocks": [{"id": "img", "type": "image", "image": "b" * 64 + ".png"}]}
            )

    def test_all_block_types_and_xss(self):
        doc = document('<script>alert("x")</script>')
        for kind in ("heading", "code", "callout", "divider"):
            doc["blocks"].append(
                {"id": kind, "type": kind, "text": "some ``` nested ` text"}
            )
        self.save(doc)
        raw = nb._path(self.project, self.nid).read_text(encoding="utf-8")
        self.assertIn("````\nsome", raw)
        self.assertNotIn("<script>", render_markdown(raw, "p", "records/manual/a.md"))

    def test_legacy_untouched(self):
        old = write_record(
            self.project["source_root"],
            state_root=self.project["state_root"],
            title="AI 讨论",
            understanding="原有理解",
        )
        legacy = list((Path(self.project["wiki_root"]) / "records").rglob("*.md"))
        before = {str(p): p.read_bytes() for p in legacy}
        self.save()
        self.assertTrue(old["ok"])
        self.assertEqual(before, {str(p): p.read_bytes() for p in legacy})
        self.assertEqual(
            list_records(
                self.project["source_root"], state_root=self.project["state_root"]
            )["count"],
            2,
        )

    def test_todos_and_record_editor_navigation(self):
        empty = todos_page(self.home, self.project["id"], {})
        self.assertIn("科研进度", empty)
        write_record(
            self.project["source_root"],
            state_root=self.project["state_root"],
            title="既有日档",
            understanding="原文",
            next_steps=["补充夜间场景"],
        )
        self.save()
        todos = todos_page(self.home, self.project["id"], {})
        from research_progress import load
        self.assertEqual(load(self.project)["candidates"][0]["title"], "补充夜间场景")
        self.assertNotIn('id="notebook"', todos)
        editor = record_view(
            self.home, self.project["id"], f"records/manual/{self.nid}.md"
        )
        self.assertIn('id="notebook"', editor)

    def test_http_security_and_routes(self):
        server = create_server(self.home, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        host = f"127.0.0.1:{server.server_port}"
        path = f"/api/project/{self.project['id']}/notebook/{self.nid}"
        headers = {
            "Origin": "http://" + host,
            "X-Notebook-Request": "1",
            "Content-Type": "application/json",
        }

        def call(method, target, body=None, extra=None):
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request(
                method,
                target,
                json.dumps(body) if body is not None else None,
                extra or {},
            )
            response = conn.getresponse()
            result = response.status, response.read().decode()
            conn.close()
            return result

        try:
            payload = {"document": document(), "revision": ""}
            self.assertEqual(call("POST", path, payload)[0], 403)
            self.assertEqual(
                call("POST", path, payload, {**headers, "Origin": "https://evil.test"})[
                    0
                ],
                403,
            )
            self.assertEqual(
                call("POST", path, payload, {**headers, "Host": "evil.test"})[0], 403
            )
            self.assertEqual(call("GET", path, extra={"Host": "evil.test"})[0], 403)
            self.assertEqual(call("POST", path, payload, headers)[0], 200)
            self.assertEqual(call("POST", path, payload, headers)[0], 409)
            status, body = call("GET", path)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["document"]["title"], "实验记录")
            for target in (
                f"/project/{self.project['id']}/records",
                f"/project/{self.project['id']}/notebook/{self.nid}",
                "/static/notebook.js",
                "/static/notebook.css",
            ):
                status, body = call("GET", target)
                self.assertEqual(status, 200)
            preview = path.rsplit("/", 1)[0] + "/preview"
            status, body = call(
                "POST", preview, {"text": "<script>alert(1)</script>"}, headers
            )
            self.assertEqual(status, 200)
            self.assertNotIn("<script>", json.loads(body)["html"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
