"""Real registered linked-worktree writes; every repository/remote is temporary."""
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from test_git_web import GitWebFixture
import git_web
from llmwiki_registry import register_project


class GitWebWorktreeTests(GitWebFixture):
    def setUp(self):
        super().setUp()
        self.initial = self.commit({"a.txt": "base a\n", "b.txt": "base b\n", "c.txt": "base c\n"})
        self.linked = self.root / "实验 linked worktree"
        self.git("worktree", "add", "-b", "experiment", str(self.linked))
        self.linked_project = register_project(
            str(self.linked), name="linked", home=str(self.home), select=False,
            state_root=str(self.root / "linked-state"), wiki_root=str(self.root / "linked-wiki"),
        )["project"]
        self.lpid = self.linked_project["id"]

    def lpreview(self, action, params=None, **kwargs):
        return self.preview(action, params, pid=self.lpid, **kwargs)

    def lexecute(self, preview, **kwargs):
        return self.api("execute", {"preview_id": preview["preview_id"], **kwargs}, pid=self.lpid)

    def adapter(self, pid=None):
        return git_web.Repo(str(self.home), pid or self.pid)

    def index(self, pid=None):
        return (self.adapter(pid).gitdir / "index").read_bytes()

    def test_registered_primary_and_linked_are_writable_and_occupied_branch_is_clear(self):
        for pid, root, other in ((self.pid, self.repo, "experiment"), (self.lpid, self.linked, "main")):
            with self.subTest(pid=pid):
                data = self.api("status", method="GET", pid=pid)
                self.assertTrue(data["capabilities"]["write"], data)
                self.assertEqual({k: v for k, v in data["worktree"].items() if k != "id"},
                                 {"path": str(root), "linked": pid == self.lpid, "count": 2})
                branch = next(b for b in data["branches"] if b["name"] == other)
                self.assertFalse(branch["switchable"])
                self.assertIn("其他工作树", branch["switch_reason"])
                blocked = self.preview("switch_branch", {"branch": other}, pid=pid, status=409, code="branch_in_use")
                self.assertIn(str(self.linked if pid == self.pid else self.repo), blocked["message"])
        self.assertEqual(self.head(), self.initial)
        self.assertEqual(self.head(self.linked), self.initial)

    def test_linked_save_preserves_both_dirty_worktrees_and_unselected_staging(self):
        self.write("a.txt", "primary unsaved\n")
        self.write("b.txt", "primary staged\n")
        self.git("add", "b.txt")
        primary_index = self.index()
        self.write("a.txt", "linked selected\n", self.linked)
        self.write("b.txt", "linked staged\n", self.linked)
        self.git("add", "b.txt", repo=self.linked)
        linked_staged = self.git("rev-parse", ":b.txt", repo=self.linked)
        self.write("c.txt", "linked unselected\n", self.linked)
        preview = self.lpreview("save")
        result = self.lexecute(preview, file_ids=self.file_ids(preview, "a.txt"), message="linked only")
        self.assertEqual(result["outcome"], "done")
        self.assertEqual(self.head(), self.initial)
        self.assertNotEqual(self.head(self.linked), self.initial)
        self.assertEqual(self.index(), primary_index)
        self.assertEqual((self.repo / "a.txt").read_text(), "primary unsaved\n")
        self.assertEqual(self.git("show", "HEAD:a.txt", repo=self.linked), "linked selected\n")
        self.assertEqual(self.git("show", "HEAD:b.txt", repo=self.linked), "base b\n")
        self.assertEqual(self.git("rev-parse", ":b.txt", repo=self.linked), linked_staged)
        self.assertEqual((self.linked / "c.txt").read_text(), "linked unselected\n")

    def test_other_worktree_dirtiness_does_not_stale_preview_or_get_saved(self):
        self.write("a.txt", "linked save\n", self.linked)
        preview = self.lpreview("save")
        self.write("a.txt", "new primary dirty content\n")
        self.git("add", "a.txt")
        primary_index = self.index()
        self.lexecute(preview, file_ids=self.file_ids(preview, "a.txt"), message="save with other dirty")
        self.assertEqual(self.index(), primary_index)
        self.assertEqual(self.head(), self.initial)

    def test_create_branch_shares_refs_without_switching_or_touching_dirty_worktrees(self):
        self.write("a.txt", "main dirty\n")
        self.write("a.txt", "linked dirty\n", self.linked)
        indexes = (self.index(), self.index(self.lpid))
        result = self.lexecute(self.lpreview("create_branch", {"target_oid": self.initial}), name="new-experiment")
        self.assertEqual(result["outcome"], "done")
        self.assertEqual(self.git("rev-parse", "refs/heads/new-experiment").strip(), self.initial)
        self.assertEqual(self.git("branch", "--show-current", repo=self.linked).strip(), "experiment")
        self.assertEqual((self.index(), self.index(self.lpid)), indexes)
        for root, value in ((self.repo, "main dirty\n"), (self.linked, "linked dirty\n")):
            self.assertEqual((root / "a.txt").read_text(), value)

    def test_current_dirty_blocks_switch_merge_restore_but_other_dirty_does_not(self):
        self.git("branch", "available")
        self.write("a.txt", "linked dirty\n", self.linked)
        for action, params in (("switch_branch", {"branch": "available"}), ("merge", {"source_branch": "main"}),
                               ("restore", {"target_oid": self.initial})):
            self.lpreview(action, params, status=409, code="dirty_worktree")
        self.commit({"a.txt": "linked saved\n"}, repo=self.linked)
        self.write("a.txt", "main dirty\n")
        primary_index = self.index()
        self.lexecute(self.lpreview("switch_branch", {"branch": "available"}))
        self.assertEqual(self.git("branch", "--show-current", repo=self.linked).strip(), "available")
        self.assertEqual(self.index(), primary_index)
        self.assertEqual((self.repo / "a.txt").read_text(), "main dirty\n")

    def test_merge_occupied_source_and_restore_are_local_new_history(self):
        source = self.commit({"b.txt": "source version\n"})
        self.commit({"a.txt": "linked version\n"}, repo=self.linked)
        self.write("b.txt", "source dirty\n")
        primary_index = self.index()
        result = self.lexecute(self.lpreview("merge", {"source_branch": "main"}))
        self.assertEqual(result["outcome"], "done")
        merged = self.head(self.linked)
        self.assertEqual(len(self.git("rev-list", "--parents", "-n", "1", merged).split()), 3)
        self.assertEqual(self.git("show", "HEAD:b.txt", repo=self.linked), "source version\n")
        result = self.lexecute(self.lpreview("restore", {"target_oid": self.initial}))
        self.assertEqual(result["outcome"], "done")
        self.assertEqual(self.git("rev-parse", "HEAD^", repo=self.linked).strip(), merged)
        self.assertEqual((self.linked / "a.txt").read_text(), "base a\n")
        self.assertEqual(self.head(), source)
        self.assertEqual(self.index(), primary_index)
        self.assertEqual((self.repo / "b.txt").read_text(), "source dirty\n")

    def test_shared_ref_change_invalidates_other_worktree_preview(self):
        self.write("a.txt", "linked unsaved\n", self.linked)
        preview = self.lpreview("save")
        index = self.index(self.lpid)
        self.commit({"b.txt": "main new commit\n"})
        self.api("execute", self.save_data(preview, "a.txt"), pid=self.lpid, status=409, code="state_changed")
        self.assertEqual(self.index(self.lpid), index)
        self.assertEqual(self.head(self.linked), self.initial)

    def test_branch_becoming_occupied_after_preview_has_actionable_error(self):
        self.git("branch", "available")
        preview = self.lpreview("switch_branch", {"branch": "available"})
        self.git("switch", "available")  # Same refs/topology, different occupancy.
        result = self.api("execute", {"preview_id": preview["preview_id"]}, pid=self.lpid,
                          status=409, code="branch_in_use")
        self.assertIn(str(self.repo), result["message"])
        self.assertEqual(self.git("branch", "--show-current", repo=self.linked).strip(), "experiment")

    def test_topology_change_invalidates_preview_without_touching_files(self):
        self.write("a.txt", "linked save\n", self.linked)
        preview = self.lpreview("save")
        self.git("worktree", "add", "--detach", str(self.root / "third"))
        self.api("execute", self.save_data(preview, "a.txt"), pid=self.lpid, status=409, code="state_changed")
        self.assertEqual(self.head(self.linked), self.initial)

    def test_preview_is_bound_to_registered_worktree_not_just_common_gitdir(self):
        self.write("a.txt", "main dirty\n")
        self.write("a.txt", "linked dirty\n", self.linked)
        preview = self.preview("save")
        self.api("execute", self.save_data(preview, "a.txt"), pid=self.lpid, status=409, code="preview_expired")
        self.assertEqual(self.api("execute", self.save_data(preview, "a.txt"))["outcome"], "done")
        self.assertEqual(self.head(self.linked), self.initial)

    def test_common_gitdir_lock_serializes_worktrees_even_from_another_process(self):
        primary, linked = self.adapter(), self.adapter(self.lpid)
        self.assertEqual(primary.lock().lock_file, linked.lock().lock_file)
        self.assertNotEqual(primary.binding, linked.binding)
        self.write("a.txt", "linked dirty\n", self.linked)
        preview = self.lpreview("save")
        command = ("import sys; from pathlib import Path; from git_service import RepositoryLock; "
                   "lock=RepositoryLock(Path(sys.argv[1]),sys.argv[2]); lock.acquire(); "
                   "print('locked',flush=True); sys.stdin.readline(); lock.release()")
        process = subprocess.Popen([sys.executable, "-B", "-c", command, str(primary.lock().lock_dir), primary.key],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env={**os.environ, "PYTHONPATH": str(Path(git_web.__file__).parent)},
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            self.api("execute", self.save_data(preview, "a.txt"), pid=self.lpid, status=409, code="repo_busy")
            self.assertTrue(primary.lock().lock_file.exists())
            self.assertEqual(self.head(self.linked), self.initial)
        finally:
            process.communicate("release\n", timeout=10)
        self.assertEqual(process.returncode, 0)
        preview = self.lpreview("save")
        self.lexecute(preview, file_ids=self.file_ids(preview, "a.txt"), message="after unlock")

    def test_external_operation_in_other_worktree_does_not_own_current_operation(self):
        (self.adapter().gitdir / "MERGE_HEAD").write_text(self.initial + "\n")
        self.assertTrue(self.api("status", method="GET")["ongoing"]["external"])
        self.assertIsNone(self.api("status", method="GET", pid=self.lpid)["ongoing"])
        self.write("a.txt", "linked save\n", self.linked)
        preview = self.lpreview("save")
        self.lexecute(preview, file_ids=self.file_ids(preview, "a.txt"), message="independent index")
        self.assertEqual((self.adapter().gitdir / "MERGE_HEAD").read_text(), self.initial + "\n")

    def test_invalid_backlink_is_browse_only_not_a_license_to_write(self):
        adapter = self.adapter(self.lpid)
        (adapter.gitdir / "gitdir").write_text(str(self.root / "unregistered/.git") + "\n")
        self.write("a.txt", "linked save\n", self.linked)
        data = self.api("status", method="GET", pid=self.lpid)
        self.assertFalse(data["capabilities"]["write"])
        self.lpreview("save", status=409, code="unsupported_repo")
        self.assertEqual(self.head(self.linked), self.initial)

    def test_inherited_git_selection_cannot_redirect_registered_worktree(self):
        for key, value in (("GIT_DIR", str(self.repo / ".git")), ("GIT_INDEX_FILE", str(self.repo / ".git/index")),
                           ("GIT_WORK_TREE", str(self.repo))):
            with self.subTest(key=key), patch.dict(os.environ, {key: value}):
                self.lpreview("save", status=409, code="unsupported_repo")

    def test_shared_hooks_and_info_attributes_are_not_bypassed(self):
        self.write("a.txt", "linked save\n", self.linked)
        common = self.adapter().common
        hook = common / "hooks/pre-commit"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 41\n")
        hook.chmod(0o755)
        self.lpreview("save", status=409, code="unsupported_repo")
        self.assertEqual(hook.read_text(), "#!/bin/sh\nexit 41\n")
        # Remove only our temporary test hook to test the independent filter guard.
        hook.unlink()
        attributes = common / "info/attributes"
        attributes.parent.mkdir(exist_ok=True)
        attributes.write_text("*.txt filter=probe\n")
        self.git("config", "filter.probe.clean", "must-not-run-filter")
        self.assertFalse(self.api("status", method="GET", pid=self.lpid)["capabilities"]["write"])
        self.lpreview("save", status=409, code="unsupported_repo")
        self.assertEqual(attributes.read_text(), "*.txt filter=probe\n")

    def test_worktree_signing_policy_remains_effective_and_unchanged(self):
        self.git("config", "extensions.worktreeConfig", "true")
        self.git("config", "--worktree", "commit.gpgsign", "true", repo=self.linked)
        self.write("a.txt", "linked save\n", self.linked)
        config = self.adapter(self.lpid).gitdir / "config.worktree"
        before = config.read_bytes()
        self.lpreview("save", status=409, code="unsupported_repo")
        self.assertEqual(config.read_bytes(), before)
        self.assertNotIn("commit.gpgsign", self.adapter().config)

    def test_linked_fetch_pull_push_only_use_current_head_and_fixed_selected_remote(self):
        bare = self.root / "remote.git"
        self.git("init", "--bare", str(bare))
        self.git("remote", "add", "origin", str(bare))
        self.git("push", "origin", "main")
        peer = self.root / "peer"
        self.git("clone", "--branch", "main", str(bare), str(peer))
        self.configure(peer)
        incoming = self.commit({"remote.txt": "remote commit\n"}, repo=peer)
        self.git("push", "origin", "main", repo=peer)
        self.write("a.txt", "main dirty\n")
        primary_index = self.index()
        remote = self.api("status", method="GET", pid=self.lpid)["remotes"][0]["remote_id"]
        checked = self.api("fetch", {"remote_id": remote, "branch": "main"}, pid=self.lpid)
        self.assertEqual(checked["fetched_oid"], incoming)
        self.assertEqual(self.git("rev-parse", "refs/remotes/origin/main").strip(), incoming)
        params = {"remote_id": remote, "branch": "main", "fetched_oid": incoming}
        self.write("a.txt", "linked dirty\n", self.linked)
        self.lpreview("pull_apply", params, status=409, code="dirty_worktree")
        self.write("a.txt", "base a\n", self.linked)
        self.lexecute(self.lpreview("pull_apply", params))
        self.assertEqual(self.head(self.linked), incoming)
        self.write("a.txt", "linked uncommitted never uploaded\n", self.linked)
        self.lexecute(self.lpreview("push", {"remote_id": remote, "target_branch": "experiment"}))
        self.assertEqual(self.git("rev-parse", "refs/heads/experiment", repo=bare).strip(), incoming)
        self.assertEqual(self.git("rev-parse", "refs/heads/main", repo=bare).strip(), incoming)
        self.assertEqual(self.head(), self.initial)
        self.assertEqual(self.index(), primary_index)
        self.assertEqual((self.repo / "a.txt").read_text(), "main dirty\n")
        self.assertEqual((self.linked / "a.txt").read_text(), "linked uncommitted never uploaded\n")
        self.assertFalse((self.adapter().gitdir / "FETCH_HEAD").exists())
        self.assertFalse((self.adapter(self.lpid).gitdir / "FETCH_HEAD").exists())

    def selected_id(self):
        return self.adapter().worktree_token(self.linked)

    def test_same_project_navigation_is_read_only_and_lists_owner(self):
        self.write("a.txt", "main dirty\n")
        self.write("b.txt", "main staged\n")
        self.git("add", "b.txt")
        self.write("a.txt", "linked dirty\n", self.linked)
        self.git("add", "a.txt", repo=self.linked)
        indexes = self.index(), self.index(self.lpid)
        primary = self.api("status", method="GET")
        selected = self.api("status", {"worktree": self.selected_id()}, method="GET")
        self.assertEqual(selected["head"]["branch"], "experiment")
        self.assertEqual(selected["worktree"]["path"], str(self.linked))
        self.assertEqual([w["path"] for w in selected["worktrees"] if w["current"]], [str(self.linked)])
        branch = next(b for b in primary["branches"] if b["name"] == "experiment")
        self.assertEqual(branch["worktree_id"], self.selected_id())
        self.assertFalse(branch["switchable"])
        self.preview("switch_branch", {"branch": "experiment"}, status=409, code="dirty_worktree")
        self.assertEqual(self.index(), indexes[0])
        self.assertEqual(self.index(self.lpid), indexes[1])
        self.assertEqual(self.head(), self.initial)
        self.assertEqual(self.head(self.linked), self.initial)
        self.assertEqual(self.adapter().project["source_root"], str(self.repo))

    def test_same_project_selected_save_and_diff_never_modify_registered_worktree(self):
        wid = self.selected_id()
        self.write("a.txt", "main untouched\n")
        self.git("add", "a.txt")
        primary_index = self.index()
        self.write("a.txt", "linked selected\n", self.linked)
        self.write("b.txt", "linked staged\n", self.linked)
        self.git("add", "b.txt", repo=self.linked)
        staged = self.git("rev-parse", ":b.txt", repo=self.linked)
        preview = self.preview("save", worktree_id=wid)
        diff = self.api("diff", {"worktree": wid, "kind": "worktree", "preview_id": preview["preview_id"],
                                "file_id": self.file_ids(preview, "a.txt")[0]}, method="GET")
        self.assertIn("linked selected", diff["text"])
        self.assertNotIn("main untouched", diff["text"])
        self.api("execute", self.save_data(preview, "a.txt"), worktree_id=wid)
        self.assertEqual(self.head(), self.initial)
        self.assertEqual(self.index(), primary_index)
        self.assertEqual((self.repo / "a.txt").read_text(), "main untouched\n")
        self.assertEqual(self.git("show", "HEAD:a.txt", repo=self.linked), "linked selected\n")
        self.assertEqual(self.git("show", "HEAD:b.txt", repo=self.linked), "base b\n")
        self.assertEqual(self.git("rev-parse", ":b.txt", repo=self.linked), staged)

    def test_same_project_preview_file_ids_and_state_are_worktree_bound(self):
        wid = self.selected_id()
        self.write("a.txt", "main dirty\n")
        self.write("a.txt", "linked dirty\n", self.linked)
        main = self.preview("save")
        other = self.preview("save", worktree_id=wid)
        self.assertNotEqual(self.file_ids(main, "a.txt"), self.file_ids(other, "a.txt"))
        self.api("execute", self.save_data(main, "a.txt"), worktree_id=wid, status=409, code="preview_expired")
        self.api("execute", {"preview_id": other["preview_id"], "file_ids": self.file_ids(main, "a.txt"),
                             "message": "reject cross-tree ids"}, worktree_id=wid, status=400, code="invalid_request")
        primary = self.adapter()
        selected = git_web.Repo(str(self.home), self.pid, wid)
        self.assertEqual(primary.lock().lock_file, selected.lock().lock_file)
        self.assertNotEqual(primary.state, selected.state)
        for name in ("fetch", "merge"):
            primary.write_state(name, {"from": "primary"})
            self.assertIsNone(selected.read_state(name))
            selected.write_state(name, {"from": "linked"})
            self.assertEqual(primary.read_state(name), {"from": "primary"})
        self.assertEqual(self.head(), self.initial)
        self.assertEqual(self.head(self.linked), self.initial)

    def test_same_project_rejects_arbitrary_paths_foreign_ids_and_removed_worktrees(self):
        for value in (str(self.linked), "../other", "x" * 64):
            self.api("status", {"worktree": value}, method="GET", status=400, code="invalid_worktree")
        foreign = self.adapter(self.lpid).worktree_token(self.linked)
        self.api("status", {"worktree": foreign}, method="GET", status=409, code="worktree_unavailable")
        wid = self.selected_id()
        self.git("worktree", "remove", str(self.linked))
        self.api("status", {"worktree": wid}, method="GET", status=409, code="worktree_unavailable")
        self.api("preview", {"action": "save", "params": {}}, worktree_id=wid, status=409, code="worktree_unavailable")
        self.assertEqual(self.head(), self.initial)

    def test_same_project_locked_revalidation_keeps_selection_and_rejects_stale_binding(self):
        wid = self.selected_id()
        selected = git_web.Repo(str(self.home), self.pid, wid)
        with selected.lock():
            selected.revalidate()
        self.write("a.txt", "linked pending\n", self.linked)
        preview = self.preview("save", worktree_id=wid)
        backlink = selected.gitdir / "gitdir"
        old = backlink.read_bytes()
        try:
            backlink.write_text(str(self.root / "elsewhere/.git") + "\n")
            with selected.lock(), self.assertRaises(git_web.WebGitError) as caught:
                selected.revalidate()
            self.assertEqual(caught.exception.code, "worktree_unavailable")
            self.api("execute", self.save_data(preview, "a.txt"), worktree_id=wid,
                     status=409, code="worktree_unavailable")
            menu = self.api("status", method="GET")["worktrees"]
            self.assertFalse(next(w for w in menu if w["branch"] == "experiment")["available"])
        finally:
            backlink.write_bytes(old)
        self.assertEqual(self.head(), self.initial)
        self.assertEqual(self.head(self.linked), self.initial)

    def test_same_project_detached_worktree_and_unavailable_option(self):
        detached = self.root / "detached worktree"
        self.git("worktree", "add", "--detach", str(detached), self.initial)
        wid = self.adapter().worktree_token(detached)
        result = self.api("status", {"worktree": wid}, method="GET")
        self.assertTrue(result["head"]["detached"])
        self.preview("save", worktree_id=wid, status=409, code="unsupported_repo")
        self.git("worktree", "remove", str(detached))
        # Broken registration is deliberately left in our disposable fixture.
        selected = self.adapter(self.lpid)
        (selected.gitdir / "gitdir").write_text(str(self.root / "gone/.git") + "\n")
        menu = self.api("status", method="GET")
        self.assertFalse(next(w for w in menu["worktrees"] if w["branch"] == "experiment")["available"])
        self.assertIsNone(next(b for b in menu["branches"] if b["name"] == "experiment")["worktree_id"])
