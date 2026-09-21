"""Browser regression on registered disposable linked worktrees, never user repos."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from test_git_web_worktrees import GitWebWorktreeTests
from web_server import create_server
from llmwiki_registry import unregister_project


def main():
    browser_home = {k: os.environ[k] for k in ("HOME", "USERPROFILE") if k in os.environ}
    fixture = GitWebWorktreeTests()
    try:
        fixture.setUp()  # Isolates HOME, Git templates/config, credentials and protocols.
        fixture.git("branch", "available")
        fixture.write("a.txt", "primary unsaved\n")
        fixture.write("b.txt", "primary staged\n")
        fixture.git("add", "b.txt")
        fixture.write("a.txt", "linked selected\n", fixture.linked)
        fixture.write("b.txt", "linked staged\n", fixture.linked)
        fixture.git("add", "b.txt", repo=fixture.linked)
        fixture.write("c.txt", "linked unselected\n", fixture.linked)
        evidence = Path(os.environ.get("TEMP", str(fixture.root))) / "llmwiki-code-worktrees-evidence"
        evidence.mkdir(exist_ok=True)
        config = {
            "primary": {"pid": fixture.pid, "root": str(fixture.repo)},
            "linked": {"pid": fixture.pid, "root": str(fixture.linked), "worktree": fixture.selected_id()},
            "initial": fixture.initial,
            "primary_index": str(fixture.adapter().gitdir / "index"),
            "evidence": str(evidence),
        }
        unregister_project(fixture.lpid, home=str(fixture.home))
        server = create_server(str(fixture.home), port=0)
        server.daemon_threads = False
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        config["origin"] = f"http://127.0.0.1:{server.server_port}"
        try:
            result = subprocess.run(
                [os.environ.get("NODE", "node"), str(Path(__file__).with_name("code_worktrees_browser_test.cjs")), json.dumps(config)],
                timeout=240, capture_output=True, text=True, encoding="utf-8", errors="replace",
                # Chrome needs the OS home to locate its installation, but uses
                # Playwright's fresh temporary profile. Git config stays isolated.
                env={**os.environ, **browser_home},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            return result.returncode
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    sys.exit(main())
