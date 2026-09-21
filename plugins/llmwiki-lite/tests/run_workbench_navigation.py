"""Retained-column navigation against isolated real repositories, not user projects."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from git_service import _run_git_command  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from llmwiki_core import wiki_write  # noqa: E402
from web_server import create_server  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix='llmwiki-navigation-') as temp:
        root = Path(temp)
        home = str(root / 'home')
        os.environ.update(GIT_CONFIG_GLOBAL=str(root / 'global-empty'), GIT_CONFIG_NOSYSTEM='1')
        projects = []
        for name in ('导航甲', '导航乙'):
            repo = root / name
            repo.mkdir()
            def git(*args):
                return _run_git_command('git', list(args), cwd=repo, timeout=20)
            git('init', '-b', 'main')
            git('config', 'user.name', 'Navigation Test')
            git('config', 'user.email', 'navigation@example.test')
            for i in range(4):
                (repo / 'research.txt').write_text(f'{name} 阶段 {i}\n', encoding='utf-8')
                git('add', 'research.txt')
                git('commit', '-m', f'{name} 阶段 {i}')
            project = register_project(str(repo), name=name, home=home)['project']
            wiki_write(str(repo), 'overview.md', f'# {name}架构\n\n这是真实临时项目的知识。', state_root=project['state_root'])
            projects.append({'pid': project['id'], 'root': str(repo), 'name': name})
        evidence = Path(os.environ.get('TEMP', temp)) / 'llmwiki-navigation-evidence'
        evidence.mkdir(exist_ok=True)
        server = create_server(home, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cfg = {'origin': f'http://127.0.0.1:{server.server_port}', 'projects': projects, 'evidence': str(evidence)}
        try:
            result = subprocess.run(['node', str(Path(__file__).with_name('workbench_navigation_browser_test.cjs')), json.dumps(cfg)],
                timeout=90, capture_output=True, text=True, encoding='utf-8', errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return result.returncode
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    sys.exit(main())
