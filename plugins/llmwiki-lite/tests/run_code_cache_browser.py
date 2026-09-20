"""Independent real-Git regression for display-only commit caching.

All repositories and the registry are temporary; no installed service is reused.
"""
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
from web_server import create_server  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix='llmwiki-code-cache-') as tmp:
        root = Path(tmp)
        home = str(root / 'home')
        os.environ.update(GIT_CONFIG_GLOBAL=str(root / 'global-empty'), GIT_CONFIG_NOSYSTEM='1')

        def git(repo, *args):
            return _run_git_command('git', list(args), cwd=repo).stdout.strip()

        source = root / 'first'
        source.mkdir()
        git(source, 'init', '-b', 'main')
        git(source, 'config', 'core.autocrlf', 'false')
        git(source, 'config', 'user.name', 'Cache Test')
        git(source, 'config', 'user.email', 'cache@example.test')
        commits = []
        for index in range(8):
            (source / 'result.txt').write_text(f'experiment {index}\n', encoding='utf-8')
            git(source, 'add', 'result.txt')
            git(source, 'commit', '-m', f'真实阶段 {index}')
            commits.insert(0, git(source, 'rev-parse', 'HEAD'))
        other = root / 'second'
        git(root, 'clone', '--no-hardlinks', str(source), str(other))
        # This local clone has identical OIDs but a different project binding.
        projects = []
        for repo in (source, other):
            project = register_project(str(repo), name=repo.name, home=home,
                                       state_root=str(root / (repo.name + '-state')),
                                       wiki_root=str(root / (repo.name + '-wiki')))['project']
            projects.append({'pid': project['id'], 'root': str(repo)})
        server = create_server(home, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = {'origin': f'http://127.0.0.1:{server.server_port}',
                  'projects': projects, 'commits': commits}
        try:
            result = subprocess.run(
                ['node', str(Path(__file__).with_name('code_cache_browser_test.cjs')), json.dumps(config)],
                timeout=180, capture_output=True, text=True, encoding='utf-8', errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return result.returncode


if __name__ == '__main__':
    sys.exit(main())
