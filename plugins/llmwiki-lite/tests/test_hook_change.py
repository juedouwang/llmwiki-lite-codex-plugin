"""Disposable, real-process file-change Hook and async configuration checks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from llmwiki_registry import register_project  # noqa: E402
import knowledge_maintenance as km  # noqa: E402


class HookChangeTests(unittest.TestCase):
    def test_patch_hint_real_subprocess_and_knowledge_consumption(self):
        with tempfile.TemporaryDirectory(prefix='hook-change-') as temp:
            root = Path(temp)
            source = root / 'project'
            source.mkdir()
            home = str(root / 'home')
            project = register_project(str(source), home=home)['project']
            (source / 'model.py').write_text('value = 7\n', encoding='utf-8')
            payload = {'hook_event_name': 'PostToolUse', 'tool_name': 'apply_patch',
                       'session_id': 'session-actual', 'task_id': 'f' * 32,
                       'cwd': str(source), 'tool_response': {'ok': True},
                       'tool_input': {'command': '*** Begin Patch\n*** Add File: model.py\n+value = 7\n*** End Patch'}}
            script = Path(__file__).resolve().parents[1] / 'scripts' / 'record_change.py'
            run = subprocess.run([sys.executable, '-I', '-B', str(script)],
                                 input=json.dumps(payload), text=True, capture_output=True,
                                 env={**os.environ, 'LLMWIKI_HOME': home}, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            events = Path(project['state_root']) / 'events.jsonl'
            captured = [json.loads(x) for x in events.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(captured[-1]['paths'], ['model.py'])
            self.assertEqual((captured[-1]['task_id'], captured[-1]['session_id']),
                             ('f' * 32, 'session-actual'))
            planned = km.knowledge_plan('manual', project['id'], home=home)
            self.assertTrue(planned['run_id'])
            self.assertEqual(km.state(project)['event_cursor'], events.stat().st_size)
            changes = km.knowledge_sources(planned['run_id'], 'changes', home=home)
            self.assertIn('source:model.py', [x['locator'] for x in changes['items']])

    def test_every_configured_codex_hook_is_async(self):
        config = json.loads((Path(__file__).resolve().parents[1] / 'hooks' / 'hooks.json').read_text(encoding='utf-8'))
        self.assertIn('PostToolUse', config['hooks'])
        self.assertIn('Stop', config['hooks'])
        for name, groups in config['hooks'].items():
            for group in groups:
                for command in group['hooks']:
                    with self.subTest(event=name):
                        self.assertIs(command.get('async'), True)


if __name__ == '__main__':
    unittest.main()
