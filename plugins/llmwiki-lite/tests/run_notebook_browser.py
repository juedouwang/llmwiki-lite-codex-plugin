"""Optional browser harness. Uses an existing Node/Playwright install, never installs one."""

import base64
from datetime import date, timedelta
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from llmwiki_registry import register_project  # noqa: E402
from web_server import create_server  # noqa: E402
from llmwiki_core import wiki_write  # noqa: E402
from research_records import write_record  # noqa: E402
import research_notebook as notebook  # noqa: E402
import research_progress as progress  # noqa: E402


def _png_bytes() -> bytes:
    """A real 1x1 PNG so the upload endpoint's own checks pass without an image library."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def _seed_task(project: dict, **task) -> dict:
    revision = progress.load_summary(project)["revision"]
    return progress.update(project, {"action": "create", "revision": revision, "task": task})


def _legacy_task(
    task_id: str, title: str, status: str, stamp: str, checkpoint: str = "", updated: str = ""
) -> dict:
    """A task shaped like the old writer wrote it: no completed_at, no context_mode.

    `updated` deliberately differs from `stamp` for the done fixture: an old task with a
    newer edit time must still sort by completion time, not by when it was last touched.
    """
    fields = {
        "title": title,
        "status": status,
        "start": "",
        "end": "",
        "checkpoint": checkpoint,
        "next_step": "",
        "record_id": "",
    }
    return {
        "id": task_id,
        **fields,
        "created_at": stamp,
        "updated_at": updated or stamp,
        "history": [{"at": stamp, **fields, "completed_at": None}],
    }


def _write_legacy_tasks(wiki_root: str, tasks: list[dict]) -> Path:
    folder = Path(wiki_root) / ".research-progress"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "tasks.json"
    target.write_text(
        json.dumps({"version": 1, "tasks": tasks}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def _append_legacy_task(wiki_root: str, task: dict) -> Path:
    target = Path(wiki_root) / ".research-progress" / "tasks.json"
    data = (
        json.loads(target.read_text(encoding="utf-8"))
        if target.exists()
        else {"version": 1, "tasks": []}
    )
    data["tasks"].append(task)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def _tool_error(call_id: int, message: str) -> dict:
    """A harness failure must still look like a tool result.

    Returning a bare {"ok": false} object instead would make the browser see
    rpc.result as undefined, so every "this must be rejected" assertion in the
    illegal-submission sweep would fail for the wrong reason.
    """
    return {
        "jsonrpc": "2.0",
        "id": call_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"ok": False, "error": message}, ensure_ascii=False),
                }
            ],
            "isError": True,
        },
    }


def _publish(target: Path, payload: dict) -> None:
    """Write then rename: the browser polls for this file and must never read a partial one."""
    temporary = target.with_name(target.name.replace(".json", ".tmp"))
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)


def _mcp_call(
    scripts_dir: Path, home: str, tool: str, arguments: dict, call_id: int
) -> dict:
    """Start one MCP server and issue one real `tools/call` over stdio, as a host would."""
    message = {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    env = dict(os.environ)
    env["LLMWIKI_HOME"] = home
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(scripts_dir)
    try:
        done = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(scripts_dir / "mcp_server.py")],
            input=json.dumps(message, ensure_ascii=False) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            # Shorter than the browser's own deadline, so a slow child still yields a result.
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return _tool_error(call_id, "injector: MCP 调用超时")
    lines = [line for line in (done.stdout or "").splitlines() if line.strip()]
    if not lines:
        return _tool_error(call_id, "injector: MCP 无响应：" + (done.stderr or "")[-400:])
    return json.loads(lines[-1])


def _injector(inject_dir: Path, scripts_dir: Path, home: str, deadline: float, stop: threading.Event) -> None:
    """Test-only stand-in for a future producer.

    The browser drops `<id>.request.json` files saying "deliver this result now";
    each one is answered by starting a real MCP server and calling the tool. Every
    submission — legal, duplicate or illegal — goes through the same public entry
    point a real client would use. There is no scheduler, model call or retry loop
    here, and none is being claimed.
    """
    while not stop.is_set() and time.monotonic() < deadline:
        pending = sorted(
            inject_dir.glob("*.request.json"),
            key=lambda item: int(item.name.split(".")[0]),
        )
        for request in pending:
            if stop.is_set():
                break
            result = request.with_name(request.name.replace(".request.json", ".result.json"))
            if result.exists():
                continue
            call_id = int(request.name.split(".")[0])
            try:
                call = json.loads(request.read_text(encoding="utf-8"))
                payload = _mcp_call(scripts_dir, home, call["tool"], call["arguments"], call_id)
            except Exception as exc:  # reported to the browser instead of hanging it
                payload = _tool_error(call_id, f"injector: {type(exc).__name__}: {exc}")
            _publish(result, payload)
        stop.wait(0.05)


with tempfile.TemporaryDirectory(prefix="llmwiki-browser-") as folder:
    root = Path(folder)
    (root / "source").mkdir()
    home = str(root / "home")
    project = register_project(
        str(root / "source"),
        name="科研笔记体验测试",
        home=home,
        wiki_root=str(root / "wiki"),
    )["project"]
    wiki_write(str(root / "source"), "experiment.md", "# 低纹理配准实验\n\n## 实验设置\n固定随机种子，比较基线与改进模型。\n\n| 方法 | 误差 |\n| --- | --- |\n| 基线 | 0.082 |\n| 改进 | 0.046 |\n", state_root=project["state_root"])
    refs = root / "source" / "references"
    refs.mkdir()
    # Sufficient for route/viewer tests; no PDF parsing dependency required.
    (refs / "demo-paper.pdf").write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n")
    wiki_write(str(root / "source"), "paper-reading.md", "---\ntitle: 几何约束方法精读\ntype: literature-note\npaper_file: references/demo-paper.pdf\n---\n\n# 几何约束方法精读\n\n## 关键结论\n需要复现实验验证。\n", state_root=project["state_root"])
    write_record(str(root / "source"), "实验设计与阶段结论", "弱纹理场景仍需补充跨场景验证。", tags=["实验"], next_steps=["补充夜间数据", "运行消融实验"], related_pages=["experiment.md"], state_root=project["state_root"])

    # --- Progress acceptance fixture ------------------------------------------------
    # A dedicated project so the progress assertions never depend on what the site or
    # notebook flows left behind in the shared project above.
    (root / "progress-source").mkdir()
    progress_project = register_project(
        str(root / "progress-source"),
        name="进度验收项目",
        home=home,
        wiki_root=str(root / "progress-wiki"),
    )["project"]
    today = date.today()
    one_week = (today + timedelta(days=6)).isoformat()
    # Four in-progress tasks. The fourth starts with no context at all so its fields
    # default to "auto" and the injected summary is visible without any manual edit.
    _seed_task(
        progress_project,
        title="夜间数据验证",
        status="active",
        start=today.isoformat(),
        end=one_week,
        checkpoint="已跑完基线，夜间样本未跑",
        next_step="先检查昨晚日志",
    )
    _seed_task(progress_project, title="消融实验整理", status="active", checkpoint="整理了两组消融", next_step="补第三组")
    _seed_task(progress_project, title="跨场景泛化分析", status="active", checkpoint="跨场景指标待汇总", next_step="汇总晚间结果")
    _seed_task(progress_project, title="自动整理接入验证", status="active")
    # Two more tasks with no context at all: their fields start owned by "auto",
    # which is the precondition the A-08 and A-09/A-10 scenarios need.
    _seed_task(progress_project, title="人工覆盖与自动版本", status="active")
    _seed_task(progress_project, title="提交边界与版本", status="active")
    _seed_task(progress_project, title="文献综述初稿", status="done")
    legacy_stamp = "2026-08-01T02:00:00Z"
    _append_legacy_task(
        progress_project["wiki_root"],
        _legacy_task(
            uuid4().hex,
            "旧完成事项（无完成时间）",
            "done",
            legacy_stamp,
            checkpoint="旧文件里的手写内容",
            # Newer than every completion the test records, so ordering by "last edited"
            # would put this task first and the assertion would notice.
            updated="2027-06-01T00:00:00Z",
        ),
    )
    # A record the progress page can import from, and link to.
    record = write_record(
        str(root / "progress-source"),
        "进度验收记录",
        "夜间数据仍需补测。",
        tags=["实验"],
        next_steps=["补齐夜间数据"],
        state_root=progress_project["state_root"],
    )["record"]
    # A manual notebook that embeds a screenshot, so A-11 can prove linking never
    # rewrites the note or its image.
    uploaded = notebook.upload(
        progress_project, {"data": base64.b64encode(_png_bytes()).decode("ascii")}
    )["image"]
    note_id = uuid4().hex
    notebook.save(
        progress_project,
        note_id,
        {
            "revision": "",
            "document": {
                "title": "含截图的实验笔记",
                "tags": ["实验"],
                "blocks": [
                    {"id": "block-1", "type": "markdown", "text": "## 观察\n加入几何约束后误差下降。"},
                    {"id": "block-2", "type": "image", "image": uploaded, "text": "图 1 · 实验界面截图", "comments": ["还需补夜间场景"]},
                ],
            },
        },
    )
    note_rel = f"records/manual/{note_id}.md"
    image_rel = f"records/assets/{uploaded}"

    # B has never been worked on: only a task the user has not started.
    (root / "progress-source-b").mkdir()
    other_project = register_project(
        str(root / "progress-source-b"),
        name="进度验收项目 B",
        home=home,
        wiki_root=str(root / "progress-wiki-b"),
    )["project"]
    _seed_task(other_project, title="B 的未开始任务", status="planned")

    # L holds nothing but an old-format file the browser may only read.
    (root / "progress-source-legacy").mkdir()
    legacy_project = register_project(
        str(root / "progress-source-legacy"),
        name="旧数据只读项目",
        home=home,
        wiki_root=str(root / "progress-wiki-legacy"),
    )["project"]
    legacy_file = _write_legacy_tasks(
        legacy_project["wiki_root"],
        [_legacy_task(uuid4().hex, "旧文件里的已完成任务", "done", legacy_stamp)],
    )
    legacy_before = legacy_file.read_bytes()

    server = create_server(home, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    node = os.environ.get("NODE", "node")
    origin = f"http://127.0.0.1:{server.server_port}"
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    inject_dir = root / "inject"
    inject_dir.mkdir()
    stop_injector = threading.Event()
    injector = threading.Thread(
        target=_injector,
        args=(inject_dir, scripts_dir, home, time.monotonic() + 900, stop_injector),
        daemon=True,
    )
    injector.start()
    progress_args = json.dumps(
        {
            "origin": origin,
            "projectId": progress_project["id"],
            "otherProjectId": other_project["id"],
            "legacyProjectId": legacy_project["id"],
            "wikiRoot": progress_project["wiki_root"],
            "otherWikiRoot": other_project["wiki_root"],
            "projectRoot": progress_project["source_root"],
            "stateRoot": progress_project["state_root"],
            "noteRelPath": note_rel,
            "imageRelPath": image_rel,
            "recordRelPath": record["path"],
            "injectDir": str(inject_dir),
        },
        ensure_ascii=False,
    )
    try:
        if "--notebook-only" not in sys.argv:
            site_result = subprocess.run([
                node,
                str(Path(__file__).with_name("site_browser_test.cjs")),
                origin,
                project["id"],
            ], timeout=120)
            if site_result.returncode:
                raise SystemExit(site_result.returncode)
            progress_result = subprocess.run(
                [node, str(Path(__file__).with_name("progress_browser_test.cjs")), progress_args],
                timeout=240,
            )
            if progress_result.returncode:
                raise SystemExit(progress_result.returncode)
        result = subprocess.run(
            [
                node,
                str(Path(__file__).with_name("notebook_browser_test.cjs")),
                origin,
                project["id"],
            ],
            timeout=150,
        )
    finally:
        stop_injector.set()
        injector.join()
        server.shutdown()
        server.server_close()
        worker.join()
    # A-14: the old-format project was only ever read. Nothing may have rewritten it.
    if legacy_file.read_bytes() != legacy_before:
        raise SystemExit("旧格式任务文件在只读访问后被改写。")
    print("Legacy read-only project unchanged:", legacy_file)
    sys.exit(result.returncode)
