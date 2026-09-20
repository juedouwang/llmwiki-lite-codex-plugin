"""Isolated real-Git browser acceptance. Never touch registered research repos."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import git_revert_restore  # noqa: E402
from git_service import _run_git_command  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from web_server import create_server  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix='llmwiki-code-browser-') as tmp:
        root = Path(tmp)
        home = str(root / 'home')
        os.environ.update(GIT_CONFIG_GLOBAL=str(root / 'global-empty'), GIT_CONFIG_NOSYSTEM='1')
        def git(repo, *args, data=None):
            return _run_git_command('git', list(args), cwd=repo, input_data=data, text=data is None).stdout

        def project(name, identity=True):
            repo = root / name
            repo.mkdir()
            git(repo, 'init', '-b', 'main')
            git(repo, 'config', 'core.autocrlf', 'false')
            if identity:
                git(repo, 'config', 'user.name', 'Browser Test')
                git(repo, 'config', 'user.email', 'browser@example.test')
            p = register_project(str(repo), name=name, home=home, state_root=str(root / (name + '-state')), wiki_root=str(root / (name + '-wiki')))['project']
            return {'pid': p['id'], 'root': str(repo)}

        def commit(repo, files, message):
            for path, body in files.items():
                (repo / path).write_text(body, encoding='utf-8', newline='')
            git(repo, 'add', '-A')
            git(repo, 'commit', '-m', message)
            return git(repo, 'rev-parse', 'HEAD').strip()

        local = project('local')
        repo = Path(local['root'])
        local['initial'] = commit(repo, {'a.txt': '初始A\n', 'b.txt': '初始B\n', 'c.txt': '初始C\n'}, '初始科研版本')
        (repo / 'a.txt').write_text('选择保存的 A\n', encoding='utf-8', newline='')
        (repo / 'b.txt').write_text('外部暂存 B\n', encoding='utf-8', newline='')
        git(repo, 'add', 'b.txt')
        (repo / 'c.txt').write_text('不选的 C\n', encoding='utf-8', newline='')
        local['b_index'] = git(repo, 'rev-parse', ':b.txt').strip()

        remote = project('remote')
        repo = Path(remote['root'])
        remote['initial'] = commit(repo, {'base.txt': 'base\n'}, '远端基线')
        bare = root / 'remote.git'
        git(root, 'init', '--bare', str(bare))
        git(repo, 'remote', 'add', 'origin', str(bare))
        git(repo, 'push', '-u', 'origin', 'main')
        peer = root / 'peer'
        git(root, 'clone', '-b', 'main', str(bare), str(peer))
        git(peer, 'config', 'user.name', 'Peer Test')
        git(peer, 'config', 'user.email', 'peer@example.test')
        remote['incoming'] = commit(peer, {'remote.txt': '来自另一台机器\n'}, '远端的新版本')
        git(peer, 'push', 'origin', 'main')
        remote.update(bare=str(bare), peer=str(peer))

        conflict = project('conflict')
        repo = Path(conflict['root'])
        commit(repo, {'conflict.txt': 'baseline\n', 'second.txt': 'baseline\n'}, '冲突基线')
        git(repo, 'switch', '-c', 'topic')
        commit(repo, {'conflict.txt': 'incoming\n', 'second.txt': 'incoming\n'}, '实验分支')
        git(repo, 'switch', 'main')
        conflict['before'] = commit(repo, {'conflict.txt': 'current\n', 'second.txt': 'current\n'}, '主线修改')
        identity = project('identity', identity=False)
        (Path(identity['root']) / 'first.txt').write_text('first\n', encoding='utf-8', newline='')

        failure = project('restore-failure')
        repo = Path(failure['root'])
        failure['target'] = commit(repo, {'result.txt': 'old result\n'}, '恢复目标')
        failure['before'] = commit(repo, {'result.txt': 'new result\n'}, '恢复前版本')
        real_restore_git = git_revert_restore._run_git_command
        failure_sent = False

        def restore_git(exe, args, **kwargs):
            # Inject one failing commit only; restore and the later save are real.
            nonlocal failure_sent
            if Path(kwargs.get('cwd', '.')) == repo_failure and args[0] == 'commit' and not failure_sent:
                failure_sent = True
                raise subprocess.CalledProcessError(1, args, stderr='injected commit failure')
            return real_restore_git(exe, args, **kwargs)

        repo_failure = repo
        benchmark = project('benchmark')
        repo = Path(benchmark['root'])
        commands = bytearray()
        for i in range(1000):
            message = f'基准版本 {i}'.encode('utf-8')
            commands.extend(f'commit refs/heads/main\ncommitter Browser Test <browser@example.test> {1700000000+i} +0000\ndata {len(message)}\n'.encode() + message + b'\n')
            if i == 0:
                for j in range(1000):
                    commands.extend(f'M 100644 inline file-{j}.txt\ndata 5\ndata\n\n'.encode())
            commands.extend(b'\n')
        git(repo, 'fast-import', '--quiet', data=bytes(commands))
        git(repo, 'reset', '--hard', 'HEAD')  # Fixture only, never used by product actions.
        evidence = Path(os.environ.get('TEMP', tmp)) / 'llmwiki-code-evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        server = create_server(home, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cfg = {'origin': f'http://127.0.0.1:{server.server_port}', 'local': local, 'remote': remote,
               'conflict': conflict, 'identity': identity, 'failure': failure, 'benchmark': benchmark, 'evidence': str(evidence)}
        try:
            with patch.object(git_revert_restore, '_run_git_command', restore_git):
                result = subprocess.run(['node', str(Path(__file__).with_name('code_browser_test.cjs')), json.dumps(cfg)],
                                    timeout=600, capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
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
