"""Project ordering/startup selection in temporary registries, never user projects."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llmwiki_core import LLMWikiError
from llmwiki_registry import (list_projects, load_settings, register_project, select_project,
                             unregister_project, update_project_preferences)
from web_server import create_server


class ProjectPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="llmwiki-preferences-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = str(self.root / "home")
        self.a = self.register("A 项目")
        self.z = self.register("Z 项目")
        select_project(self.z["id"], home=self.home)

    def register(self, name):
        source = self.root / name
        source.mkdir()
        return register_project(str(source), name=name, home=self.home,
                                wiki_root=str(self.root / (name + "-wiki")),
                                state_root=str(self.root / (name + "-state")))["project"]

    def test_startup_follows_first_not_agent_selection(self):
        listed = list_projects(self.home)
        self.assertEqual(listed["current_project_id"], self.z["id"])
        self.assertEqual(listed["landing_project_id"], self.a["id"])
        self.assertIsNone(listed["web_default_project_id"])

    def test_explicit_default_persists_without_changing_agent(self):
        update_project_preferences(home=self.home, default_project_id=self.a["id"])
        self.assertEqual(load_settings(self.home)["web_default_project_id"], self.a["id"])
        result = update_project_preferences(home=self.home, project_order=[self.z["id"], self.a["id"]])
        self.assertEqual(result["landing_project_id"], self.a["id"])
        self.assertEqual(result["current_project_id"], self.z["id"])
        result = update_project_preferences(home=self.home, default_project_id=None)
        self.assertEqual(result["landing_project_id"], self.z["id"])

    def test_order_persists_and_new_projects_append(self):
        ids = [self.z["id"], self.a["id"]]
        update_project_preferences(home=self.home, project_order=ids)
        self.assertEqual(list_projects(self.home)["project_order"], ids)
        new = self.register("0 新项目")
        self.assertEqual(list_projects(self.home)["project_order"], ids + [new["id"]])
        self.assertFalse(list(Path(new["source_root"]).iterdir()))

    def test_invalid_stale_order_and_default_are_atomic(self):
        before = load_settings(self.home)
        for invalid in [[], [self.a["id"]], [self.a["id"], self.a["id"]], [[], self.z["id"]], "bad"]:
            with self.assertRaises(LLMWikiError):
                update_project_preferences(home=self.home, default_project_id=self.z["id"], project_order=invalid)
            self.assertEqual(load_settings(self.home), before)
        for invalid in ["unknown", {}, []]:
            with self.assertRaises(LLMWikiError):
                update_project_preferences(home=self.home, default_project_id=invalid)
        self.assertEqual(load_settings(self.home), before)

    def test_unregister_default_falls_back_without_deleting_files(self):
        update_project_preferences(home=self.home, default_project_id=self.z["id"], project_order=[self.z["id"], self.a["id"]])
        unregister_project(self.z["id"], home=self.home)
        listed = list_projects(self.home)
        self.assertIsNone(listed["web_default_project_id"])
        self.assertEqual(listed["project_order"], [self.a["id"]])
        self.assertEqual(listed["landing_project_id"], self.a["id"])
        self.assertTrue(Path(self.z["source_root"]).is_dir())

    def test_http_startup_overview_and_same_origin_preferences(self):
        server = create_server(self.home, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        def get(path):
            with urllib.request.urlopen(origin + path) as response:
                return response.url, response.read().decode("utf-8")
        def post(payload, same_origin=True):
            headers = {"Content-Type": "application/json", "X-Notebook-Request": "1", "Origin": origin if same_origin else "https://untrusted.test"}
            request = urllib.request.Request(origin + "/api/projects/preferences", data=json.dumps(payload).encode(), headers=headers)
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        try:
            self.assertEqual(get("/")[0], origin + "/daily")
            url, page = get("/projects")
            self.assertEqual(url, origin + "/projects")
            self.assertIn(f'console-sidebar-brand" href="/projects?context={self.a["id"]}"', page)
            self.assertIn('project-drag-handle', page)
            self.assertIn(f'project-row-main" href="/projects?context={self.a["id"]}"', page)
            # Both selection controls remain on the project browser; neither opens a column.
            menu = page.split('<div class="console-project-menu">', 1)[1].split("</div>", 1)[0]
            self.assertIn(f'href="/projects?context={self.z["id"]}"', menu)
            self.assertNotIn("/todos", menu)
            self.assertEqual(get(f'/projects?context={self.z["id"]}')[0], origin + f'/projects?context={self.z["id"]}')
            self.assertNotIn('project-row-task', page)
            with self.assertRaises(urllib.error.HTTPError) as error:
                post({"default_project_id": self.z["id"]}, same_origin=False)
            self.assertEqual(error.exception.code, 403)
            post({"default_project_id": self.z["id"]})
            self.assertEqual(get("/")[0], origin + "/daily")
            before = load_settings(self.home)
            with self.assertRaises(urllib.error.HTTPError) as error:
                post({"current_project_id": self.a["id"]})
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(load_settings(self.home), before)
            unregister_project(self.a["id"], home=self.home)
            unregister_project(self.z["id"], home=self.home)
            self.assertEqual(get("/")[0], origin + "/daily")
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
