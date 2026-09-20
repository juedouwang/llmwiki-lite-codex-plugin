"""Real loopback HTTP guards and read-only Git page integration."""
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from git_service import _run_git_command  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from web_server import create_server  # noqa: E402


class GitHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='git-http-')
        cls.root = Path(cls.tmp.name)
        cls.env = patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'})
        cls.env.start()
        cls.repo = cls.root / 'repo'
        cls.repo.mkdir()
        for args in [('init', '-b', 'main'), ('config', 'user.name', 'HTTP Test'), ('config', 'user.email', 'http@example.test')]:
            _run_git_command('git', list(args), cwd=cls.repo)
        (cls.repo / 'file.txt').write_text('test\n', encoding='utf-8')
        _run_git_command('git', ['add', 'file.txt'], cwd=cls.repo)
        _run_git_command('git', ['commit', '-m', '<img src=x onerror=alert(1)>'], cwd=cls.repo)
        home = str(cls.root / 'home')
        cls.pid = register_project(str(cls.repo), home=home, state_root=str(cls.root / 'state'), wiki_root=str(cls.root / 'wiki'))['project']['id']
        cls.server = create_server(home, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host = f'127.0.0.1:{cls.server.server_port}'
        cls.api = f'/api/project/{cls.pid}/code'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.env.stop()
        cls.tmp.cleanup()

    def request(self, method='POST', suffix='/preview', body=b'{}', headers=None, path=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        actual = {'Host': self.host, 'Origin': 'http://' + self.host, 'X-Notebook-Request': '1', 'Content-Type': 'application/json'}
        actual.update(headers or {})
        actual = {k: v for k, v in actual.items() if v is not None}
        try:
            connection.request(method, path or self.api + suffix, body=body, headers=actual)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_origin_header_content_type_and_host(self):
        for headers in [{'Origin': 'https://example.invalid'}, {'Origin': None}, {'X-Notebook-Request': None}, {'Content-Type': 'text/plain'}, {'Host': 'attacker.invalid'}]:
            with self.subTest(headers=headers):
                status, raw = self.request(headers=headers)
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(raw)['code'], 'forbidden_origin')

    def test_body_limit_and_invalid_json(self):
        for body, headers in [(b'{}', {'Content-Length': str(2 * 1024 * 1024 + 1)}), (b'[]', {}), (b'{bad}', {}), (b'\xff', {})]:
            with self.subTest(body=body):
                status, raw = self.request(body=body, headers=headers)
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(raw)['code'], 'invalid_request')

    def test_page_shared_shell_assets_and_no_write(self):
        before = _run_git_command('git', ['rev-parse', 'HEAD'], cwd=self.repo).stdout
        status, page = self.request('GET', path=f'/project/{self.pid}/code')
        self.assertEqual(status, 200)
        html = page.decode('utf-8')
        self.assertEqual(html.count('id="code-app"'), 1)
        self.assertIn('科研进度', html)
        self.assertIn('code.js', html)
        self.assertNotIn('<img src=x', html)
        for asset in ['code.js', 'code.css']:
            self.assertEqual(self.request('GET', path='/static/' + asset)[0], 200)
        for endpoint in ['/status', '/graph?page=0&page_size=100']:
            status, raw = self.request('GET', suffix=endpoint)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(raw)['ok'])
        self.assertEqual(before, _run_git_command('git', ['rev-parse', 'HEAD'], cwd=self.repo).stdout)

    def test_unknown_project_and_path_rejected(self):
        for target in ['/api/project/does-not-exist/code/status', '/api/project/..%2F..%2Fetc/code/status']:
            status, _ = self.request('GET', path=target)
            self.assertGreaterEqual(status, 400)


if __name__ == '__main__':
    unittest.main()
