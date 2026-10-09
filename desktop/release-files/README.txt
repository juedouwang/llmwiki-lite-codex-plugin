野人工作台（Windows 桌面版）

【安装】
1. 把整个压缩包解压到任意文件夹。
2. 双击 install.cmd。程序会装到当前用户目录，并在桌面和开始菜单创建「野人工作台」快捷方式。不需要管理员权限。
3. 如果 Windows 提示「已保护你的电脑」：点「更多信息」→「仍要运行」。本程序没有付费代码签名，这是正常提示。
4. 不想安装也可以：直接双击 WildResearchWorkbench 文件夹里的 WildResearchWorkbench.exe。

【运行要求】
- Windows 10 / 11，64 位。
- Microsoft Edge WebView2 Runtime（Windows 10/11 一般已自带）。缺少时从微软官网安装：
  https://developer.microsoft.com/microsoft-edge/webview2/

【更新】
- 有新版本时，工作台顶部会提示，点「下载新版」。下载后先关闭工作台，再双击新版的 install.cmd，数据不受影响。
- 不想自动检查更新：设置环境变量 LLMWIKI_UPDATE_CHECK=0。检查每天最多一次，只向 GitHub 查询最新版本号。

【数据】
- 你的项目、科研记录和知识库都保存在你自己的电脑上，安装、更新、卸载都不会删除它们。
- 工作台数据目录：%LOCALAPPDATA%\LLMWiki

【配合 AI 助手使用】
科研记录、项目现状等内容由 Claude Code 或 Codex 里的 LLM Wiki Lite 插件自动写入。安装方法见：
https://github.com/juedouwang/llmwiki-lite-codex-plugin

【卸载】
删除 %LOCALAPPDATA%\Programs\WildResearchWorkbench 文件夹和「野人工作台」快捷方式即可。
