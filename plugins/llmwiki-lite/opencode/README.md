# opencode 集成

opencode 没有插件市场，通过配置接入。安装脚本会把三项配置合并进你的 `opencode.json`，不会覆盖其他设置：

- `skills.paths`：七个 llmwiki Skill 所在目录；
- `mcp.llmwiki`：确定性文件与注册表工具（stdio 本地 MCP）；
- `plugin`：可选的变更 Hook（`llmwiki-hook.js`），只记录文件改动提示，失败不影响工具调用。

## 安装

在本仓库根目录运行：

```powershell
python plugins/llmwiki-lite/opencode/install.py
```

默认写入全局配置 `~/.config/opencode/opencode.json`。其他用法：

```powershell
python plugins/llmwiki-lite/opencode/install.py --scope project --project-dir D:\my-project
python plugins/llmwiki-lite/opencode/install.py --config D:\my-project\opencode.json
python plugins/llmwiki-lite/opencode/install.py --dry-run
python plugins/llmwiki-lite/opencode/install.py --uninstall
```

说明：

- 写入前会备份原配置为 `opencode.json.bak-时间戳`；
- 如果配置里已有非本插件写入的 `mcp.llmwiki`，安装会终止并提示，不会覆盖；
- `--python` 指定 MCP 与 Hook 使用的 Python 命令，默认 `python`；
- 安装后请退出并重新启动 opencode；配置在启动时加载，不会热更新。

## 手动配置

不想运行脚本时，把下面内容合并到 `opencode.json`（把 `<repo>` 换成仓库绝对路径，Windows 路径使用正斜杠或 `file:///` URL）：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "skills": {"paths": ["<repo>/plugins/llmwiki-lite/skills"]},
  "mcp": {
    "llmwiki": {
      "type": "local",
      "command": ["python", "-I", "-B", "<repo>/plugins/llmwiki-lite/scripts/mcp_server.py"],
      "enabled": true
    }
  },
  "plugin": [["file:///<repo>/plugins/llmwiki-lite/opencode/llmwiki-hook.js", {"python": "python"}]]
}
```

`plugin` 条目中的 `{"python": ...}` 是 Hook 调用 Python 的命令；也可写成字符串形式 `"file:///..."`，此时 Hook 使用环境变量 `LLMWIKI_PYTHON` 或默认 `python`。

Skill 名称与其他平台一致：`llmwiki-projects`、`llmwiki-understand`、`llmwiki-query`、`llmwiki-maintain`、`llmwiki-literature`、`llmwiki-research-record`、`llmwiki-web`。

## 卸载

```powershell
python plugins/llmwiki-lite/opencode/install.py --uninstall
```

只移除本插件写入的条目；如仓库位置已移动，仍按文件名匹配移除。

## 说明

- Hook 只对 `write`、`edit`、`patch` 等写入类工具触发，写入 `<state_root>/events.jsonl`，供 `llmwiki-maintain` 做增量维护提示；
- Hook 始终 fail-open：Python 不存在、脚本缺失或输入异常都不会影响 opencode 的工具调用；
- 网站仍只监听 loopback，不产生额外网络请求；
- 若同时安装了 Claude Code 插件并把它复制到 `~/.claude/skills/` 或 `~/.agents/skills/`，opencode 会自动扫描到同名 Skill，请避免重复配置。
