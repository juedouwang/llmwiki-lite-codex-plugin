"""Windows desktop host for the existing research workbench.

The optional desktop environment is independent of the installable AI plugin.
One process owns both the WebView2 window and its loopback HTTP server.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
from http.server import ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import socket
import threading
import time

TITLE = "野人工作台"
APP_ID = "LLMWiki.WildResearchWorkbench.Desktop"
DEFAULT_PORT = 18765


def plugin_scripts() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "plugin" / "scripts"
    return Path(__file__).resolve().parents[1] / "plugins" / "llmwiki-lite" / "scripts"


def read_settings(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_settings(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def configure_logging(home: Path) -> Path:
    logfile = home / "logs" / "desktop.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logfile, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    return logfile


def find_window(pid: int) -> int | None:
    """Only select a visible top-level window belonging to the recorded process."""
    user = ctypes.windll.user32
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(handle, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(handle):
            windows.append(handle)
        return True

    user.EnumWindows(visit, 0)
    return windows[0] if windows else None


class SingleInstance:
    def __init__(self, home: Path):
        identity = hashlib.sha256(str(home.resolve()).casefold().encode()).hexdigest()[:24]
        self.name = "Local\\LLMWikiDesktop-" + identity
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateMutexW(None, False, self.name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.is_first = ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS

    def focus_existing(self, state_file: Path) -> None:
        for _ in range(40):
            state = read_settings(state_file)
            pid = state.get("pid")
            handle = find_window(pid) if isinstance(pid, int) else None
            if handle:
                user = ctypes.windll.user32
                hwnd = wintypes.HWND(handle)
                if user.IsIconic(hwnd):
                    user.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
                user.SetForegroundWindow(hwnd)
                return
            time.sleep(0.1)

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class DesktopHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        # Windows SO_REUSEADDR permits two HTTP listeners on the same port.
        # An exclusive socket makes the fallback deterministic and isolates data.
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class DesktopServer:
    def __init__(self, home: Path, port: int | None = None):
        self.home = home
        self.settings_file = home / "desktop" / "window.json"
        self.instance_file = home / "desktop" / "instance.json"
        self.settings = read_settings(self.settings_file)
        saved_port = self.settings.get("port", DEFAULT_PORT)
        self.port = port if port is not None else saved_port
        if not isinstance(self.port, int) or not 0 <= self.port <= 65535:
            self.port = DEFAULT_PORT
        self.server = None
        self.thread = None

    def start(self) -> str:
        from web_server import create_handler

        handler = create_handler(str(self.home))

        try:
            self.server = DesktopHTTPServer(("127.0.0.1", self.port), handler)
        except OSError as exc:
            if exc.errno not in {13, 98, 10013, 10048}:
                raise
            # Never attach to a different service, stale build, or data directory.
            self.server = DesktopHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_port
        self.url = f"http://127.0.0.1:{self.port}/"
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
        )
        self.thread.start()
        self.settings["port"] = self.port
        write_settings(self.settings_file, self.settings)
        write_settings(self.instance_file, {"pid": os.getpid(), "url": self.url})
        return self.url

    def stop(self) -> None:
        if self.server:
            if self.thread and self.thread.is_alive():
                self.server.shutdown()
                self.thread.join(timeout=5)
            self.server.server_close()
            self.server = None
        if read_settings(self.instance_file).get("pid") == os.getpid():
            self.instance_file.unlink(missing_ok=True)


def window_size(settings: dict) -> tuple[int, int]:
    # pywebview sizes are logical pixels. Keep the first window inside the work area.
    user = ctypes.windll.user32
    area = wintypes.RECT()
    user.SystemParametersInfoW(48, 0, ctypes.byref(area), 0)  # SPI_GETWORKAREA
    dpi = user.GetDpiForSystem() if hasattr(user, "GetDpiForSystem") else 96
    scale = max(1, dpi / 96)
    available = (int((area.right - area.left) / scale), int((area.bottom - area.top) / scale))
    sizes = []
    for key, default, minimum, limit in zip(("width", "height"), (1440, 900), (800, 600), available):
        requested = settings.get(key, default)
        if not isinstance(requested, (int, float)):
            requested = default
        sizes.append(max(minimum, min(int(requested), max(minimum, limit - 48))))
    return tuple(sizes)


class Appearance:
    """The sole JS bridge capability: match the native title bar to the page theme."""
    def theme(self, dark: bool) -> None:
        if not isinstance(dark, bool):
            return
        handle = find_window(os.getpid())
        if handle:
            enabled = ctypes.c_int(int(dark))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(handle), 20, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )


SHELL_SCRIPT = """(() => {
  if (window.__workbenchDesktop) return;
  window.__workbenchDesktop = true;
  const apply = () => window.pywebview.api.theme(
    document.documentElement.dataset.resolvedTheme === 'dark');
  apply();
  new MutationObserver(apply).observe(document.documentElement,
    {attributes:true, attributeFilter:['data-resolved-theme']});
  document.addEventListener('click', event => {
    const a = event.target.closest && event.target.closest('a[href]');
    if (a && /^https?:$/.test(a.protocol) && a.origin !== location.origin) {
      a.target = '_blank'; a.rel = 'noopener noreferrer';
    }
  }, true);
})()"""

UNSAVED_SCRIPT = """(() => {
  const event = new Event('beforeunload', {cancelable:true});
  window.dispatchEvent(event); return event.defaultPrevented;
})()"""


class CloseGuard:
    """Run the existing page's unsaved-content check outside the native UI thread."""
    def __init__(self, window, runtime: DesktopServer):
        self.window = window
        self.runtime = runtime
        self.allowed = False
        self.checking = False

    def closing(self) -> bool:
        if self.allowed:
            return True
        if not self.checking:
            self.checking = True
            threading.Thread(target=self._check, daemon=True).start()
        return False

    def _check(self) -> None:
        try:
            if self.window.events.loaded.is_set():
                dirty = self.window.evaluate_js(UNSAVED_SCRIPT)
                if dirty:
                    # Give the shared editor's 600ms autosave a chance to finish.
                    time.sleep(0.8)
                    dirty = self.window.evaluate_js(UNSAVED_SCRIPT)
                if dirty and not self.window.create_confirmation_dialog(
                    "内容尚未保存", "请等待保存或图片上传完成。现在仍要关闭工作台吗？"
                ):
                    return
            self.runtime.settings.update(width=self.window.width, height=self.window.height)
            write_settings(self.runtime.settings_file, self.runtime.settings)
            self.allowed = True
            self.window.destroy()
        except Exception:
            logging.exception("Window close failed")
            # A failed WebView must remain closable. Only UI failures take this path.
            self.allowed = True
            self.window.destroy()
        finally:
            self.checking = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="野人工作台 Windows 桌面版")
    parser.add_argument("--home", help="共享工作台数据目录；默认沿用插件设置")
    parser.add_argument("--port", type=int, help="本机桌面服务端口；默认独立端口 18765")
    parser.add_argument("--debug-port", type=int, help="仅开发测试：启用本机 WebView2 调试")
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("当前桌面发行版支持 Windows；其他平台请继续使用网页端。")
    for port in (args.port, args.debug_port):
        if port is not None and not 0 <= port <= 65535:
            parser.error("端口必须在 0–65535 之间。")
    scripts = plugin_scripts()
    sys.path.insert(0, str(scripts))
    from llmwiki_registry import llmwiki_home

    home = llmwiki_home(args.home)
    runtime = None
    instance = None
    logfile = home / "logs" / "desktop.log"
    try:
        instance = SingleInstance(home)
        if not instance.is_first:
            instance.focus_existing(home / "desktop" / "instance.json")
            return 0
        configure_logging(home)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            pass
        import webview

        runtime = DesktopServer(home, args.port)
        url = runtime.start()
        width, height = window_size(runtime.settings)
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
        if args.debug_port:
            webview.settings["REMOTE_DEBUGGING_PORT"] = args.debug_port
        window = webview.create_window(
            TITLE, url, width=width, height=height, min_size=(800, 600),
            background_color="#f8f8f7", text_select=True, js_api=Appearance(),
            localization={"global.quitConfirmation": "确认关闭野人工作台？"},
        )
        guard = CloseGuard(window, runtime)
        window.events.closing += guard.closing
        window.events.loaded += lambda: window.evaluate_js(SHELL_SCRIPT)
        logging.info("Desktop started at %s", url)
        webview.start(
            gui="edgechromium", debug=bool(args.debug_port), private_mode=False,
            storage_path=str(home / "desktop" / "webview"),
            icon=str(scripts / "static" / "workbench.ico"),
        )
        return 0
    except Exception:
        logging.exception("Desktop startup failed")
        ctypes.windll.user32.MessageBoxW(
            None, f"工作台未能启动。\n请查看：{logfile}\n桌面版需要 Microsoft Edge WebView2 Runtime。",
            TITLE, 0x10,
        )
        return 1
    finally:
        if runtime:
            runtime.stop()
        if instance:
            instance.close()


if __name__ == "__main__":
    raise SystemExit(main())
