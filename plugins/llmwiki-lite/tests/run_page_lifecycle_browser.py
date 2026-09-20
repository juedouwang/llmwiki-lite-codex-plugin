"""Isolated browser checks for cached-main lifecycle contracts; no user data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from llmwiki_registry import register_project  # noqa: E402
from web_server import create_server  # noqa: E402
import research_reports as reports  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix='llmwiki-page-lifecycle-') as tmp:
        source = Path(tmp) / 'source'
        source.mkdir()
        home = str(Path(tmp) / 'home')
        project = register_project(str(source), name='页面生命周期隔离测试', home=home)['project']
        reports.create(reports.workspace(home), 'daily', '2026-09-19', [project['id']], home=home)
        server = create_server(home, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = {'origin': f'http://127.0.0.1:{server.server_port}', 'pid': project['id']}
        try:
            return subprocess.run(['node', str(Path(__file__).with_name('page_lifecycle_browser_test.cjs')), json.dumps(config)], timeout=120, env=os.environ.copy()).returncode
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    sys.exit(main())
