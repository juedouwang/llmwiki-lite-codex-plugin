#!/usr/bin/env python3
"""Install or remove LLM Wiki Lite entries in an opencode configuration file."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

MCP_SERVER_NAME = "llmwiki"
HOOK_FILE = "llmwiki-hook.js"
CONFIG_FILE = "opencode.json"


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config_path(scope: str, project_dir: Path | None) -> Path:
    if scope == "global":
        return Path.home() / ".config" / "opencode" / CONFIG_FILE
    base = project_dir if project_dir is not None else Path.cwd()
    return base.resolve() / CONFIG_FILE


def hook_url(root: Path) -> str:
    return (root / "opencode" / HOOK_FILE).as_uri()


def mcp_command(root: Path, python: str) -> list[str]:
    return [python, "-I", "-B", str(root / "scripts" / "mcp_server.py")]


def is_our_skills_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = Path(value.replace("\\", "/")).parts
    return len(parts) >= 2 and parts[-2:] == ("llmwiki-lite", "skills")


def is_our_mcp_entry(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    command = value.get("command")
    if not isinstance(command, list):
        return False
    return any(
        isinstance(item, str) and Path(item.replace("\\", "/")).name == "mcp_server.py"
        for item in command
    )


def is_our_plugin_entry(value: Any) -> bool:
    if isinstance(value, list) and value:
        value = value[0]
    if not isinstance(value, str):
        return False
    return value.replace("\\", "/").rstrip("/").endswith("/" + HOOK_FILE)


def install_into(config: dict[str, Any], root: Path, python: str = "python") -> dict[str, Any]:
    skills = config.get("skills")
    if not isinstance(skills, dict):
        skills = {}
    paths = [p for p in skills.get("paths", []) if not is_our_skills_path(p)]
    paths.append((root / "skills").as_posix())
    skills["paths"] = paths
    config["skills"] = skills

    mcp = config.get("mcp")
    if not isinstance(mcp, dict):
        mcp = {}
    current = mcp.get(MCP_SERVER_NAME)
    if current is not None and not is_our_mcp_entry(current):
        raise SystemExit(
            f"opencode 配置中已存在名为 {MCP_SERVER_NAME} 的 MCP 服务，"
            "且不是本插件写入的；请先重命名或删除它，再重新运行安装。"
        )
    mcp[MCP_SERVER_NAME] = {
        "type": "local",
        "command": mcp_command(root, python),
        "enabled": True,
    }
    config["mcp"] = mcp

    plugins = [p for p in config.get("plugin", []) if not is_our_plugin_entry(p)]
    plugins.append([hook_url(root), {"python": python}])
    config["plugin"] = plugins
    return config


def uninstall_from(config: dict[str, Any], root: Path) -> dict[str, Any]:
    skills = config.get("skills")
    if isinstance(skills, dict):
        kept = [p for p in skills.get("paths", []) if not is_our_skills_path(p)]
        if kept:
            skills["paths"] = kept
        else:
            skills.pop("paths", None)
        if not skills:
            config.pop("skills", None)

    mcp = config.get("mcp")
    if isinstance(mcp, dict) and is_our_mcp_entry(mcp.get(MCP_SERVER_NAME)):
        mcp.pop(MCP_SERVER_NAME, None)
        if not mcp:
            config.pop("mcp", None)

    if "plugin" in config:
        kept = [p for p in config.get("plugin", []) if not is_our_plugin_entry(p)]
        if kept:
            config["plugin"] = kept
        else:
            config.pop("plugin", None)
    return config


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"$schema": "https://opencode.ai/config.json"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise SystemExit(
            f"无法解析 {path}：{error}\n"
            "请先把该文件改为标准 JSON（去掉注释和尾逗号），或使用 --config 指定另一个配置文件。"
        )
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} 必须包含一个 JSON 对象。")
    return payload


def save_config(path: Path, config: dict[str, Any]) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.is_file():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        shutil.copy2(path, backup)
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return backup


def check_plugin_layout(root: Path) -> None:
    for relative in ("skills", "scripts/mcp_server.py", f"opencode/{HOOK_FILE}"):
        if not (root / relative).exists():
            raise SystemExit(f"插件目录不完整：缺少 {root / relative}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 LLM Wiki Lite 注册到 opencode 配置（skills、MCP、变更 Hook）。"
    )
    parser.add_argument(
        "--scope",
        choices=("global", "project"),
        default="global",
        help="global 写入 ~/.config/opencode/opencode.json；project 写入项目目录下的 opencode.json",
    )
    parser.add_argument("--project-dir", help="project 模式下使用的项目目录，默认当前目录")
    parser.add_argument("--config", help="直接指定 opencode.json 路径（优先于 --scope）")
    parser.add_argument("--python", default="python", help="启动 MCP 使用的 Python 命令")
    parser.add_argument("--uninstall", action="store_true", help="移除本插件写入的配置")
    parser.add_argument("--dry-run", action="store_true", help="只打印结果，不写入文件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = plugin_root()
    check_plugin_layout(root)
    if args.config:
        config_path = Path(args.config).expanduser().resolve()
    else:
        config_path = default_config_path(args.scope, Path(args.project_dir) if args.project_dir else None)

    config = load_config(config_path)
    if args.uninstall:
        config = uninstall_from(config, root)
        action = "移除"
    else:
        config = install_into(config, root, args.python)
        action = "写入"

    rendered = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    if args.dry_run:
        print(rendered, end="")
        print(f"# dry-run：未修改 {config_path}", file=sys.stderr)
        return 0

    backup = save_config(config_path, config)
    print(f"已{action} opencode 配置：{config_path}")
    if backup is not None:
        print(f"原文件备份：{backup}")
    if args.uninstall:
        print("已移除 llmwiki 的 skills 路径、MCP 服务和变更 Hook；其他配置保持不变。")
    else:
        print(f"- skills.paths：{root / 'skills'}")
        print(f"- mcp.llmwiki：{' '.join(mcp_command(root, args.python))}")
        print(f"- plugin：{hook_url(root)}")
        print("请退出并重新启动 opencode，使配置生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
