"""Open the local workbench without a terminal (launch with pythonw on Windows)."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def show_error(message: str) -> None:
    """Normal startup is silent; only an actual failure needs attention."""
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "野人工作台", 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="静默启动并打开野人工作台。")
    parser.add_argument("--home")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    try:
        from web_server import start_background
        start_background(home=args.home, port=args.port, open_browser=True)
        return 0
    except Exception:
        # Log import/startup failures as well: pythonw has no stderr window.
        root = Path(args.home or os.environ.get("LLMWIKI_HOME") or
                    Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LLMWiki")
        log = root / "logs" / "desktop-launch-error.log"
        detail = traceback.format_exc()
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(detail, encoding="utf-8")
            message = f"网页未能启动，请查看错误日志：\n{log}"
        except OSError:
            message = "网页未能启动，且无法写入错误日志。请检查安装目录和文件权限。"
        show_error(message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
