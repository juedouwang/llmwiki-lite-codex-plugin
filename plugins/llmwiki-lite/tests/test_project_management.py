"""Project UI context and actions: every fixture lives in a disposable registry."""

import http.cookiejar
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
import test_project_preferences as preferences
from llmwiki_core import LLMWikiError
from llmwiki_registry import (
    get_project,
    load_settings,
    rename_project,
    update_project_preferences,
)
from web_server import create_server


class ProjectManagementTests(preferences.ProjectPreferencesTests):
    def launch(self):
        server = create_server(self.home, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()

        def stop():
            server.shutdown()
            server.server_close()
            worker.join()

        self.addCleanup(stop)
        return f"http://127.0.0.1:{server.server_port}"

    def browser(self):
        jar = http.cookiejar.CookieJar()
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def page(self, opener, origin, path, **headers):
        request = urllib.request.Request(origin + path, headers=headers)
        with opener.open(request) as response:
            return response.url, response.read().decode("utf-8")

    def post(self, origin, path, payload, *, same_origin=True):
        headers = {
            "Content-Type": "application/json",
            "X-Notebook-Request": "1",
            "Origin": origin if same_origin else "https://untrusted.test",
        }
        request = urllib.request.Request(
            origin + path, data=json.dumps(payload).encode(), headers=headers
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_browser_choice_survives_global_pages_preference_changes_and_prefetch(self):
        update_project_preferences(home=self.home, default_project_id=self.a["id"])
        origin, browser = self.launch(), self.browser()
        self.page(browser, origin, f"/project/{self.z['id']}/todos")
        for route in ["/settings", "/projects", "/reports"]:
            _, html = self.page(browser, origin, route)
            self.assertIn(f'data-workbench-project="{self.z["id"]}"', html)
        self.page(
            browser,
            origin,
            f"/project/{self.a['id']}/todos",
            **{"X-Workbench-Navigation": "1"},
        )
        update_project_preferences(home=self.home, default_project_id=self.a["id"])
        url, _ = self.page(browser, origin, "/")
        self.assertTrue(url.endswith(f"/project/{self.z['id']}/todos"))
        self.assertEqual(load_settings(self.home)["current_project_id"], self.z["id"])
        # An independent browser uses the default, not another browser's selection.
        url, _ = self.page(self.browser(), origin, "/")
        self.assertTrue(url.endswith(f"/project/{self.a['id']}/todos"))

    def test_server_restart_ignores_previous_epoch_and_uses_default(self):
        origin, browser = self.launch(), self.browser()
        self.page(browser, origin, f"/project/{self.z['id']}/todos")
        update_project_preferences(home=self.home, default_project_id=self.a["id"])
        # A second server handler models a new process epoch; same host cookie persists.
        restarted = self.launch()
        url, _ = self.page(browser, restarted, "/")
        self.assertTrue(url.endswith(f"/project/{self.a['id']}/todos"))

    def test_explicit_global_context_and_empty_report_context(self):
        origin, browser = self.launch(), self.browser()
        _, html = self.page(browser, origin, "/projects?context=" + self.z["id"])
        self.assertIn(f'data-workbench-project="{self.z["id"]}"', html)
        _, html = self.page(browser, origin, "/reports?context=")
        self.assertIn('data-workbench-project=""', html)

    def test_rename_changes_only_display_name(self):
        before = get_project(self.a["id"], home=self.home)["project"]
        result = rename_project(self.a["id"], "新项目名称", home=self.home)["project"]
        self.assertEqual(result["name"], "新项目名称")
        for field in ["id", "source_root", "state_root", "wiki_root"]:
            self.assertEqual(result[field], before[field])
        for invalid in ["", "  ", "bad\nname", "x" * 121, None, ["name"]]:
            with self.assertRaises(LLMWikiError):
                rename_project(self.a["id"], invalid, home=self.home)
        self.assertEqual(
            get_project(self.a["id"], home=self.home)["project"]["name"], "新项目名称"
        )

    def test_action_endpoints_validate_and_preserve_files(self):
        origin = self.launch()
        base = "/api/projects/" + self.z["id"]
        for suffix, payload in [
            ("rename", {"name": "改名"}),
            ("unregister", {"confirm": True}),
        ]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post(origin, base + "/" + suffix, payload, same_origin=False)
            self.assertEqual(error.exception.code, 403)
        result = self.post(origin, base + "/rename", {"name": "重命名成功"})
        self.assertEqual(result["project"]["name"], "重命名成功")
        with self.assertRaises(urllib.error.HTTPError):
            self.post(origin, base + "/unregister", {})
        sentinels = []
        for field in ["source_root", "wiki_root", "state_root"]:
            sentinel = Path(self.z[field]) / "keep.txt"
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text("preserved", encoding="utf-8")
            sentinels.append(sentinel)
        result = self.post(origin, base + "/unregister", {"confirm": True})
        self.assertFalse(result["files_deleted"])
        self.assertNotIn(self.z["id"], [p["id"] for p in result["projects"]])
        self.assertTrue(
            all(p.read_text(encoding="utf-8") == "preserved" for p in sentinels)
        )

    def test_switcher_contains_only_projects_and_schedule_explains_connection(self):
        origin = self.launch()
        _, html = self.page(self.browser(), origin, "/settings")
        menu = html.split('<div class="console-project-menu">')[1].split("</div>")[0]
        self.assertNotIn("管理项目", menu)
        self.assertNotIn("添加项目", menu)
        self.assertIn("所有参与项目共用一份整理计划", html)
        self.assertIn("新注册项目默认参与", html)
        self.assertIn("会话关联决定用哪些材料", html)
        self.assertIn("保存配置不会创建后台进程", html)
        self.assertIn("不自动开启取材或恢复已暂停计划", html)
        with urllib.request.urlopen(origin + "/api/reports/settings") as response:
            config = json.load(response)
        self.assertCountEqual(config["project_ids"], [self.a["id"], self.z["id"]])
        self.assertFalse(config["enabled"])
