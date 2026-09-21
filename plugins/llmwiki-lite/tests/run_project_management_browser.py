"""Run project management UI checks against disposable registry and sources."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llmwiki_registry import register_project
from web_server import create_server


def main():
    with tempfile.TemporaryDirectory(prefix="llmwiki-project-ui-") as temp:
        root = Path(temp)
        home = str(root / "home")
        ids = []
        for name in ["A default", "Z current"]:
            source = root / name
            source.mkdir()
            p = register_project(
                str(source),
                name=name,
                home=home,
                wiki_root=str(root / (name + "-wiki")),
                state_root=str(root / (name + "-state")),
            )["project"]
            ids.append(p["id"])
        server = create_server(home, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(
                [
                    "node",
                    str(
                        Path(__file__).with_name("project_management_browser_test.cjs")
                    ),
                    f"http://127.0.0.1:{server.server_port}",
                    *ids,
                ],
                timeout=150,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            return result.returncode
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    raise SystemExit(main())
