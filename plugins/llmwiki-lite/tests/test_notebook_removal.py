"""Deletion races and lossless undo use isolated files, never real research data."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llmwiki_core import LLMWikiError, wiki_list
from llmwiki_registry import register_project
from research_notebook import NotebookConflict, load, remove, save, undo_remove
from research_records import list_records
from web_server import create_server


class NotebookRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="llmwiki-remove-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "source").mkdir()
        self.home = str(self.root / "home")
        self.project = register_project(str(self.root / "source"), name="删除验收", home=self.home,
                                        wiki_root=str(self.root / "wiki"), state_root=str(self.root / "state"))["project"]
        self.note_id = uuid4().hex
        self.document = {"format": "markdown", "title": "原始笔记", "body": "正文 **粗体**", "comments": [], "tags": []}
        self.result = save(self.project, self.note_id, {"revision": "", "document": self.document})
        self.path = self.root / "wiki" / "records" / "manual" / (self.note_id + ".md")

    def test_remove_and_undo_preserve_bytes_timestamps_images_history(self):
        raw, timestamp = self.path.read_bytes(), self.path.stat().st_mtime_ns
        attachment = self.path.parent.parent / "assets" / "image.png"
        attachment.parent.mkdir()
        attachment.write_bytes(b"kept image")
        archive = self.root / "wiki" / ".notebook-history" / self.note_id / "kept.snapshot"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"kept history")
        remove(self.project, self.note_id, self.result)
        self.assertFalse(self.path.exists())
        self.assertEqual(list_records(self.project["source_root"], state_root=self.project["state_root"])["count"], 0)
        pages = wiki_list(self.project["source_root"], state_root=self.project["state_root"])["pages"]
        self.assertFalse(any(".notebook-trash" in page["path"] for page in pages))
        undo_remove(self.project, self.note_id, self.result)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.path.stat().st_mtime_ns, timestamp)
        self.assertEqual(attachment.read_bytes(), b"kept image")
        self.assertEqual(archive.read_bytes(), b"kept history")

    def test_external_change_is_not_deleted(self):
        changed = {**self.document, "body": "另一窗口的新内容"}
        save(self.project, self.note_id, {"revision": self.result["revision"], "document": changed})
        with self.assertRaises(NotebookConflict):
            remove(self.project, self.note_id, self.result)
        self.assertEqual(load(self.project, self.note_id)["document"]["body"], changed["body"])

    def test_undo_never_overwrites_a_recreated_note(self):
        remove(self.project, self.note_id, self.result)
        save(self.project, self.note_id, {"revision": "", "document": {**self.document, "body": "新笔记"}})
        with self.assertRaises(NotebookConflict):
            undo_remove(self.project, self.note_id, self.result)
        self.assertEqual(load(self.project, self.note_id)["document"]["body"], "新笔记")

    def test_rejects_non_manual_id_without_creating_lock_folders(self):
        before = list((self.root / "wiki").rglob("*"))
        for invalid in ["../other", "records/2026/09/20.md", "a" * 31]:
            with self.assertRaises(LLMWikiError):
                remove(self.project, invalid, self.result)
        self.assertEqual(list((self.root / "wiki").rglob("*")), before)

    def test_http_origin_and_conflict(self):
        server = create_server(self.home, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        def post(action, payload, trusted=True):
            request = urllib.request.Request(origin + f'/api/project/{self.project["id"]}/notebook/{self.note_id}/' + action,
                data=json.dumps(payload).encode(), headers={"Origin": origin if trusted else "https://foreign.test",
                    "Content-Type": "application/json", "X-Notebook-Request": "1"})
            try:
                with urllib.request.urlopen(request) as response:
                    return response.status
            except urllib.error.HTTPError as exc:
                return exc.code
        try:
            self.assertEqual(post("delete", self.result, trusted=False), 403)
            self.assertTrue(self.path.exists())
            self.assertEqual(post("delete", {"revision": "old"}), 409)
            self.assertEqual(post("delete", self.result), 200)
            self.assertEqual(post("undo-delete", self.result), 200)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
