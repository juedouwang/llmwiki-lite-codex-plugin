"""Opt-in report browser tests using an existing Playwright installation."""
import base64
from datetime import date, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import struct
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from llmwiki_registry import register_project  # noqa: E402
from web_server import create_server  # noqa: E402
import research_reports as reports  # noqa: E402



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



def main():
    with tempfile.TemporaryDirectory(prefix='llmwiki-reports-browser-') as tmp:
        root = Path(tmp)
        home = str(root / 'home')
        source = root / 'source'
        source.mkdir()
        project = register_project(str(source), name='报告交互验收', home=home)['project']
        other_source = root / 'other-source'
        other_source.mkdir()
        other = register_project(str(other_source), name='第二项目', home=home)['project']
        owner = reports.workspace(home)
        for report_owner, ids in [(owner, [project['id'], other['id']]), (project, [project['id']])]:
            week = reports.create(report_owner, 'weekly', '2026-09-14', ids, home=home)
            text = '两个项目的本周工作' if reports.is_workspace(report_owner) else '历史项目周报，不移动'
            week = reports.update(report_owner, 'weekly', '2026-09-14', {'action': 'save', 'expected_revision': week['revision'], 'body': text})
            reports.update(report_owner, 'weekly', '2026-09-14', {'action': 'confirm', 'expected_revision': week['revision']})
        reports.create(owner, 'daily', '2026-09-19', [project['id']], home=home)
        formal = reports.create(owner, 'daily', '2026-09-18', [project['id']], home=home)
        formal = reports.update(owner, 'daily', '2026-09-18', {'action': 'save', 'expected_revision': formal['revision'], 'body': '正式版：实测尚未完成。'})
        reports.update(owner, 'daily', '2026-09-18', {'action': 'confirm', 'expected_revision': formal['revision']})
        reports.publish(owner, 'daily', '2026-09-18', '候选：准备下一轮验证。', generation_id='fixture', sources=[], fingerprint='fixture', project_ids=[project['id']], coverage_until='2026-09-18T12:00:00Z', gaps=['模拟来源；不是实际科研成果。'])
        for i in range(31):
            day = (date(2026, 8, 1) + timedelta(days=i)).isoformat()
            reports.create(owner, 'daily', day, [project['id'], other['id']], home=home)
        server = create_server(home, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cfg = {'origin': f'http://127.0.0.1:{server.server_port}', 'pid': project['id'], 'other_pid': other['id'], 'png': base64.b64encode(_png_bytes()).decode(),
               'evidence': str(Path(os.environ.get('TEMP', tmp)) / 'llmwiki-reports-evidence')}
        try:
            result = subprocess.run(['node', str(Path(__file__).with_name('reports_browser_test.cjs')), json.dumps(cfg)], timeout=180)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        return result.returncode


if __name__ == '__main__':
    sys.exit(main())
