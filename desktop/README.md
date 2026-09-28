# 野人工作台 · Windows 桌面版

窗口标题为 **野人工作台**；pywebview 使用 Windows WebView2 显示原有工作台网页。`app.py` 在同一进程中复用 `web_server.create_handler`，通过独占端口的桌面 HTTP 服务监听本机回环地址，随窗口关闭。程序有单实例控制，重复双击唤回原窗口；普通关闭无提示，未保存的编辑或上传会先等待自动保存，仍有未保存内容时询问是否关闭。

## 数据与安装位置

- 默认数据目录为 `%LOCALAPPDATA%\LLMWiki`，与原网站共享原有数据及存储配置；不会复制出另一套研究数据。原网站使用自定义位置时，桌面版也应选择同一数据根目录。
- 程序安装到 `%LOCALAPPDATA%\Programs\WildResearchWorkbench`，与数据目录分离；安装和更新不删除用户数据。
- 当前用户桌面和开始菜单各创建 **野人工作台（桌面版）** 快捷方式，直接指向 EXE；原网站快捷方式保留。同名但指向其他程序的 `.lnk` 会导致安装停止，不会覆盖。
- 不需要管理员权限，不改系统 Python，不设置开机自启，不自动启动应用。

## 构建环境

目前只支持 **Windows x64 / Python 3.11**；请在 Windows 上构建，不能跨平台生成此 EXE。运行端需要 WebView2 Runtime，打包不附带完整浏览器；本机已安装。缺失时从 [Microsoft 官方页面](https://developer.microsoft.com/microsoft-edge/webview2/) 安装，构建和安装脚本不会自动安装系统组件。

构建前需有已安装且带 pip 的独立 Python 3.11，不依赖 uv CLI。脚本用 `-Python` 指定的解释器（默认 `python`）执行 `-m venv`，创建或复用唯一的隔离 venv；不会下载或全局安装 Python：

```text
%LOCALAPPDATA%\LLMWikiDesktop\build-env
本机：C:\Users\lyn\AppData\Local\LLMWikiDesktop\build-env
```

若该环境正在创建、不是 Python 3.11 x64 venv，或开放了 system-site-packages，脚本会停止而不是删除、重建或污染它。现成环境直接复用其中的 `Scripts/python.exe`，无需 PATH 上有 Python。依赖只声明在 `desktop/requirements-build.txt`，用隔离环境的 `python -m pip install` 安装并通过 `pip check` 检查，插件仍不引入桌面依赖。

直接依赖已固定为 [pywebview 6.2.1](https://pypi.org/project/pywebview/6.2.1/)、[PyInstaller 6.22.3](https://pypi.org/project/pyinstaller/6.22.3/)、[pyinstaller-hooks-contrib 2026.7](https://pypi.org/project/pyinstaller-hooks-contrib/2026.7/) 和 [pypdf 6.19.0](https://pypi.org/project/pypdf/6.19.0/)，于 2026-09-25 查询官方 PyPI 确认；传递依赖仍由 pip 解析。pypdf 用于原有 PDF 文本阅读功能。首次安装依赖需要联网。

## 构建与安装

在 PowerShell 中执行；只对这次脚本进程使用 Bypass，不修改用户/系统执行策略：

```powershell
Set-Location 'E:\GitHub\llm-wiki-agent\next\llmwiki-lite-codex-plugin'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\desktop\build.ps1
if ($LASTEXITCODE -ne 0) { throw '构建失败，请勿继续安装旧产物。' }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\desktop\install.ps1
if ($LASTEXITCODE -ne 0) { throw '安装失败，请查看上方错误。' }
```

`build.ps1` 只构建，不安装或启动。它通过明确的 venv Python 路径运行 PyInstaller，输出为：

首次创建环境时，若默认 `python` 不是 Python 3.11，可在构建命令末尾添加 `-Python 'C:\Users\lyn\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe'`；已有构建环境时无需此参数。

```text
desktop/dist/WildResearchWorkbench/
  WildResearchWorkbench.exe
  _internal/
    plugin/scripts/                  # 全部 Python 模块和 static 等资源
    plugin/.codex-plugin/plugin.json
    plugin/templates/                # 包括周报模板
    plugin/skills/llmwiki-research-record/
    ...                             # Python、WebView2 桥接 DLL、JS 等依赖
```

采用 **onedir + `console=False`** 的 Windows GUI EXE，不是 onefile，不需要每次启动解压整包。必须保留整个目录，不能只复制 EXE。构建中间文件和 PyInstaller 缓存位于 `desktop/build/`，输出在 `desktop/dist/`；这两个目录属于生成物，不应提交版本控制。

`Workbench.spec` 同时收集全部脚本源码和动态导入的 hidden imports，以便分析其标准库依赖；`app.py` 将 `sys._MEIPASS/plugin/scripts` 放到 `sys.path` 首位，保证 `__file__` 相对路径资源可用。pywebview 的 Windows 后端、桥接 JS/DLL 和 Python.NET 也纳入打包，不引入 Qt/CEF。

`install.ps1` 会检查目录完整性，先复制到同级临时目录，再替换程序目录；替换失败时尝试恢复旧目录。所有递归删除和目录移动都限制在规范化后的程序目录及 GUID 命名临时目录中，并拒绝符号链接/junction。运行中的桌面应用会阻止安装，不会被强制终止。可用 `-WhatIf` 预览，或用 `-SourceDirectory 'D:\release\WildResearchWorkbench'` 安装其他完整构建目录。

应用支持 `--home` 指定已有数据根目录、`--port` 指定本机端口（`0` 为自动分配）；默认独立端口为 `18765`，占用时自动选择可用端口。需要单独测试时，例如：

```powershell
& "$env:LOCALAPPDATA\Programs\WildResearchWorkbench\WildResearchWorkbench.exe" --home 'D:\LLMWiki-Test' --port 0
```

`--debug-port` 仅用于开发测试的本机 WebView2 CDP 调试，日常使用不要开启；安装的快捷方式不传入调试参数。无终端模式下可查看数据目录下的 `logs/desktop.log` 排错。

## 更新与验证

- 修改 `app.py`、插件 Python、网页/static 资源或构建依赖后，必须**重新构建，再安装**。已安装的 EXE 使用打包快照，不会自动读取仓库修改。
- 更新前关闭桌面版；安装仅替换程序及专用快捷方式，不触碰 `%LOCALAPPDATA%\LLMWiki`，不会迁移/删除原 Wiki 文件。
- 首次构建后检查：双击快捷方式无终端、窗口标题正确、页面及图标可用、能读取原项目/数据；关闭窗口后对应本机服务和应用进程退出。原网站独立启动的服务不属于桌面版的关闭范围。
- 运行端无需 Python/uv，但 Git 相关功能仍需要本机 Git；WebView2/.NET 运行环境问题应按实际报错处理。尚未完成真实构建/启动验证时，不应将静态检查当作发行验证。

桌面专项验证：`python -B -m unittest discover -s desktop -p test_app.py`。打包后运行 `python -B desktop/run_native_acceptance.py`，需要 Node.js 与 Playwright（可通过 `NODE_PATH` 或 `LLMWIKI_PLAYWRIGHT` 指定已有模块）。它使用临时数据目录，在实际 EXE 的 WebView2 中检查八个主要页面、任务创建、原生 Ctrl+V 截图、Markdown、深色、重复启动、关闭保存和重启持久化；截图和 JSON 结果在 `desktop/test-output/packaged`。不会把验收项目注册到真实工作台。

实现依据：[pywebview API](https://pywebview.flowrl.com/api/) 与 [Windows 打包说明](https://pywebview.flowrl.com/guide/freezing)。
