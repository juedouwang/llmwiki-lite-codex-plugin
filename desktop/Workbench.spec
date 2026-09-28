# -*- mode: python ; coding: utf-8 -*-
"""Windows onedir bundle. app.py puts _MEIPASS/plugin/scripts first on sys.path."""

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

if sys.platform != "win32":
    raise SystemExit("Build WildResearchWorkbench on Windows, using Python 3.11 x64.")

desktop_dir = Path(SPECPATH).resolve()
plugin_dir = desktop_dir.parent / "plugins" / "llmwiki-lite"
scripts_dir = plugin_dir / "scripts"
icon = scripts_dir / "static" / "workbench.ico"


def resource_tree(root):
    """Preserve plugin-relative paths, excluding development bytecode caches."""
    if not root.is_dir():
        raise FileNotFoundError(root)
    resources = []
    for source in sorted(root.rglob("*")):
        relative = source.relative_to(plugin_dir)
        if "__pycache__" in relative.parts or source.suffix in {".pyc", ".pyo"}:
            continue
        if source.is_file():
            # Do not package a symlink/junction target outside the plugin.
            source.resolve().relative_to(plugin_dir.resolve())
            resources.append((str(source), str(Path("plugin") / relative.parent)))
    return resources


# Source copies keep __file__-relative static/template access working. Explicit
# hidden imports make Analysis also discover dependencies of dynamic imports.
datas = resource_tree(scripts_dir)
datas += resource_tree(plugin_dir / "templates")
datas += resource_tree(plugin_dir / "skills" / "llmwiki-research-record")
datas.append((str(plugin_dir / ".codex-plugin" / "plugin.json"), "plugin/.codex-plugin"))
datas += collect_data_files("webview")  # Includes JS bridge assets as well as lib/.

plugin_modules = set()
for source in sorted(scripts_dir.rglob("*.py")):
    relative = source.relative_to(scripts_dir)
    if "__pycache__" in relative.parts:
        continue
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    if parts and all(part.isidentifier() for part in parts):
        plugin_modules.add(".".join(parts))

a = Analysis(
    [str(desktop_dir / "app.py")],
    pathex=[str(desktop_dir), str(scripts_dir)],
    binaries=collect_dynamic_libs("webview"),
    datas=datas,
    hiddenimports=sorted(plugin_modules | {
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "clr",
        "pythonnet",
        "clr_loader",
        "pypdf",
    }),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "cefpython3",
        "webview.platforms.qt", "webview.platforms.gtk",
        "webview.platforms.cocoa", "webview.platforms.android", "webview.platforms.cef",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="WildResearchWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # Windows GUI subsystem: no terminal, not a hidden shell wrapper.
    disable_windowed_traceback=False,
    contents_directory="_internal",
    icon=str(icon),
    uac_admin=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="WildResearchWorkbench",
)
