"""One-shot, agent-only task access; all storage stays in the shared backend."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import date
from pathlib import Path

from llmwiki_registry import list_projects


class InputError(ValueError):
    """Invalid command line or JSON envelope, rejected before mutation."""


class BackendUnavailable(RuntimeError):
    """The shared daily-task API has not been installed yet."""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InputError(message)


def _parser() -> argparse.ArgumentParser:
    parser = Parser(description="一次性读取或写入任务；写入身份固定为 agent，不验收完成。")
    parser.add_argument("--home", help="可选的 LLM Wiki 配置目录；省略时沿用现有默认值。")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("list", "读取指定本地日期的跨项目任务与 revisions"),
        ("projects", "列出已注册项目的精确 ID"),
        ("progress", "读取一个注册项目的完整任务摘要"),
        ("write", "从 JSON 文件或标准输入提交一次写入"),
    ):
        command = commands.add_parser(name, help=help_text)
        # Accept --home on either side of the subcommand without overriding it.
        command.add_argument("--home", default=argparse.SUPPRESS)
        if name == "list":
            command.add_argument("--date", type=_date, help="YYYY-MM-DD；省略时由后端取本地今天。")
        elif name == "progress":
            command.add_argument("--project-id", required=True, help="注册表中的精确项目 ID。")
        elif name == "write":
            command.add_argument("--args-file", required=True, help="UTF-8 JSON payload 文件；- 表示 stdin。")
    return parser


def _date(value: str) -> str:
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("日期必须是有效的 YYYY-MM-DD。") from None
    return value


def _backend():
    try:
        backend = importlib.import_module("daily_tasks")
    except ModuleNotFoundError as exc:
        if exc.name != "daily_tasks":
            raise
        raise BackendUnavailable("daily_tasks API 尚未就绪；未写入，请等待后端实现后重试。") from None
    if not all(callable(getattr(backend, name, None)) for name in ("list_day", "mutate")):
        raise BackendUnavailable("daily_tasks 需要提供 list_day 和 mutate；未写入。")
    return backend


def _project(home: str | None, project_id: str) -> dict:
    for project in list_projects(home=home)["projects"]:
        if project["id"] == project_id:
            return project
    raise InputError("project_id 必须是注册表中的精确 ID；临时任务使用 __workspace__。")


def _object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("JSON 对象不能包含重复字段。")
        result[key] = value
    return result


def _constant(value: str):
    raise InputError("JSON 不能包含 NaN 或 Infinity。")


def _payload(filename: str) -> dict:
    try:
        text = sys.stdin.read() if filename == "-" else Path(filename).read_text(encoding="utf-8-sig")
        payload = json.loads(text.lstrip("\ufeff"), object_pairs_hook=_object, parse_constant=_constant)
    except json.JSONDecodeError as exc:
        raise InputError(f"无效 JSON（第 {exc.lineno} 行，第 {exc.colno} 列）。") from None
    except UnicodeError:
        raise InputError("JSON 输入必须使用 UTF-8 编码。") from None
    if not isinstance(payload, dict):
        raise InputError("输入必须是 payload JSON 对象，不是 MCP 的 {home, payload} 包装。")
    if "actor" in payload:
        raise InputError("不能指定 actor；CLI 的写入身份固定为 agent。")
    action = payload.get("action")
    if not isinstance(action, str) or action not in {"create", "update", "delete", "plan", "submit", "restore"}:
        raise InputError("action 仅支持 create/update/delete/plan/submit/restore；不支持 accept/complete。")
    if not isinstance(payload.get("project_id"), str) or not payload["project_id"].strip():
        raise InputError("project_id 必须是注册项目精确 ID 或 __workspace__。")
    if not isinstance(payload.get("revision"), str):
        raise InputError("revision 必须原样取自 list 返回的 revisions[project_id]，包括初始空字符串。")
    fields = [payload, payload.get("task")]
    if isinstance(payload.get("subtasks"), list):
        fields.extend(payload["subtasks"])
    if any(isinstance(task, dict) and task.get("status") == "done" for task in fields):
        raise InputError("agent 不得设置 done；请使用 submit 提交交付摘要，等待用户验收。")
    # Field/date/relationship validation and atomicity belong to daily_tasks.
    return payload


def _run(args: argparse.Namespace) -> dict:
    if args.command == "projects":
        return list_projects(home=args.home)
    if args.command == "progress":
        project = _project(args.home, args.project_id)
        from research_progress import load_summary
        return {**load_summary(project), "project_id": project["id"], "project_name": project["name"]}
    if args.command == "list":
        return _backend().list_day(home=args.home, day=args.date)
    payload = _payload(args.args_file)
    if payload["project_id"] != "__workspace__":
        _project(args.home, payload["project_id"])
    # Do not refresh revisions, retry conflicts, split plans, or infer state here.
    return _backend().mutate(args.home, payload, actor="agent")


def main(argv: list[str] | None = None) -> int:
    try:
        result = _run(_parser().parse_args(argv))
    except InputError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "error_type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, ImportError, BackendUnavailable) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "error_type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 1
    failed = result.get("ok") is False
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), file=sys.stderr if failed else sys.stdout)
    return 1 if failed else 0


if __name__ == "__main__":
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
