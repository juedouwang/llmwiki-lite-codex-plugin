"""A-25: a 60-second unresolved host lease does not block local HTTP reads/writes."""

import json
from pathlib import Path
import platform
import sys
import tempfile
import threading
import time
from urllib.request import Request, urlopen
from uuid import uuid4
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llmwiki_registry import register_project  # noqa: E402
from web_server import create_server  # noqa: E402
import knowledge_maintenance as km  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="llmwiki-knowledge-perf-") as tmp:
        root = Path(tmp)
        home = str(root / "home")
        source = root / "source"
        source.mkdir()
        (source / "main.py").write_text("return 42", encoding="utf-8")
        p = register_project(str(source), home=home)["project"]
        (Path(p["wiki_root"]) / "test.md").write_text(
            "# 测试\n\n" + ("normal text\n" * 2000), encoding="utf-8"
        )
        run = km.knowledge_plan("manual", p["id"], home=home)["run_id"]
        server = create_server(home, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        api = f"/api/project/{p['id']}"
        headers = {
            "Content-Type": "application/json",
            "Origin": origin,
            "X-Notebook-Request": "1",
        }
        note = uuid4().hex
        revision = ""
        samples = {"read": [], "save": [], "status": []}

        def request(route, payload=None):
            req = Request(
                origin + route,
                json.dumps(payload).encode() if payload is not None else None,
                headers,
            )
            with urlopen(req, timeout=5) as res:
                raw = res.read()
                return (
                    json.loads(raw)
                    if res.headers.get("Content-Type", "").startswith(
                        "application/json"
                    )
                    else raw
                )

        try:
            request(f"/project/{p['id']}/page/test.md")
            request(api + "/knowledge-maintenance")
            start = time.monotonic()
            # Host does nothing for >=60s. No mock sleep occupies HTTP handlers.
            with patch.object(
                km, "scan", side_effect=AssertionError("foreground scanned sources")
            ):
                for i in range(20):
                    for label, route, payload in [
                        ("read", f"/project/{p['id']}/page/test.md", None),
                        (
                            "save",
                            api + "/notebook/" + note,
                            {
                                "revision": revision,
                                "document": {
                                    "title": "性能测试",
                                    "blocks": [
                                        {
                                            "id": "body",
                                            "type": "markdown",
                                            "text": "note " + str(i),
                                        }
                                    ],
                                },
                            },
                        ),
                        ("status", api + "/knowledge-maintenance", None),
                    ]:
                        tick = time.perf_counter()
                        res = request(route, payload)
                        samples[label].append((time.perf_counter() - tick) * 1000)
                        if label == "save":
                            revision = res["revision"]
                    time.sleep(max(0, start + (i + 1) * 3 - time.monotonic()))
            result = {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "host_pending_seconds": round(time.monotonic() - start, 2),
                "run_unchanged": km.state(p)["active_run"] == run,
                "requests": {},
            }
            for name, values in samples.items():
                p95 = sorted(values)[18]
                result["requests"][name] = {
                    "count": 20,
                    "p95_ms": round(p95, 2),
                    "max_ms": round(max(values), 2),
                }
                assert p95 <= 500, (name, p95)
            assert result["run_unchanged"] and result["host_pending_seconds"] >= 60
            output = (
                Path(tempfile.gettempdir())
                / "llmwiki-knowledge-evidence/performance.json"
            )
            output.parent.mkdir(exist_ok=True)
            output.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps(result))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
