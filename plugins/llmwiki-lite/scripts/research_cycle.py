"""One-shot JSON CLI for the shared host workflow, not a background exec runner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from research_schedule import (  # noqa: E402
    automation_prompt, bind_runtime, schedule_begin, schedule_call, schedule_finish,
)
from research_reports import report_settings  # noqa: E402


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stdin.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description='科研共享计划的一次性确定性入口；无模型、无守护进程。')
    parser.add_argument('--home', required=True)
    parser.add_argument('command', choices=['status', 'prompt', 'bind', 'begin', 'call', 'finish'])
    parser.add_argument('--cycle-id')
    parser.add_argument('--tool')
    parser.add_argument('--args-file', help='UTF-8 JSON object, or - to read stdin.')
    parser.add_argument('--automation-id')
    parser.add_argument('--thread-id')
    args = parser.parse_args()
    try:
        if args.command == 'status':
            result = report_settings(args.home)
        elif args.command == 'prompt':
            result = {'ok': True, 'prompt': automation_prompt(args.home)}
        elif args.command == 'bind':
            result = bind_runtime(args.automation_id, args.thread_id, args.home)
        elif args.command == 'begin':
            result = schedule_begin(args.home)
        elif args.command == 'finish':
            result = schedule_finish(args.cycle_id, args.home)
        else:
            payload = {}
            if args.args_file:
                if args.args_file != '-' and Path(args.args_file).stat().st_size > 3 * 1024 * 1024:
                    raise ValueError('输入过大。')
                raw = sys.stdin.read(3 * 1024 * 1024 + 1) if args.args_file == '-' else Path(args.args_file).read_text(encoding='utf-8-sig')
                if len(raw.encode('utf-8')) > 3 * 1024 * 1024:
                    raise ValueError('输入过大。')
                payload = json.loads(raw)
            result = schedule_call(args.cycle_id, args.tool, payload, args.home)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('ok', True) else 1
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': {'code': getattr(exc, 'code', 'INVALID_INPUT'),
                                               'message': str(exc) if isinstance(exc, ValueError) or hasattr(exc, 'code') else '本轮调用失败；请检查配置与来源。'}}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
