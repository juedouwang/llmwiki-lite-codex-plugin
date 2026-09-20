"""真实临时仓库 Git API 验收；不访问真实注册表、身份、项目或网络远端。

运行：python -B -m unittest discover -s plugins/llmwiki-lite/tests -p test_git_web.py
只 mock 预览时钟，不 mock Git 命令或成功结果。
"""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import git_web
from llmwiki_registry import register_project
from web_server import create_server


@unittest.skipUnless(shutil.which("git"), "Git executable required")
class GitWebFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="llmwiki-web-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.template = self.root / "empty-template"
        self.template.mkdir()
        # 清除继承的仓库/索引、身份、配置注入及认证命令；只用临时本地配置。
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("GIT_", "SSH_")) and k.upper() != "EMAIL"}
        env.update({
            "HOME": str(self.home), "USERPROFILE": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "xdg"),
            "CODEX_HOME": str(self.home / "codex"), "LLMWIKI_HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TEMPLATE_DIR": str(self.template),
            "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
            "GIT_ALLOW_PROTOCOL": "file", "LC_ALL": "C", "LANG": "C",
        })
        environment = patch.dict(os.environ, env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.repo, self.project = self.new_project("primary")
        self.pid = self.project["id"]
        self.preview_ids = []
        self.addCleanup(self.clear_previews)

    def clear_previews(self):
        with git_web.PREVIEW_LOCK:
            for preview_id in self.preview_ids:
                git_web.PREVIEWS.pop(preview_id, None)

    def git(self, *args, repo=None, check=True, binary=False):
        result = subprocess.run(
            [shutil.which("git"), *args], cwd=repo or self.repo,
            capture_output=True, text=not binary,
            encoding=None if binary else "utf-8", timeout=30, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if check and result.returncode:
            self.fail(f"Fixture Git failed {args!r}: {result.stderr!r}")
        return result if not check else result.stdout

    def configure(self, repo):
        for key, value in (("user.name", "API Fixture"), ("user.email", "api@example.invalid"),
                           ("core.autocrlf", "false"), ("core.quotepath", "false")):
            self.git("config", "--local", key, value, repo=repo)

    def new_project(self, name):
        repo = self.root / name
        repo.mkdir()
        self.git("init", "--initial-branch=main", repo=repo)
        self.configure(repo)
        project = register_project(
            str(repo), name=name, home=str(self.home), select=False,
            state_root=str(self.root / (name + "-state")),
            wiki_root=str(self.root / (name + "-wiki")),
        )["project"]
        for key in ("state_root", "wiki_root"):
            self.assertFalse(Path(project[key]).is_relative_to(repo))
        return repo, project

    def write(self, path, content, repo=None):
        target = (repo or self.repo) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return target

    def commit(self, changes, message="fixture", repo=None):
        repo = repo or self.repo
        for path, content in changes.items():
            if content is None:
                (repo / path).unlink()
            else:
                self.write(path, content, repo)
        self.git("add", "-A", repo=repo)
        self.git("commit", "-m", message, repo=repo)
        return self.git("rev-parse", "HEAD", repo=repo).strip()

    def head(self, repo=None):
        return self.git("rev-parse", "HEAD", repo=repo).strip()

    def api(self, endpoint, data=None, *, method="POST", status=200, code=None, pid=None):
        payload, actual = git_web.dispatch(str(self.home), pid or self.pid, method, endpoint, data)
        self.assertEqual(actual, status, f"{method} {endpoint}: {payload!r}")
        self.assertEqual(payload.get("ok"), status == 200, payload)
        if code is not None:
            self.assertEqual(payload.get("code"), code, payload)
        return payload

    def preview(self, action, params=None, **kwargs):
        result = self.api("preview", {"action": action, "params": params or {}}, **kwargs)
        if result.get("preview_id"):
            self.preview_ids.append(result["preview_id"])
            self.assertTrue(result["can_execute"], result)
        return result

    def execute(self, preview, **values):
        return self.api("execute", {"preview_id": preview["preview_id"], **values})

    def perform(self, action, params=None, **values):
        return self.execute(self.preview(action, params), **values)

    def file_ids(self, preview, *paths):
        files = {f["path"]: f["file_id"] for f in preview["files"]}
        self.assertTrue(set(paths) <= set(files), files)
        return [files[path] for path in paths]

    def save_data(self, preview, *paths):
        return {"preview_id": preview["preview_id"],
                "file_ids": self.file_ids(preview, *paths), "message": "网页保存 测试"}

    def assert_clean(self):
        self.assertEqual(self.git("status", "--porcelain", "-z", binary=True), b"")

    def conflict(self, *, external=False, binary=False):
        base = b"base\0value\n" if binary else "base\n"
        current = b"main\0value\n" if binary else "main\n"
        incoming = b"side\0value\n" if binary else "side\n"
        self.commit({"conflict.txt": base, "keep.txt": "keep\n"}, "base")
        self.git("switch", "-c", "feature")
        source = self.commit({"conflict.txt": incoming}, "feature")
        self.git("switch", "main")
        before = self.commit({"conflict.txt": current}, "main")
        if external:
            result = self.git("merge", "feature", "-m", "external merge", check=False)
            self.assertNotEqual(result.returncode, 0)
        else:
            result = self.perform("merge", {"source_branch": "feature"})
            self.assertEqual(result["outcome"], "conflicts", result)
        view = self.api("merge", method="GET")
        return before, source, view

    def resolve(self, view, resolution="manual", content="resolved\n", **kwargs):
        file = view["files"][0]
        return self.api("merge/resolve", {
            "operation_id": view["operation_id"], "expected_revision": file["revision"],
            "file_id": file["file_id"], "resolution": resolution, "content": content,
        }, **kwargs)

    def finish(self, view, action="complete", **kwargs):
        return self.api("merge/" + action, {
            "operation_id": view["operation_id"], "expected_revision": view["expected_revision"],
        }, **kwargs)

    def remote_fixture(self):
        base = self.commit({"tracked.txt": "base\n"})
        bare = self.root / "remote.git"
        self.git("init", "--bare", "--initial-branch=main", str(bare))
        self.git("remote", "add", "origin", str(bare))
        self.git("push", "origin", "main")
        peer = self.root / "peer"
        self.git("clone", str(bare), str(peer))
        self.configure(peer)
        remote_id = self.api("status", method="GET")["remotes"][0]["remote_id"]
        return base, bare, peer, remote_id


class GitWebSaveTests(GitWebFixture):
    def test_selected_whole_file_preserves_unselected_index(self):
        self.commit({"selected.txt": "base\n", "other.txt": "original\n"})
        self.write("selected.txt", "staged selected\n")
        self.write("other.txt", "staged other\n")
        self.git("add", "--", "selected.txt", "other.txt")
        other_entry = self.git("ls-files", "--stage", "--", "other.txt")
        self.write("selected.txt", "final selected\n")
        self.write("other.txt", "unstaged other\n")
        preview = self.preview("save")
        index = (self.repo / ".git/index").read_bytes()
        diff = self.api("diff", {"kind": "worktree", "preview_id": preview["preview_id"],
                               "file_id": self.file_ids(preview, "selected.txt")[0]}, method="GET")
        self.assertIn("final selected", diff["text"])
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        result = self.api("execute", self.save_data(preview, "selected.txt"))
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(result["commit_oid"], self.head())
        self.assertEqual(self.git("show", "HEAD:selected.txt"), "final selected\n")
        self.assertEqual(self.git("show", "HEAD:other.txt"), "original\n")
        self.assertEqual(self.git("ls-files", "--stage", "--", "other.txt"), other_entry)
        self.assertEqual((self.repo / "other.txt").read_text(), "unstaged other\n")
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "other.txt")

    def test_first_commit_excludes_other_staged_file(self):
        self.write("首个 文件.txt", "first\n")
        self.write("not-selected.txt", "private fixture\n")
        self.git("add", "--", "not-selected.txt")
        other_entry = self.git("ls-files", "--stage", "--", "not-selected.txt")
        self.assertTrue(self.api("status", method="GET")["head"]["unborn"])
        preview = self.preview("save")
        result = self.api("execute", self.save_data(preview, "首个 文件.txt"))
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(self.git("show", "-s", "--format=%P", "HEAD").strip(), "")
        self.assertEqual(self.git("ls-tree", "--name-only", "HEAD").splitlines(), ["首个 文件.txt"])
        self.assertEqual(self.git("ls-files", "--stage", "--", "not-selected.txt"), other_entry)

    def test_add_delete_rename_unicode_spaces_and_literal_paths(self):
        self.commit({"旧 名字.txt": "rename contents\n" * 4,
                     "删除 文件.txt": "remove\n", "keep.txt": "unchanged\n"})
        self.git("mv", "--", "旧 名字.txt", "新 名字.txt")
        (self.repo / "删除 文件.txt").unlink()
        self.write("新增 目录/中文 空格.txt", "new\n")
        self.write("-literal.txt", "dash\n")
        self.write("[literal].txt", "brackets\n")
        preview = self.preview("save")
        renamed = next(f for f in preview["files"] if f["path"] == "新 名字.txt")
        self.assertEqual(renamed["old_path"], "旧 名字.txt")
        result = self.api("execute", self.save_data(
            preview, "新 名字.txt", "删除 文件.txt", "新增 目录/中文 空格.txt",
            "-literal.txt", "[literal].txt"))
        self.assertEqual(result["outcome"], "done", result)
        paths = self.git("ls-tree", "-r", "--name-only", "-z", "HEAD", binary=True).decode().split("\0")
        self.assertEqual(set(filter(None, paths)),
                         {"新 名字.txt", "新增 目录/中文 空格.txt", "-literal.txt", "[literal].txt", "keep.txt"})
        self.assert_clean()
        meta = self.api("commit/" + self.head(), method="GET")
        self.assertEqual(len(meta["parents"]), 1)
        self.assertEqual(next(f for f in meta["files"] if f["path"] == "新 名字.txt")["old_path"], "旧 名字.txt")

    def test_already_staged_deletion_can_be_saved_alone(self):
        self.commit({"删除 文件.txt": "remove\n", "keep.txt": "keep\n"})
        self.git("rm", "--", "删除 文件.txt")
        preview = self.preview("save")
        result = self.api("execute", self.save_data(preview, "删除 文件.txt"))
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(self.git("ls-tree", "--name-only", "HEAD").splitlines(), ["keep.txt"])
        self.assert_clean()

    def test_more_than_one_hundred_files_are_not_silently_omitted(self):
        paths = [f"目录/file {i:03d}.txt" for i in range(105)]
        for path in paths:
            self.write(path, path + "\n")
        preview = self.preview("save")
        self.assertEqual(len(preview["files"]), 105)
        result = self.api("execute", self.save_data(preview, *paths))
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(len(self.git("ls-tree", "-r", "--name-only", "HEAD").splitlines()), 105)
        self.assert_clean()

    def test_changed_selected_content_even_same_size_and_mtime_is_rejected(self):
        self.commit({"a.txt": "base\n"})
        file = self.write("a.txt", "first\n")
        preview = self.preview("save")
        old = file.stat()
        file.write_bytes(b"other\n")
        os.utime(file, ns=(old.st_atime_ns, old.st_mtime_ns))
        head, index = self.head(), (self.repo / ".git/index").read_bytes()
        self.api("execute", self.save_data(preview, "a.txt"), status=409, code="state_changed")
        self.assertEqual(self.head(), head)
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual(file.read_bytes(), b"other\n")
        self.api("execute", self.save_data(preview, "a.txt"), status=409, code="preview_expired")

    def test_unselected_worktree_change_does_not_get_committed(self):
        self.commit({"a.txt": "base\n", "b.txt": "base\n"})
        self.write("a.txt", "selected\n")
        self.write("b.txt", "old unsaved\n")
        preview = self.preview("save")
        self.write("b.txt", "new unsaved\n")
        result = self.api("execute", self.save_data(preview, "a.txt"))
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(self.git("show", "HEAD:b.txt"), "base\n")
        self.assertEqual((self.repo / "b.txt").read_text(), "new unsaved\n")

    def test_changed_index_invalidates_preview_without_overwrite(self):
        self.commit({"a.txt": "base\n", "b.txt": "base\n"})
        self.write("a.txt", "selected\n")
        preview = self.preview("save")
        self.write("b.txt", "external staged\n")
        self.git("add", "b.txt")
        index, head = (self.repo / ".git/index").read_bytes(), self.head()
        self.api("execute", self.save_data(preview, "a.txt"), status=409, code="state_changed")
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual(self.head(), head)

    def test_expiry_and_successful_preview_consumption(self):
        self.write("first.txt", "first\n")
        preview = self.preview("save")
        with patch.object(git_web.time, "monotonic", return_value=time.monotonic() + 301):
            self.api("execute", self.save_data(preview, "first.txt"), status=409, code="preview_expired")
        self.assertTrue(self.api("status", method="GET")["head"]["unborn"])
        fresh = self.preview("save")
        result = self.api("execute", self.save_data(fresh, "first.txt"))
        self.assertEqual(result["outcome"], "done", result)
        before = self.head()
        self.api("execute", self.save_data(fresh, "first.txt"), status=409, code="preview_expired")
        self.assertEqual(self.head(), before)

    def test_cross_project_tokens_and_file_ids_are_not_authorized(self):
        other_repo, other = self.new_project("other")
        self.write("same.txt", "one\n")
        self.write("same.txt", "two\n", other_repo)
        first = self.preview("save")
        second = self.preview("save", pid=other["id"])
        self.api("execute", self.save_data(first, "same.txt"), pid=other["id"],
                 status=409, code="preview_expired")
        self.api("execute", {"preview_id": second["preview_id"],
                            "file_ids": self.file_ids(first, "same.txt"), "message": "bad"},
                 pid=other["id"], status=400, code="invalid_request")
        self.assertTrue(self.api("status", method="GET", pid=other["id"])["head"]["unborn"])
        self.assertEqual(self.api("execute", self.save_data(first, "same.txt"))["outcome"], "done")
        self.api("execute", self.save_data(second, "same.txt"), pid=other["id"],
                 status=409, code="preview_expired")

    def test_forged_file_id_and_extra_fields_are_rejected(self):
        self.write("file.txt", "data\n")
        self.preview("save", {"path": "../outside"}, status=400, code="invalid_request")
        preview = self.preview("save")
        self.api("execute", {"preview_id": preview["preview_id"], "file_ids": ["../outside"],
                            "message": "bad"}, status=400, code="invalid_request")
        self.assertTrue(self.api("status", method="GET")["head"]["unborn"])


class GitWebBranchTests(GitWebFixture):
    def test_create_branch_does_not_switch_or_touch_dirty_worktree(self):
        old = self.commit({"a.txt": "old\n"})
        current = self.commit({"a.txt": "current\n"})
        self.write("a.txt", "unsaved\n")
        self.write("untracked.txt", "unsaved too\n")
        index = (self.repo / ".git/index").read_bytes()
        result = self.perform("create_branch", {"target_oid": old}, name="研究/旧版本")
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(self.head(), current)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD").strip(), "main")
        self.assertEqual(self.git("rev-parse", "refs/heads/研究/旧版本").strip(), old)
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual((self.repo / "a.txt").read_text(), "unsaved\n")
        self.assertTrue((self.repo / "untracked.txt").exists())

    def test_dirty_switch_rejects_staged_unstaged_and_untracked_files(self):
        before = self.commit({"a.txt": "base\n"})
        self.git("branch", "other")
        for kind in ("unstaged", "staged", "untracked"):
            with self.subTest(kind=kind):
                if kind == "untracked":
                    self.git("restore", "--staged", "--worktree", "a.txt")
                    self.write("new.txt", "new\n")
                else:
                    self.write("a.txt", kind + "\n")
                    if kind == "staged":
                        self.git("add", "a.txt")
                index = (self.repo / ".git/index").read_bytes()
                self.preview("switch_branch", {"branch": "other"}, status=409, code="dirty_worktree")
                self.assertEqual(self.head(), before)
                self.assertEqual(self.git("symbolic-ref", "--short", "HEAD").strip(), "main")
                self.assertEqual((self.repo / ".git/index").read_bytes(), index)
                self.assertEqual(self.git("stash", "list"), "")

    def test_clean_switch_and_dirty_after_preview_refusal(self):
        self.commit({"a.txt": "base\n"})
        self.git("branch", "other")
        preview = self.preview("switch_branch", {"branch": "other"})
        self.write("untracked.txt", "not discarded\n")
        self.api("execute", {"preview_id": preview["preview_id"]}, status=409, code="dirty_worktree")
        self.assertTrue((self.repo / "untracked.txt").exists())
        (self.repo / "untracked.txt").unlink()
        self.assertEqual(self.perform("switch_branch", {"branch": "other"})["outcome"], "done")
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD").strip(), "other")

    def test_non_head_ref_change_invalidates_operation_preview(self):
        old = self.commit({"a.txt": "old\n"})
        current = self.commit({"a.txt": "new\n"})
        self.git("branch", "other", current)
        preview = self.preview("create_branch", {"target_oid": old})
        self.git("update-ref", "refs/heads/other", old)
        self.api("execute", {"preview_id": preview["preview_id"], "name": "not-created"},
                 status=409, code="state_changed")
        self.assertEqual(self.head(), current)
        self.assertNotEqual(self.git("show-ref", "--verify", "refs/heads/not-created", check=False).returncode, 0)

    def test_merge_fast_forward_and_already_contained(self):
        self.commit({"base.txt": "base\n"})
        self.git("switch", "-c", "feature")
        source = self.commit({"feature.txt": "feature\n"})
        self.git("switch", "main")
        self.git("config", "merge.ff", "false")
        result = self.perform("merge", {"source_branch": "feature"})
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(self.head(), source)
        self.assertEqual(self.perform("merge", {"source_branch": "feature"})["outcome"], "no_change")
        self.assertEqual(self.head(), source)
        self.assert_clean()

    def test_diverged_merge_has_two_real_parents_and_retains_source(self):
        self.commit({"base.txt": "base\n"})
        self.git("switch", "-c", "feature")
        source = self.commit({"feature.txt": "feature\n"})
        self.git("switch", "main")
        before = self.commit({"main.txt": "main\n"})
        self.git("config", "pull.rebase", "true")
        self.git("config", "merge.ff", "only")
        result = self.perform("merge", {"source_branch": "feature"})
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(self.git("show", "-s", "--format=%P", "HEAD").split(), [before, source])
        self.assertEqual(self.git("rev-parse", "feature").strip(), source)
        self.assertEqual(self.git("show", "HEAD:feature.txt"), "feature\n")
        self.assertEqual(self.git("show", "HEAD:main.txt"), "main\n")
        self.assert_clean()

    def test_restore_preserves_history_parent_and_exact_target_tree(self):
        target = self.commit({"修改.txt": "old\n", "恢复.txt": "restore\n"})
        before = self.commit({"修改.txt": "new\n", "恢复.txt": None, "remove.txt": "remove\n"})
        preview = self.preview("restore", {"target_oid": target})
        self.assertEqual({f["path"] for f in preview["files"]}, {"修改.txt", "恢复.txt", "remove.txt"})
        diff = self.api("diff", {"kind": "restore", "preview_id": preview["preview_id"],
                               "file_id": self.file_ids(preview, "修改.txt")[0]}, method="GET")
        self.assertIn("-new", diff["text"])
        self.assertIn("+old", diff["text"])
        result = self.execute(preview)
        self.assertEqual(result["outcome"], "done", result)
        self.assertNotEqual(self.head(), target)
        self.assertEqual(self.git("show", "-s", "--format=%P", "HEAD").split(), [before])
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), self.git("rev-parse", target + "^{tree}"))
        self.assertEqual(self.git("merge-base", "--is-ancestor", before, "HEAD", check=False).returncode, 0)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD").strip(), "main")
        self.assert_clean()
        self.assertEqual(self.perform("restore", {"target_oid": target})["outcome"], "no_change")

    def test_ignored_file_collision_blocks_restore_without_overwriting(self):
        self.commit({".gitignore": "ignored.txt\n", "anchor.txt": "base\n"})
        self.write("ignored.txt", "historical\n")
        self.git("add", "-f", "ignored.txt")
        self.git("commit", "-m", "historical ignored path")
        historical = self.head()
        self.git("rm", "ignored.txt")
        self.git("commit", "-m", "remove ignored path")
        self.write("ignored.txt", "local ignored, preserve\n")
        before = self.head()
        self.preview("restore", {"target_oid": historical}, status=409, code="dirty_worktree")
        self.assertEqual(self.head(), before)
        self.assertEqual((self.repo / "ignored.txt").read_text(), "local ignored, preserve\n")


class GitWebConflictTests(GitWebFixture):
    def complete_resolution(self, resolution, expected):
        before, source, view = self.conflict()
        self.assertFalse(view["external"])
        self.assertTrue(view["files"][0]["supported"])
        self.assertEqual(view["files"][0]["base"], "base\n")
        self.finish(view, status=422, code="unsupported_conflict")
        self.resolve(view, resolution, "manual result\n")
        self.resolve(view, resolution, "manual result\n", status=409, code="state_changed")
        refreshed = self.api("merge", method="GET")
        self.assertTrue(refreshed["files"][0]["resolved"])
        self.assertEqual(refreshed["files"][0]["content"], expected)
        result = self.finish(refreshed)
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(self.git("show", "-s", "--format=%P", "HEAD").split(), [before, source])
        self.assertEqual(self.git("show", "HEAD:conflict.txt"), expected)
        self.assertIsNone(self.api("merge", method="GET")["operation_id"])
        self.assert_clean()

    def test_current_resolution_complete_and_refresh(self):
        self.complete_resolution("current", "main\n")

    def test_incoming_resolution_complete_and_refresh(self):
        self.complete_resolution("incoming", "side\n")

    def test_manual_resolution_complete_and_refresh(self):
        self.complete_resolution("manual", "manual result\n")

    def test_abort_restores_original_tree_and_preserves_new_untracked_file(self):
        before, _, view = self.conflict()
        self.resolve(view)
        self.write("untracked.txt", "do not remove\n")
        result = self.finish(self.api("merge", method="GET"), "abort")
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.git("write-tree").strip(), self.git("rev-parse", before + "^{tree}").strip())
        self.assertEqual(self.git("diff", "HEAD", "--"), "")
        self.assertEqual((self.repo / "untracked.txt").read_text(), "do not remove\n")
        self.assertFalse((self.repo / ".git/MERGE_HEAD").exists())

    def test_external_tracked_edit_blocks_resolve_complete_and_abort(self):
        before, _, view = self.conflict()
        self.write("keep.txt", "external unsaved\n")
        index = (self.repo / ".git/index").read_bytes()
        conflicted = (self.repo / "conflict.txt").read_bytes()
        self.resolve(view, status=409, code="state_changed")
        for action in ("complete", "abort"):
            self.finish(view, action, status=409, code="state_changed")
        self.assertEqual(self.head(), before)
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual((self.repo / "conflict.txt").read_bytes(), conflicted)
        self.assertEqual((self.repo / "keep.txt").read_text(), "external unsaved\n")
        self.assertTrue((self.repo / ".git/MERGE_HEAD").exists())

    def test_external_staged_addition_never_enters_merge_commit(self):
        before, _, view = self.conflict()
        self.resolve(view)
        refreshed = self.api("merge", method="GET")
        self.write("external.txt", "external staged\n")
        self.git("add", "external.txt")
        index = (self.repo / ".git/index").read_bytes()
        for action in ("complete", "abort"):
            self.finish(refreshed, action, status=409, code="state_changed")
        self.assertEqual(self.head(), before)
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual(self.git("show", ":external.txt"), "external staged\n")

    def test_external_merge_cannot_be_resolved_completed_or_aborted(self):
        before, _, view = self.conflict(external=True)
        self.assertTrue(view["external"])
        self.assertIsNone(view["operation_id"])
        index = (self.repo / ".git/index").read_bytes()
        for action in ("complete", "abort"):
            self.api("merge/" + action, {"operation_id": "foreign", "expected_revision": "foreign"},
                     status=409, code="operation_in_progress")
        self.api("merge/resolve", {"operation_id": "foreign", "expected_revision": "foreign",
                                  "file_id": "foreign", "resolution": "manual", "content": "new"},
                 status=409, code="operation_in_progress")
        self.preview("save", status=409, code="operation_in_progress")
        self.assertEqual(self.head(), before)
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)

    def test_marker_nul_and_oversized_manual_resolution_are_rejected(self):
        _, _, view = self.conflict()
        original = (self.repo / "conflict.txt").read_bytes()
        for content, status, code in (
            ("<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n", 422, "unsupported_conflict"),
            ("text\0binary", 400, "invalid_request"),
            ("x" * (1024 * 1024 + 1), 400, "invalid_request"),
        ):
            with self.subTest(code=code, size=len(content)):
                self.resolve(view, content=content, status=status, code=code)
                self.assertEqual((self.repo / "conflict.txt").read_bytes(), original)
        self.assertFalse(self.api("merge", method="GET")["files"][0]["resolved"])

    def test_binary_conflict_is_read_only_but_can_abort(self):
        before, _, view = self.conflict(binary=True)
        self.assertFalse(view["files"][0]["supported"])
        self.resolve(view, status=422, code="unsupported_conflict")
        self.finish(view, status=422, code="unsupported_conflict")
        self.assertEqual(self.finish(view, "abort")["outcome"], "done")
        self.assertEqual(self.head(), before)
        self.assert_clean()

    def test_external_symlink_replacement_is_not_read_or_overwritten(self):
        secret = self.root / "fixture-outside-conflict.txt"
        secret.write_text("fixture-private-outside-data", encoding="utf-8")
        probe = self.repo / "probe-link"
        try:
            probe.symlink_to(secret)
        except OSError as exc:
            self.skipTest(f"Symlink creation unavailable: {exc.__class__.__name__}")
        probe.unlink()
        before, _, view = self.conflict()
        file = self.repo / "conflict.txt"
        file.unlink()
        file.symlink_to(secret)
        refreshed = self.api("merge", method="GET")
        self.assertNotIn("fixture-private-outside-data", json.dumps(refreshed))
        self.resolve(view, status=409, code="state_changed")
        self.finish(view, "abort", status=409, code="state_changed")
        self.assertTrue(file.is_symlink())
        self.assertEqual(secret.read_text(), "fixture-private-outside-data")
        self.assertEqual(self.head(), before)


class GitWebRemoteTests(GitWebFixture):
    def test_fetch_is_explicit_preserves_dirty_files_and_pull_requires_clean(self):
        base, _, peer, remote = self.remote_fixture()
        source = self.commit({"remote.txt": "remote version\n"}, repo=peer)
        self.git("push", "origin", "main", repo=peer)
        self.write("tracked.txt", "dirty local\n")
        self.write("untracked.txt", "uncommitted\n")
        index = (self.repo / ".git/index").read_bytes()
        fetched = self.api("fetch", {"remote_id": remote, "branch": "main"})
        self.assertEqual(fetched["fetched_oid"], source)
        self.assertEqual((fetched["ahead"], fetched["behind"], fetched["relation"]), (0, 1, "behind"))
        self.assertEqual(self.head(), base)
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual((self.repo / "tracked.txt").read_text(), "dirty local\n")
        self.assertFalse((self.repo / "remote.txt").exists())
        self.assertFalse((self.repo / ".git/FETCH_HEAD").exists())
        params = {"remote_id": remote, "branch": "main", "fetched_oid": source}
        self.preview("pull_apply", params, status=409, code="dirty_worktree")
        self.git("restore", "tracked.txt")
        (self.repo / "untracked.txt").unlink()
        result = self.perform("pull_apply", params)
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(self.head(), source)
        self.assert_clean()

    def test_diverged_pull_merges_fixed_fetched_commit_without_rebase(self):
        _, _, peer, remote = self.remote_fixture()
        source = self.commit({"remote.txt": "remote\n"}, repo=peer)
        self.git("push", "origin", "main", repo=peer)
        before = self.commit({"local.txt": "local\n"})
        self.git("config", "pull.rebase", "true")
        fetched = self.api("fetch", {"remote_id": remote, "branch": "main"})
        self.assertEqual(fetched["relation"], "diverged")
        result = self.perform("pull_apply", {"remote_id": remote, "branch": "main", "fetched_oid": source})
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(self.git("show", "-s", "--format=%P", "HEAD").split(), [before, source])
        self.assertEqual(self.git("show", "HEAD:local.txt"), "local\n")
        self.assertEqual(self.git("show", "HEAD:remote.txt"), "remote\n")
        self.assertEqual(self.git("ls-remote", "origin", "refs/heads/main").split()[0], source)

    def test_push_only_saved_head_not_dirty_files_other_branches_or_tags(self):
        _, bare, _, remote = self.remote_fixture()
        saved = self.commit({"saved.txt": "saved\n"})
        self.git("branch", "not-uploaded")
        self.git("tag", "not-uploaded-tag")
        self.write("tracked.txt", "staged dirty\n")
        self.git("add", "tracked.txt")
        self.write("tracked.txt", "unstaged dirty\n")
        self.write("untracked.txt", "not uploaded\n")
        index = (self.repo / ".git/index").read_bytes()
        result = self.perform("push", {"remote_id": remote, "target_branch": "main"})
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(self.git("rev-parse", "refs/heads/main", repo=bare).strip(), saved)
        self.assertEqual(self.git("show", "main:tracked.txt", repo=bare), "base\n")
        self.assertEqual(set(self.git("ls-tree", "-r", "--name-only", "main", repo=bare).splitlines()),
                         {"tracked.txt", "saved.txt"})
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)", repo=bare).splitlines(), ["refs/heads/main"])
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual((self.repo / "tracked.txt").read_text(), "unstaged dirty\n")
        self.assertTrue((self.repo / "untracked.txt").exists())
        self.assertEqual(self.git("config", "branch.main.remote").strip(), "origin")

    def test_push_never_forces_over_remote_divergence_or_sets_upstream_on_failure(self):
        _, bare, peer, remote = self.remote_fixture()
        remote_tip = self.commit({"remote.txt": "remote\n"}, repo=peer)
        self.git("push", "origin", "main", repo=peer)
        local_tip = self.commit({"local.txt": "local\n"})
        preview = self.preview("push", {"remote_id": remote, "target_branch": "main"})
        self.api("execute", {"preview_id": preview["preview_id"]}, status=500, code="git_failed")
        self.assertEqual(self.git("rev-parse", "main", repo=bare).strip(), remote_tip)
        self.assertEqual(self.head(), local_tip)
        self.assertNotEqual(self.git("config", "--get", "branch.main.remote", check=False).returncode, 0)

    def test_fetch_only_requested_branch_no_tags_and_stale_pull_is_rejected(self):
        _, _, peer, remote = self.remote_fixture()
        first = self.commit({"remote.txt": "one\n"}, repo=peer)
        self.git("branch", "unrequested", repo=peer)
        self.git("tag", "unrequested-tag", repo=peer)
        self.git("push", "--all", "origin", repo=peer)
        self.git("push", "--tags", "origin", repo=peer)
        self.api("fetch", {"remote_id": remote, "branch": "main"})
        preview = self.preview("pull_apply", {"remote_id": remote, "branch": "main", "fetched_oid": first})
        second = self.commit({"remote.txt": "two\n"}, repo=peer)
        self.git("push", "origin", "main", repo=peer)
        self.assertEqual(self.api("fetch", {"remote_id": remote, "branch": "main"})["fetched_oid"], second)
        self.api("execute", {"preview_id": preview["preview_id"]}, status=409, code="state_changed")
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)", "refs/remotes").splitlines(), ["refs/remotes/origin/main"])
        self.assertEqual(self.git("tag", "--list"), "")

    def test_case_sensitive_branch_and_remote_names_preserve_upstream(self):
        _, bare, _, _ = self.remote_fixture()
        self.git("remote", "rename", "origin", "OriginCase")
        self.git("branch", "-m", "FeatureCase")
        self.git("config", "branch.FeatureCase.remote", "OriginCase")
        self.git("config", "branch.FeatureCase.merge", "refs/heads/main")
        remote = self.api("status", method="GET")["remotes"][0]["remote_id"]
        status = self.api("status", method="GET")
        self.assertIsNotNone(status["upstream"])
        self.assertEqual(status["upstream"]["remote_id"], remote)
        self.assertEqual(status["upstream"]["branch"], "main")
        saved = self.commit({"local.txt": "case aware\n"})
        result = self.perform("push", {"remote_id": remote, "target_branch": "different-target"})
        self.assertEqual(result["outcome"], "done", result)
        self.assertEqual(self.git("rev-parse", "refs/heads/different-target", repo=bare).strip(), saved)
        self.assertEqual(self.git("config", "branch.FeatureCase.merge").strip(), "refs/heads/main")
        self.assertEqual(self.api("status", method="GET")["upstream"]["branch"], "main")


    def test_changed_remote_url_after_preview_cannot_redirect_push(self):
        before, original, _, remote = self.remote_fixture()
        self.commit({"saved.txt": "must not be redirected\n"})
        alternate = self.root / "alternate.git"
        self.git("init", "--bare", "--initial-branch=main", str(alternate))
        preview = self.preview("push", {"remote_id": remote, "target_branch": "main"})
        self.git("remote", "set-url", "origin", str(alternate))
        self.api("execute", {"preview_id": preview["preview_id"]}, status=409, code="state_changed")
        self.assertEqual(self.git("for-each-ref", repo=alternate), "")
        self.assertEqual(self.git("rev-parse", "refs/heads/main", repo=original).strip(), before)

    def test_real_loopback_401_fetch_reports_auth_required_without_prompts(self):
        requests = []

        class Unauthorized(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                requests.append(self.path)
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="fixture"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Unauthorized)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            before = self.commit({"a.txt": "base\n"})
            url = f"http://127.0.0.1:{server.server_port}/fixture.git"
            self.git("remote", "add", "origin", url)
            remote = self.api("status", method="GET")["remotes"][0]["remote_id"]
            # 唯一允许 HTTP 的测试，目的地址固定为本测试拥有的 loopback 401 服务。
            with patch.dict(os.environ, {"GIT_ALLOW_PROTOCOL": "file:http",
                                         "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
                result = self.api("fetch", {"remote_id": remote, "branch": "main"},
                                  status=422, code="auth_required")
            self.assertTrue(requests, "Expected a real local HTTP authentication failure")
            self.assertNotIn(url, json.dumps(result))
            self.assertEqual(self.head(), before)
            self.assertEqual(self.git("for-each-ref", "refs/remotes"), "")
            self.assert_clean()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())


class GitWebPolicyTests(GitWebFixture):
    def test_missing_identity_requires_explicit_local_identity_only(self):
        self.git("config", "--unset", "user.name")
        self.git("config", "--unset", "user.email")
        self.write("first.txt", "data\n")
        initial = self.preview("save")
        selected = self.save_data(initial, "first.txt")
        self.api("execute", selected, status=422, code="identity_required")
        self.assertEqual(selected, self.save_data(initial, "first.txt"))
        self.assertFalse((self.repo / ".git/index").exists())
        self.api("identity", {"name": "测试作者", "email": "test@example.invalid", "scope": "global"},
                 status=400, code="invalid_request")
        self.api("identity", {"name": "测试作者", "email": "test@example.invalid", "scope": "local"})
        self.assertEqual(self.git("config", "--local", "user.name").strip(), "测试作者")
        self.assertFalse((self.home / ".gitconfig").exists())
        preview = self.preview("save")
        self.assertEqual(self.api("execute", self.save_data(preview, "first.txt"))["outcome"], "done")
        self.assertEqual(self.git("show", "-s", "--format=%an <%ae>", "HEAD").strip(), "测试作者 <test@example.invalid>")

    def test_custom_hooks_are_not_run_removed_or_silently_disabled(self):
        self.write("first.txt", "data\n")
        marker = self.repo / "hook-ran.txt"
        hook = self.repo / ".git/hooks/pre-commit"
        hook.parent.mkdir(exist_ok=True)
        original = b"#!/bin/sh\nprintf ran > hook-ran.txt\n"
        hook.write_bytes(original)
        hook.chmod(0o755)
        self.preview("save", status=409, code="unsupported_repo")
        self.assertFalse(marker.exists())
        self.assertEqual(hook.read_bytes(), original)
        self.assertTrue(self.api("status", method="GET")["head"]["unborn"])

    def test_required_signing_is_not_silently_disabled(self):
        self.write("first.txt", "data\n")
        self.git("config", "commit.gpgsign", "true")
        self.preview("save", status=409, code="unsupported_repo")
        self.assertEqual(self.git("config", "commit.gpgsign").strip(), "true")
        self.assertFalse((self.repo / ".git/index").exists())

    def test_custom_fsmonitor_config_is_seen_and_write_is_rejected(self):
        self.commit({"a.txt": "base\n"})
        self.write("a.txt", "unsaved\n")
        self.git("config", "core.fsmonitor", "fixture-custom-monitor")
        config = (self.repo / ".git/config").read_bytes()
        self.preview("save", status=409, code="unsupported_repo")
        self.assertEqual((self.repo / ".git/config").read_bytes(), config)
        self.assertEqual(self.git("config", "core.fsmonitor").strip(), "fixture-custom-monitor")

    def test_filter_repository_is_browse_only(self):
        before = self.commit({"a.txt": "base\n"})
        self.write(".gitattributes", "*.txt filter=fixture\n")
        config = (self.repo / ".git/config").read_bytes()
        status = self.api("status", method="GET")
        self.assertTrue(status["capabilities"]["read"])
        self.assertFalse(status["capabilities"]["write"])
        self.preview("save", status=409, code="unsupported_repo")
        self.assertEqual(self.head(), before)
        self.assertEqual((self.repo / ".git/config").read_bytes(), config)

    def test_multi_worktree_is_browse_only(self):
        before = self.commit({"a.txt": "base\n"})
        self.git("worktree", "add", "-b", "other", str(self.root / "linked"))
        self.write("a.txt", "unsaved\n")
        self.assertFalse(self.api("status", method="GET")["capabilities"]["write"])
        self.preview("save", status=409, code="unsupported_repo")
        self.assertEqual(self.head(), before)

    def test_detached_head_can_browse_create_branch_but_not_save(self):
        before = self.commit({"a.txt": "base\n"})
        self.git("switch", "--detach", before)
        self.write("a.txt", "unsaved\n")
        self.assertTrue(self.api("status", method="GET")["head"]["detached"])
        self.preview("save", status=409, code="unsupported_repo")
        self.assertEqual(self.perform("create_branch", {"target_oid": before}, name="recovered")["outcome"], "done")
        self.assertTrue(self.api("status", method="GET")["head"]["detached"])

    def test_remote_url_credentials_query_fragment_are_redacted_without_network(self):
        self.commit({"a.txt": "base\n"})
        self.git("remote", "add", "origin", "https://fixture-user:fixture-password@example.invalid/repo.git?token=fixture-token#fixture-fragment")
        response = self.api("status", method="GET")
        encoded = json.dumps(response)
        for secret in ("fixture-user", "fixture-password", "fixture-token", "fixture-fragment"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(response["remotes"][0]["url"], "https://example.invalid/repo.git")

    def test_failed_local_remote_does_not_leak_path_or_raw_stderr(self):
        self.commit({"a.txt": "base\n"})
        missing = self.root / "fixture-secret-not-a-repository"
        self.git("remote", "add", "origin", str(missing))
        remote = self.api("status", method="GET")["remotes"][0]["remote_id"]
        response = self.api("fetch", {"remote_id": remote, "branch": "main"}, status=500, code="git_failed")
        encoded = json.dumps(response)
        self.assertNotIn("fixture-secret", encoded)
        self.assertNotIn("fatal:", encoded)
        self.assertNotIn(str(self.root), encoded)


class GitWebHttpTests(GitWebFixture):
    def setUp(self):
        super().setUp()
        self.server = create_server(home=str(self.home), host="127.0.0.1", port=0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)
        self.port = self.server.server_port

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)
        self.assertFalse(self.worker.is_alive())

    def request(self, endpoint="identity", *, headers=None, raw=None, method="POST"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        values = {"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}",
                  "X-Notebook-Request": "1", "Content-Type": "application/json"}
        for key, value in (headers or {}).items():
            if value is None:
                values.pop(key, None)
            else:
                values[key] = value
        body = raw if raw is not None else json.dumps({"name": "HTTP Fixture", "email": "http@example.invalid", "scope": "local"}).encode()
        try:
            connection.request(method, f"/api/project/{self.pid}/code/{endpoint}", body=body, headers=values)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_http_same_origin_guard_blocks_cross_site_without_side_effects(self):
        config = (self.repo / ".git/config").read_bytes()
        for headers in (
            {"Origin": "https://example.invalid"}, {"Origin": None},
            {"X-Notebook-Request": None}, {"Content-Type": "text/plain"},
            {"Host": "example.invalid", "Origin": "http://example.invalid"},
        ):
            with self.subTest(headers=headers):
                status, body = self.request(headers=headers)
                self.assertEqual((status, body.get("code")), (403, "forbidden_origin"))
                self.assertEqual((self.repo / ".git/config").read_bytes(), config)
        status, body = self.request(headers={"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertEqual(self.git("config", "--local", "user.name").strip(), "HTTP Fixture")

    def test_http_rejects_oversize_bad_length_nonobject_and_invalid_utf8(self):
        config = (self.repo / ".git/config").read_bytes()
        for headers, raw in (
            ({"Content-Length": str(2 * 1024 * 1024 + 1)}, b"{}"),
            ({"Content-Length": "-1"}, b"{}"),
            ({"Content-Length": "bad"}, b"{}"),
            ({"Content-Length": "0"}, b""),
            ({}, b"[]"), ({}, b"null"), ({}, b"{"), ({}, b"\xff"),
        ):
            with self.subTest(headers=headers, raw=raw):
                status, body = self.request(headers=headers, raw=raw)
                self.assertEqual((status, body.get("code")), (400, "invalid_request"))
                self.assertEqual((self.repo / ".git/config").read_bytes(), config)

    def test_http_get_dispatches_status_without_identity_or_git_writes(self):
        self.write("new.txt", "uncommitted\n")
        config = (self.repo / ".git/config").read_bytes()
        status, body = self.request("status", method="GET", raw=b"", headers={"Origin": None, "X-Notebook-Request": None})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["head"]["unborn"])
        self.assertEqual([f["path"] for f in body["files"]], ["new.txt"])
        self.assertFalse((self.repo / ".git/index").exists())
        self.assertEqual((self.repo / ".git/config").read_bytes(), config)


if __name__ == "__main__":
    unittest.main(verbosity=2)
