"""Isolated browser verification for the incremental daily workbench."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from llmwiki_registry import register_project  # noqa: E402
from daily_tasks import mutate  # noqa: E402
import research_progress as progress  # noqa: E402
from web_server import create_server  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix='daily-browser-') as temp:
        root = Path(temp)
        home = str(root / 'home')
        today = date.today()
        tomorrow = (today + timedelta(days=1)).isoformat()
        yesterday = (today - timedelta(days=1)).isoformat()
        projects = []
        for name in ('跟踪方案', '野人工作台'):
            source = root / name
            source.mkdir()
            projects.append(register_project(str(source), name=name, home=home, wiki_root=str(root / (name + '-wiki')))['project'])
        project = projects[0]
        mutate(home, {'project_id': project['id'], 'revision': '', 'action': 'plan', 'request_id': 'browser-plan',
                      'task': {'title': '验证低纹理跟踪方案', 'start': today.isoformat(), 'end': tomorrow},
                      'subtasks': [{'title': '准备样例', 'scheduled_date': today.isoformat(), 'estimated_minutes': 60, 'description': '准备并核对五段样例，记录来源。'},
                                   {'title': '运行对比', 'scheduled_date': tomorrow}]}, actor='agent')
        def revision():
            return progress.load_summary(project)['revision']
        tasks = progress.load_summary(project)['tasks']
        child = next(t for t in tasks if t['title'] == '准备样例')
        parent = next(t for t in tasks if t['title'] == '验证低纹理跟踪方案')
        mutate(home, {'project_id': project['id'], 'revision': revision(), 'action': 'submit', 'id': child['id'], 'summary': '样例准备完毕；等待用户检查，不宣称实验有效。'}, actor='agent')
        mutate(home, {'project_id': project['id'], 'revision': revision(), 'action': 'create', 'task': {'title': '昨日未完成', 'scheduled_date': yesterday}}, actor='user')
        mutate(home, {'project_id': projects[1]['id'], 'revision': '', 'action': 'create', 'task': {'title': '跨项目任务', 'scheduled_date': today.isoformat()}}, actor='user')
        evidence = Path(os.environ.get('TEMP', temp)) / 'llmwiki-daily-evidence'
        evidence.mkdir(exist_ok=True)
        config = {'origin': None, 'pid': project['id'], 'pid2': projects[1]['id'], 'child': child['id'], 'parent': parent['id'], 'today': today.isoformat(), 'tomorrow': tomorrow, 'evidence': str(evidence)}
        server = create_server(home, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config['origin'] = f'http://127.0.0.1:{server.server_port}'
        try:
            result = subprocess.run([os.environ.get('NODE', 'node'), str(Path(__file__).with_name('daily_tasks_browser_test.cjs')), json.dumps(config)], timeout=150, capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return result.returncode
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    raise SystemExit(main())
