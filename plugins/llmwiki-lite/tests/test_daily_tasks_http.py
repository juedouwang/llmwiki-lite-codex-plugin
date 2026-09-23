"""Real HTTP and MCP integration, using only disposable registered projects."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from llmwiki_registry import register_project  # noqa: E402
import mcp_server as mcp  # noqa: E402
import research_progress as progress  # noqa: E402
from web_server import create_server  # noqa: E402


class DailyHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='daily-http-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = str(root / 'home')
        source = root / 'source'
        source.mkdir()
        self.project = register_project(str(source), home=self.home, wiki_root=str(root / 'wiki'))['project']
        self.pid = self.project['id']
        self.server = create_server(self.home, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get(self, path):
        with urllib.request.urlopen(self.origin + path) as response:
            raw = response.read().decode('utf-8')
            return json.loads(raw) if path.startswith('/api/') else raw

    def post(self, path, payload, origin=None):
        request = urllib.request.Request(self.origin + path, data=json.dumps(payload).encode(), headers={
            'Content-Type': 'application/json', 'X-Notebook-Request': '1', 'Origin': origin or self.origin,
        })
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def agent(self, payload):
        return mcp.dispatch('llmwiki_task_write', {'home': self.home, 'payload': payload})

    def revision(self):
        return progress.load_summary(self.project)['revision']

    def test_plan_mcp_web_shared_task_and_acceptance(self):
        payload = {'project_id': self.pid, 'action': 'plan', 'revision': self.revision(),
                   'request_id': 'http-plan-1', 'task': {'title': '验证低纹理方案', 'start': '2026-09-22', 'end': '2026-09-23'},
                   'subtasks': [{'title': '准备数据', 'scheduled_date': '2026-09-22'},
                                {'title': '运行基线', 'scheduled_date': '2026-09-23'}]}
        self.assertTrue(self.agent(payload)['ok'])
        self.assertTrue(self.agent(payload)['ok'])
        day = self.get('/api/daily-tasks?date=2026-09-22')
        self.assertEqual(len(day['tasks']), 1)
        child = day['tasks'][0]
        source = next(t for t in progress.load_summary(self.project)['tasks'] if t['id'] == child['id'])
        self.assertEqual(source['scheduled_date'], '2026-09-22')
        self.assertEqual(len(progress.load_summary(self.project)['tasks']), 3)
        self.agent({'project_id': self.pid, 'action': 'submit', 'revision': self.revision(), 'id': child['id'], 'summary': '已准备测试数据，实验结果尚未验证。'})
        pending = self.get('/api/daily-tasks?date=2026-09-22')['tasks'][0]
        self.assertEqual(pending['review_state'], 'pending')
        self.assertNotEqual(pending['status'], 'done')
        self.assertTrue(pending['completion_record_id'])
        self.post('/api/daily-tasks', {'project_id': self.pid, 'action': 'accept', 'revision': self.revision(), 'id': child['id']})
        done = self.get('/api/daily-tasks?date=2026-09-22')['completed'][0]
        self.assertEqual(done['id'], child['id'])
        self.assertEqual(done['status'], 'done')
        self.assertEqual(done['review_state'], 'accepted')
        self.assertTrue(done['completion_record_id'])
        self.assertEqual(self.get('/api/daily-tasks?date=2026-09-23')['tasks'][0]['title'], '运行基线')

    def test_web_reject_then_agent_resubmit_then_user_accept(self):
        day = '2026-09-22'
        created = self.post('/api/daily-tasks', {'project_id': self.pid, 'action': 'create',
                           'revision': self.revision(), 'task': {'title': '验证边界', 'scheduled_date': day}})['task']
        self.agent({'project_id': self.pid, 'action': 'submit', 'revision': self.revision(),
                    'id': created['id'], 'summary': '首轮验证'})
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            self.post('/api/daily-tasks', {'project_id': self.pid, 'action': 'reject',
                      'revision': self.revision(), 'id': created['id'], 'reason': ''})
        self.assertEqual(invalid.exception.code, 400)
        rejected = self.post('/api/daily-tasks', {'project_id': self.pid, 'action': 'reject',
                             'revision': self.revision(), 'id': created['id'], 'reason': '需补充失败样本'})['task']
        self.assertEqual((rejected['status'], rejected['review_state']), ('active', 'rejected'))
        self.assertTrue(rejected['rejection_record_id'])
        self.agent({'project_id': self.pid, 'action': 'submit', 'revision': self.revision(),
                    'id': created['id'], 'summary': '补充失败样本并复测'})
        pending = self.get('/api/daily-tasks?date=' + day)['tasks'][0]
        self.assertEqual(pending['review_state'], 'pending')
        self.assertNotEqual(pending['completion_record_id'], rejected['completion_record_id'])
        self.post('/api/daily-tasks', {'project_id': self.pid, 'action': 'accept',
                  'revision': self.revision(), 'id': created['id']})
        self.assertEqual(self.get('/api/daily-tasks?date=' + day)['completed'][0]['status'], 'done')

    def test_csrf_revision_and_agent_cannot_accept(self):
        create = {'project_id': self.pid, 'action': 'create', 'revision': self.revision(), 'task': {'title': '手动任务', 'scheduled_date': '2026-09-22'}}
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            self.post('/api/daily-tasks', create, origin='https://untrusted.test')
        self.assertEqual(rejected.exception.code, 403)
        self.post('/api/daily-tasks', create)
        child = self.get('/api/daily-tasks?date=2026-09-22')['tasks'][0]
        with self.assertRaises(urllib.error.HTTPError) as stale:
            self.post('/api/daily-tasks', create)
        self.assertEqual(stale.exception.code, 409)
        result = mcp.handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'llmwiki_task_write', 'arguments': {'home': self.home, 'payload': {'project_id': self.pid, 'action': 'accept', 'revision': self.revision(), 'id': child['id']}}}})
        self.assertTrue(result['result']['isError'])
        self.assertNotEqual(progress.load_summary(self.project)['tasks'][0]['status'], 'done')

    def test_page_and_old_routes_preserved(self):
        page = self.get('/daily?date=2026-09-22&context=' + self.pid)
        for text in ('每日待办', '我的工作', '科研记录', '科研进度', '日报与周报', 'daily-tasks.js'):
            self.assertIn(text, page)
        for path in ('/static/daily-tasks.js', '/static/daily-tasks.css', '/project/' + self.pid + '/todos', '/project/' + self.pid + '/records', '/reports?context=' + self.pid, '/project/' + self.pid + '/code'):
            self.assertTrue(self.get(path), path)
        invalid = '/api/daily-tasks?date=not-a-date'
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.get(invalid)
        self.assertEqual(error.exception.code, 400)


if __name__ == '__main__':
    unittest.main()
