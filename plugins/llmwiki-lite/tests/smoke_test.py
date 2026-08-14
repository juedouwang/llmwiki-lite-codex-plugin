"""End-to-end smoke tests for the lightweight LLM Wiki plugin."""

from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import quote, urlencode

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from llmwiki_core import (  # noqa: E402
    LLMWikiError,
    init_project,
    read_file,
    search,
    snapshot,
    status,
    wiki_check,
    wiki_list,
    wiki_write,
)
from llmwiki_registry import (  # noqa: E402
    get_project,
    register_project,
    select_project,
    unregister_project,
    update_project_storage,
    update_settings,
)
from markdown_renderer import render_markdown  # noqa: E402
from research_records import list_records, read_record, write_record  # noqa: E402
from web_server import create_server  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_source(root: Path, name: str) -> Path:
    source = root / name
    (source / "src").mkdir(parents=True)
    (source / "src/main.py").write_text(
        "from pipeline import run\n\ndef main():\n    return run('demo')\n",
        encoding="utf-8",
    )
    (source / "src/pipeline.py").write_text(
        "def run(name):\n    return f'hello {name}'\n", encoding="utf-8"
    )
    return source


def test_core(root: Path) -> None:
    source = make_source(root, "core-project")
    init = init_project(str(source))
    require(init["ok"], "init failed")
    first = snapshot(str(source))
    require(first["file_count"] == 2, "snapshot should exclude state and Wiki")
    hits = search(str(source), "def run")
    require(hits["results"][0]["path"] == "src/pipeline.py", "search failed")
    opened = read_file(str(source), "src/main.py", start_line=1, end_line=3)
    require("def main" in opened["content"], "bounded read failed")
    wiki_write(
        str(source),
        "architecture.md",
        "---\ntitle: Architecture\nsources:\n  - src/main.py\n---\n\n# Architecture\n\nSee [[pipeline]].\n",
    )
    wiki_write(
        str(source),
        "pipeline.md",
        "---\ntitle: Pipeline\nsources:\n  - src/pipeline.py\n---\n\n# Pipeline\n",
    )
    require(
        len(wiki_list(str(source))["pages"]) == 3, "Wiki index and pages should exist"
    )
    checked = wiki_check(str(source))
    require(
        not checked["broken_links"] and not checked["missing_sources"],
        "Wiki checks failed",
    )
    (source / "src/pipeline.py").write_text(
        "def run(name):\n    return f'HELLO {name}'\n", encoding="utf-8"
    )
    require(
        snapshot(str(source), save=False)["changes"]["modified"] == ["src/pipeline.py"],
        "modified file not detected",
    )
    require(
        status(str(source))["snapshot_file_count"] == 2, "preview replaced baseline"
    )


def test_registry(root: Path) -> tuple[Path, Path, dict]:
    home = root / "home"
    source1 = make_source(root, "registered-one")
    first = register_project(str(source1), home=str(home))
    require(
        Path(first["project"]["wiki_root"]) == source1 / "wiki",
        "new-user default must be <project>/wiki",
    )
    require(
        register_project(str(source1), home=str(home))["existing"],
        "duplicate registration not detected",
    )
    default = root / "obsidian"
    update_settings(home=str(home), default_wiki_root=str(default), web_port=9123)
    source2 = make_source(root, "registered-two")
    second = register_project(str(source2), home=str(home))
    record = second["project"]
    require(
        Path(record["wiki_root"]).parent == default, "configured default root not used"
    )
    require(
        get_project(current_path=str(source2 / "src"), home=str(home))["project"]["id"]
        == record["id"],
        "current path resolution failed",
    )
    select_project(record["id"], home=str(home))
    wiki_write(
        str(source2),
        "guide.md",
        "---\ntitle: Guide\nsources:\n  - src/main.py\n---\n\n# Guide\n\n[[Other]]\n",
        state_root=record["state_root"],
    )
    moved_wiki = root / "moved/wiki"
    moved_state = root / "moved/state"
    moved = update_project_storage(
        record["id"],
        home=str(home),
        wiki_root=str(moved_wiki),
        state_root=str(moved_state),
    )
    require((moved_wiki / "guide.md").is_file(), "Wiki was not copied")
    require(Path(moved["previous_wiki_root"]).exists(), "old Wiki should be preserved")
    require(
        Path(moved["previous_state_root"]).exists(), "old state should be preserved"
    )
    record = moved["project"]
    removed = unregister_project(record["id"], home=str(home))
    require(
        not removed["files_deleted"] and moved_wiki.exists(), "unregister deleted files"
    )
    record = register_project(
        str(source2),
        home=str(home),
        wiki_root=str(moved_wiki),
        state_root=str(moved_state),
    )["project"]
    return home, source2, record


def test_renderer() -> None:
    text = """---\ntitle: Demo\ntags:\n  - test\n---\n# Heading\n\n- [x] done\n- item\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n> [!NOTE] Note\n> visible\n\n```python\nprint("<safe>")\n```\n\n[[Other Page]] and ![local](img.png)\n"""
    rendered = render_markdown(text, "demo", "index.md")
    for token in (
        "frontmatter",
        "<table>",
        "checkbox",
        "callout",
        "code-language",
        "wikilink",
        "/asset/img.png",
        "&lt;safe&gt;",
    ):
        require(token in rendered, f"renderer missing {token}")


def test_research_records(source: Path, record: dict) -> dict:
    first = write_record(
        str(source),
        title="\u9636\u6bb5\u6027\u7406\u89e3\uff1a\u5b9e\u9a8c\u8bbe\u8ba1",
        understanding="\u5f53\u524d\u8bc1\u636e\u652f\u6301\u5148\u8c03\u6574\u91c7\u6837\u65b9\u6848\uff0c\u518d\u8fdb\u884c\u4e0b\u4e00\u8f6e\u5bf9\u7167\u5b9e\u9a8c\u3002",
        discussion_context="\u56f4\u7ed5\u5b9e\u9a8c\u53d8\u91cf\u3001\u6837\u672c\u91cf\u548c\u4e0b\u4e00\u8f6e\u9a8c\u8bc1\u65b9\u5f0f\u4e0e Codex \u8ba8\u8bba\u3002",
        evidence=["src/main.py", "references/demo-paper.pdf"],
        conclusion="\u5148\u5b8c\u6210\u5c0f\u89c4\u6a21\u590d\u73b0\u5b9e\u9a8c\uff0c\u518d\u51b3\u5b9a\u662f\u5426\u6269\u5927\u6837\u672c\u3002",
        decisions=["\u4fdd\u7559\u5f53\u524d\u57fa\u7ebf", "\u4e0b\u4e00\u8f6e\u589e\u52a0\u5bf9\u7167\u7ec4"],
        open_questions=["\u91c7\u6837\u504f\u5dee\u662f\u5426\u4f1a\u5f71\u54cd\u7ed3\u8bba\uff1f"],
        next_steps=["\u8865\u5145\u5b9e\u9a8c\u8bb0\u5f55", "\u6838\u5bf9\u8bba\u6587\u4e2d\u7684\u8bc4\u4ef7\u6307\u6807"],
        related_files=["src/main.py"],
        related_pages=["guide.md"],
        tags=["\u5b9e\u9a8c", "\u9636\u6bb5\u6027\u7406\u89e3"],
        project_id=record["id"],
        recorded_at="2026-08-04T12:30:00Z",
        state_root=record["state_root"],
    )
    second = write_record(
        str(source),
        title="\u9636\u6bb5\u6027\u7406\u89e3\uff1a\u5b9e\u9a8c\u8bbe\u8ba1",
        understanding="\u8ffd\u52a0\u8bb0\u5f55\uff1a\u9700\u8981\u5148\u6838\u5bf9\u8bc4\u4ef7\u6307\u6807\u5b9a\u4e49\u3002",
        project_id=record["id"],
        recorded_at="2026-08-04T12:30:00Z",
        state_root=record["state_root"],
    )
    first_path = Path(record["wiki_root"]) / first["record"]["path"]
    second_path = Path(record["wiki_root"]) / second["record"]["path"]
    require(first_path.is_file() and second_path.is_file(), "research record files missing")
    require(first_path == second_path, "same-day records should share one daily file")
    require(
        first["record"]["path"] == second["record"]["path"] == "records/2026/08/2026-08-04.md",
        "daily record path is not YYYY/MM/YYYY-MM-DD.md",
    )
    require(
        first["record"]["id"] != second["record"]["id"]
        and first["record"].get("entry_key") != second["record"].get("entry_key"),
        "same-day entries should have distinct fragment IDs",
    )
    daily_text = first_path.read_text(encoding="utf-8")
    require(
        daily_text.count("## 12:30\uff5c\u9636\u6bb5\u6027\u7406\u89e3\uff1a\u5b9e\u9a8c\u8bbe\u8ba1") == 2
        and daily_text.count("<!-- llmwiki-record-entry ") == 2,
        "daily record did not append both entries",
    )
    listed_all = list_records(str(source), state_root=record["state_root"], max_records=20)
    require(listed_all["count"] == 2, "daily record listing should expose both entries")
    listed = list_records(
        str(source), state_root=record["state_root"], query="\u91c7\u6837", max_records=20
    )
    require(listed["count"] == 1, "research record search failed")
    loaded = read_record(
        str(source), first["record"]["id"], state_root=record["state_root"]
    )
    require(
        "\u9636\u6bb5\u6027\u7406\u89e3" in loaded["record"]["content"]
        and "\u8c03\u6574\u91c7\u6837\u65b9\u6848" in loaded["record"]["content"],
        "research record content failed",
    )
    try:
        read_record(str(source), first["record"]["path"], state_root=record["state_root"])
    except LLMWikiError:
        pass
    else:
        raise AssertionError("reading a multi-entry daily file without #entry_key should fail")
    try:
        read_record(str(source), "../../secret.md", state_root=record["state_root"])
    except LLMWikiError:
        pass
    else:
        raise AssertionError("research record path traversal was not rejected")
    return first


def request(
    connection: HTTPConnection,
    method: str,
    path: str,
    body: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict]:
    request_headers = dict(headers or {})
    if body is not None:
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    return response.status, response.read(), dict(response.headers)


def test_web(
    root: Path, home: Path, source: Path, record: dict, research_record: dict
) -> None:
    wiki_write(
        str(source), "other-page.md", "# Other Page\n", state_root=record["state_root"]
    )
    wiki_write(
        str(source),
        "demo.md",
        "---\ntitle: Demo\n---\n\n# Demo\n\n- [x] task\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n[[other-page]]\n",
        state_root=record["state_root"],
    )
    references = source / "references"
    references.mkdir(parents=True, exist_ok=True)
    demo_pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
    (references / "demo-paper.pdf").write_bytes(demo_pdf)
    wiki_write(
        str(source),
        "demo-paper-reading.md",
        "---\ntitle: Demo Paper 中文精读\ntype: literature-note\nlanguage: zh-CN\npaper_file: references/demo-paper.pdf\nsources:\n  - references/demo-paper.pdf\n---\n\n# Demo Paper 中文精读\n\n## 一句话结论\n这是用于文献中心测试的中文辅助阅读。\n",
        state_root=record["state_root"],
    )
    server = create_server(home=str(home), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    connection = HTTPConnection(host, port, timeout=5)
    try:
        original_stderr = sys.stderr
        sys.stderr = None
        try:
            code, body, _ = request(connection, "GET", "/")
        finally:
            sys.stderr = original_stderr
        home_text = body.decode("utf-8")
        require(
            code == 200
            and "registered-two" in home_text
            and "中文科研知识工作台" in home_text
            and "我的研究项目" in home_text,
            "Chinese research home page failed",
        )
        for token in ("console-shell", "console-topbar", "console-sidebar", "console-project-table"):
            require(token in home_text, f"console shell missing {token}")
        code, body, _ = request(connection, "GET", f"/project/{record['id']}")
        project_text = body.decode("utf-8")
        for token in ("项目研究台", "研究内容", "研究总览", "实验记录", "建议下一步"):
            require(token in project_text, f"project cockpit missing {token}")
        require("进入文献中心" in project_text, "project literature entry missing")
        for token in ("console-breadcrumbs", "console-nav-item is-active"):
            require(token in project_text, f"project console navigation missing {token}")
        records_base = f"/project/{record['id']}/records"
        code, body, _ = request(connection, "GET", records_base)
        records_text = body.decode("utf-8")
        for token in (
            "\u79d1\u7814\u8bb0\u5f55",
            "\u8bb0\u5f55\u521a\u624d\u7684\u8ba8\u8bba",
            "\u9636\u6bb5\u6027\u7406\u89e3\uff1a\u5b9e\u9a8c\u8bbe\u8ba1",
            "\u660e\u786e\u89e6\u53d1\uff0c\u4e0d\u81ea\u52a8\u6293\u53d6",
            "records-timeline",
            "timeline-marker",
            "2026\u5e7408\u670804\u65e5",
            "2 \u6761\u8bb0\u5f55",
        ):
            require(
                code == 200 and token in records_text,
                f"research records page missing {token}",
            )
        record_id = str(research_record["record"]["id"])
        record_name = record_id.split("/", 1)[-1]
        encoded_record = quote(record_name, safe="/")
        require("/records/records/" not in records_text, "record links duplicated the records prefix")
        code, body, _ = request(connection, "GET", "/static/style.css")
        style_text = body.decode("utf-8")
        require(
            code == 200
            and ".timeline-items::before" in style_text
            and ".timeline-marker" in style_text,
            "research records timeline connector CSS missing",
        )
        code, body, _ = request(connection, "GET", f"{records_base}/{encoded_record}")
        record_text = body.decode("utf-8")
        for token in ("\u8bb0\u5f55\u65f6\u95f4", "\u9636\u6bb5\u6027\u7406\u89e3", "\u8c03\u6574\u91c7\u6837\u65b9\u6848", "\u5173\u8054\u6750\u6599"):
            require(
                code == 200 and token in record_text,
                f"research record view missing {token}",
            )
        search_query = quote(chr(0x91C7) + chr(0x6837))
        code, body, _ = request(connection, "GET", f"{records_base}?q={search_query}")
        require(
            code == 200 and "\u9636\u6bb5\u6027\u7406\u89e3\uff1a\u5b9e\u9a8c\u8bbe\u8ba1" in body.decode("utf-8"),
            "research record search page failed",
        )
        code, _, _ = request(
            connection, "GET", f"{records_base}/..%2F..%2Fsecret.md"
        )
        require(code in {400, 404}, "research record path traversal was not rejected")
        literature_base = f"/project/{record['id']}/literature"
        code, body, _ = request(connection, "GET", literature_base)
        library_text = body.decode("utf-8")
        for token in (
            "文献中心",
            "论文原文",
            "LLM 辅助阅读",
            "demo-paper.pdf",
            "literature-sidebar",
            "全部文献",
            "已精读",
            "待精读",
            "文献类型",
            "卡片",
            "列表",
            "从推荐到网页对照阅读",
            "data-literature-kind",
            "console-project-switcher",
            "console-global-search",
            "data-literature-status",
        ):
            require(code == 200 and token in library_text, f"literature library missing {token}")
        require(
            '<main class="literature-content">' not in library_text
            and '<section class="literature-content">' in library_text,
            "literature workspace contains nested main landmark",
        )
        paper_path = "references/demo-paper.pdf"
        encoded_paper = quote(paper_path, safe="/")
        code, body, _ = request(
            connection, "GET", f"{literature_base}/read/{encoded_paper}"
        )
        read_text = body.decode("utf-8")
        require(
            code == 200 and "paper-frame" in read_text and "原文 + LLM 辅助阅读" in read_text,
            "paper reading page failed",
        )
        code, body, _ = request(
            connection, "GET", f"{literature_base}/compare/{encoded_paper}"
        )
        compare_text = body.decode("utf-8")
        for token in ("论文原文", "LLM 辅助阅读", "这是用于文献中心测试的中文辅助阅读"):
            require(code == 200 and token in compare_text, f"comparison page missing {token}")
        code, body, headers = request(
            connection, "GET", f"{literature_base}/source/{encoded_paper}"
        )
        require(
            code == 200
            and body == demo_pdf
            and headers.get("Content-Type") == "application/pdf"
            and headers.get("X-Frame-Options") == "SAMEORIGIN",
            "PDF source endpoint failed",
        )
        code, body, headers = request(
            connection,
            "GET",
            f"{literature_base}/source/{encoded_paper}",
            headers={"Range": "bytes=0-7"},
        )
        require(
            code == 206
            and body == demo_pdf[:8]
            and headers.get("Content-Range") == f"bytes 0-7/{len(demo_pdf)}",
            "PDF range response failed",
        )
        code, _, _ = request(
            connection,
            "GET",
            f"{literature_base}/source/..%2F..%2Fsecret.pdf",
        )
        require(code in {400, 404}, "literature path traversal was not rejected")
        code, body, _ = request(
            connection, "GET", f"/project/{record['id']}/page/demo.md"
        )
        text = body.decode("utf-8")
        require(
            code == 200
            and "<table>" in text
            and "wikilink" in text
            and "返回项目研究台" in text
            and "本页目录" in text
            and "打印 / 导出 PDF" in text,
            "Markdown reading page failed",
        )
        code, body, _ = request(connection, "GET", "/search")
        require(
            code == 200 and "检索论文、方法、实验和结论" in body.decode("utf-8"),
            "Chinese research search page failed",
        )
        code, body, _ = request(connection, "GET", "/settings")
        settings_text = body.decode("utf-8")
        require(
            code == 200
            and "人类可读 Wiki 目录" in settings_text
            and "机器状态目录" in settings_text,
            "Chinese storage settings page failed",
        )
        code, _, _ = request(
            connection, "GET", f"/project/{record['id']}/page/..%2F..%2Fsecret.md"
        )
        require(code in {400, 404}, "path traversal was not rejected")
        new_wiki = root / "web-moved"
        form = urlencode(
            {
                "wiki_root": str(new_wiki),
                "state_root": record["state_root"],
                "copy_existing": "1",
            }
        )
        code, _, headers = request(
            connection, "POST", f"/project/{record['id']}/storage", form
        )
        require(code == 303 and "Location" in headers, "storage form failed")
        require((new_wiki / "demo.md").is_file(), "web storage move did not copy Wiki")
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_mcp(
    root: Path, home: Path, source: Path, record: dict, research_record: dict
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", str(SCRIPTS / "mcp_server.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, "LLMWIKI_HOME": str(home)},
    )
    assert process.stdin and process.stdout
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "llmwiki_project_list", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "llmwiki_record_list",
                "arguments": {
                    "project_root": str(source),
                    "state_root": record["state_root"],
                    "query": "\u91c7\u6837",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "llmwiki_record_read",
                "arguments": {
                    "project_root": str(source),
                    "state_root": record["state_root"],
                    "record_id": research_record["record"]["id"],
                },
            },
        },
    ]
    for message in messages:
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
    process.stdin.close()
    responses = [json.loads(process.stdout.readline()) for _ in range(5)]
    process.wait(timeout=10)
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    require(
        responses[0]["result"]["serverInfo"]["version"] == str(manifest.get("version", "")),
        "initialize failed",
    )
    names = {x["name"] for x in responses[1]["result"]["tools"]}
    require(
        len(names) == 21
        and "llmwiki_web_start" in names
        and "llmwiki_search" in names
        and {"llmwiki_record_write", "llmwiki_record_list", "llmwiki_record_read"}.issubset(names),
        "tool catalog failed",
    )
    require(responses[2]["result"]["isError"] is False, "registry MCP call failed")
    record_list_payload = json.loads(responses[3]["result"]["content"][0]["text"])
    require(
        responses[3]["result"]["isError"] is False
        and record_list_payload["count"] == 1,
        "record list MCP call failed",
    )
    record_read_payload = json.loads(responses[4]["result"]["content"][0]["text"])
    require(
        responses[4]["result"]["isError"] is False
        and "\u9636\u6bb5\u6027\u7406\u89e3" in record_read_payload["record"]["content"],
        "record read MCP call failed",
    )


def test_hook(home: Path, source: Path, record: dict) -> None:
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "cwd": str(source),
        "tool_input": {"file_path": "src/main.py"},
        "tool_response": {"ok": True},
    }
    completed = subprocess.run(
        [sys.executable, "-I", "-B", str(SCRIPTS / "record_change.py")],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=10,
        env={**os.environ, "LLMWIKI_HOME": str(home)},
        check=False,
    )
    require(completed.returncode == 0, "hook should be fail-open")
    require(
        "src/main.py"
        in (Path(record["state_root"]) / "events.jsonl").read_text(encoding="utf-8"),
        "registered hook event missing",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="llmwiki-smoke-") as temp:
        root = Path(temp)
        test_core(root)
        home, source, record = test_registry(root)
        test_renderer()
        research_record = test_research_records(source, record)
        test_web(root, home, source, record, research_record)
        record = get_project(current_path=str(source), home=str(home))["project"]
        test_mcp(root, home, source, record, research_record)
        test_hook(home, source, record)
    print("LLM Wiki smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
