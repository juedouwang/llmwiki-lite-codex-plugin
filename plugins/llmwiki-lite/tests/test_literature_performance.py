"""Opt-in D-08 performance evidence, production HTTP, only temporary projects.

Runs each operation once warm and twenty measured times for each of two isolated
5000-item catalogs, then repeats while a simulated host waits a full 60 seconds.
No real model, task or automation is launched; no new dependency is installed.
"""
import copy
import http.client
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def main():
    from llmwiki_registry import register_project
    from literature_catalog import LiteratureCatalog, literature_collect
    from web_server import create_server
    with tempfile.TemporaryDirectory(prefix="llmwiki-literature-performance-") as tmp:
        roots, servers, threads, cases = [], [], [], []
        for index in range(2):
            root = Path(tmp) / str(index)
            source = root / "source"
            source.mkdir(parents=True)
            roots.append(source)
            home = str(root / "home")
            project = register_project(str(source), name=f"Perf-{index}", wiki_root=str(root / "wiki"), state_root=str(root / "state"), home=home)["project"]
            first = literature_collect(project["id"], "10.1234/seed", "a" * 32, home=home)
            catalog = LiteratureCatalog(project["wiki_root"])
            template = catalog.get_item(first["item_id"])
            data = catalog._load_catalog()
            data["items"] = []
            for i in range(5000):
                item = copy.deepcopy(template)
                item.update(id=f"{i:032x}", title=f"Performance paper {i}")
                item["identifiers"]["doi"] = f"10.1234/perf-{i}"
                item["urls"][0]["url"] = f"https://doi.org/10.1234/perf-{i}"
                item["attachments"] = [{"path": f"not-read-{i}.pdf", "sha256": "0" * 64}]
                data["items"].append(item)
            catalog._save_catalog(data)
            server = create_server(home, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            servers.append(server)
            threads.append(thread)
            from literature_catalog import item_revision
            cases.append({"pid": project["id"], "port": server.server_port,
                          "revision": item_revision(data["items"][0]), "catalog_bytes": catalog.catalog_path.stat().st_size})
        report = {"environment": {"platform": platform.platform(), "python": platform.python_version(), "cpu": os.environ.get("PROCESSOR_IDENTIFIER", "unknown")},
                  "catalogs": [{"initial_items": 5000, "bytes": c["catalog_bytes"], "independent_home": True} for c in cases],
                  "samples_per_operation": 20, "warmups": 1, "production_routes": True, "metrics": []}
        original_stat = Path.stat
        import socket
        original_connect = socket.socket.connect
        ports = {c["port"] for c in cases}

        def guarded_stat(path, *args, **kwargs):
            if any(source in path.parents for source in roots):
                raise AssertionError("frontend statted source content")
            return original_stat(path, *args, **kwargs)

        def guarded_connect(sock, address):
            if not isinstance(address, tuple) or address[0] != "127.0.0.1" or address[1] not in ports:
                raise AssertionError("unexpected frontend network")
            return original_connect(sock, address)

        def request(case, operation):
            api = f"/api/project/{case['pid']}/literature/"
            payload = None
            if operation == "list":
                route = f"/project/{case['pid']}/literature"
            elif operation == "add":
                route = api + "add"
                unique = uuid.uuid4().hex
                payload = {"locator": f"10.1234/{unique}", "request_id": unique}
            else:
                route = api + "item/" + "0" * 32 + "/update"
                payload = {"title": "Saved " + uuid.uuid4().hex, "locator": "10.1234/perf-0", "authors": [], "year": None,
                           "expected_item_revision": case["revision"]}
            conn = http.client.HTTPConnection("127.0.0.1", case["port"], timeout=10)
            start = time.perf_counter()
            try:
                conn.request("GET" if payload is None else "POST", route,
                             body=None if payload is None else json.dumps(payload),
                             headers={"Origin": f"http://127.0.0.1:{case['port']}", "Content-Type": "application/json", "X-Literature-Request": "1"})
                response = conn.getresponse()
                raw = response.read()
                elapsed = time.perf_counter() - start
                assert response.status == 200, (operation, response.status, raw[:200])
                if payload is None:
                    assert raw.count(b"data-item href=") == 50
                elif operation == "save":
                    case["revision"] = json.loads(raw)["item_revision"]
                return elapsed
            finally:
                conn.close()

        def measure(mode, waiting=None):
            for index, case in enumerate(cases):
                for operation in ("list", "add", "save"):
                    request(case, operation)
                    samples = []
                    for _ in range(20):
                        if waiting is not None:
                            assert waiting.is_alive(), "simulated host returned during measurement"
                        samples.append(request(case, operation))
                    median = statistics.median(samples)
                    p95 = sorted(samples)[18]
                    report["metrics"].append({"mode": mode, "project": index, "operation": operation,
                                               "median_ms": round(median * 1000, 2), "p95_ms": round(p95 * 1000, 2)})
                    assert median <= 1 and p95 <= 2, report["metrics"][-1]
        try:
            with patch.object(Path, "rglob", side_effect=AssertionError("frontend source scan")), \
                 patch.object(Path, "stat", guarded_stat), \
                 patch.object(socket.socket, "connect", guarded_connect), \
                 patch("subprocess.Popen", side_effect=AssertionError("frontend process")):
                measure("idle")
                start = time.monotonic()
                waiting = threading.Thread(target=lambda: time.sleep(60), daemon=True)
                waiting.start()
                measure("host_waiting_60s", waiting)
                waiting.join()
                report["simulated_host_wait_seconds"] = round(time.monotonic() - start, 2)
            report["ok"] = True
            evidence = Path(os.environ.get("TEMP", tmp)) / "llmwiki-literature-evidence"
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "literature-performance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join()


if __name__ == "__main__":
    main()
