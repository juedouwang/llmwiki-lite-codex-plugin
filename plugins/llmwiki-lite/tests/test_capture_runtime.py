"""Project-index capture tests; never inspect the user's real sessions."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import research_capture_runtime as runtime
from research_capture import RolloutReader, CaptureInputError
from llmwiki_registry import register_project
from research_reports import report_settings, save_settings


class CaptureRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='capture-runtime-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = str(self.root / 'home')
        self.host = self.root / 'host'
        self.host.mkdir()
        self.projects = []
        for rel in ('parent_%', 'parent_%/child', 'elsewhere'):
            source = self.root / rel
            source.mkdir(parents=True, exist_ok=True)
            self.projects.append(register_project(str(source), home=self.home)['project'])
        self.p = self.projects[0]
        self.db = self.host / 'state_5.sqlite'
        with closing(sqlite3.connect(self.db, isolation_level=None)) as conn:
            conn.execute('create table threads(id text,cwd text,rollout_path text,updated_at integer)')

    def session(self, sid, project=None, *, cwd=None, thread_source='user', text='实验尚未验证', when='2026-09-19T14:00:00Z'):
        cwd = cwd or (project or self.p)['source_root']
        path = self.host / 'sessions' / (sid + '.jsonl')
        path.parent.mkdir(exist_ok=True)
        lines = [
            {'timestamp': when, 'type': 'session_meta', 'payload': {'id': sid, 'cwd': cwd, 'thread_source': thread_source}},
            {'timestamp': when, 'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}},
        ]
        path.write_text(''.join(json.dumps(line, ensure_ascii=False) + '\n' for line in lines), encoding='utf-8')
        with closing(sqlite3.connect(self.db, isolation_level=None)) as conn:
            conn.execute('insert into threads values(?,?,?,?)', (sid, cwd, str(path), 1))
        return path

    def enable(self):
        runtime.configure_capture(self.p, ['codex'], now='2026-09-19T13:00:00Z')

    def test_disabled_does_not_even_query_index(self):
        with patch.object(runtime, 'indexed_rollouts', side_effect=AssertionError('unauthorized read')):
            result = runtime.capture_project(self.p, self.projects, host_home=self.host)
        self.assertEqual(result['written'], 0)
        self.assertFalse((Path(self.p['state_root']) / 'workbench/runtime.sqlite3').exists())

    def test_project_and_nested_roots_configuration_thread_and_automation_isolation(self):
        expected = self.session('mine')
        self.session('nested', self.projects[1])
        self.session('other', self.projects[2])
        self.session('config')
        self.session('scheduled', thread_source='automation')
        _, paths, gaps = runtime.indexed_rollouts(self.p, self.projects, host_home=self.host, excluded_sessions=['config'])
        self.assertEqual(paths, [expected])
        self.assertFalse(gaps)

    def test_windows_verbatim_paths_and_wildcard_escaping(self):
        expected = self.session('mine')
        raw = self.p['source_root']
        if sys.platform == 'win32':
            with closing(sqlite3.connect(self.db, isolation_level=None)) as conn:
                conn.execute('update threads set cwd=?', ('\\\\?\\' + raw,))
        _, paths, _ = runtime.indexed_rollouts(self.p, self.projects, host_home=self.host)
        self.assertEqual(paths, [expected])

    def test_index_schema_failure_never_falls_back_to_account_scan(self):
        self.session('mine')
        with closing(sqlite3.connect(self.db, isolation_level=None)) as conn:
            conn.execute('drop table threads')
        with patch.object(runtime, 'read_session_meta', side_effect=AssertionError('body read')):
            _, paths, gaps = runtime.indexed_rollouts(self.p, self.projects, host_home=self.host)
        self.assertFalse(paths)
        self.assertTrue(gaps)

    def test_outside_transcript_is_rejected(self):
        self.session('mine')
        outside = self.root / 'private.jsonl'
        outside.write_text('private', encoding='utf-8')
        with closing(sqlite3.connect(self.db, isolation_level=None)) as conn:
            conn.execute('update threads set rollout_path=?', (str(outside),))
        with patch.object(runtime, 'read_session_meta', side_effect=AssertionError('outside read')):
            _, paths, gaps = runtime.indexed_rollouts(self.p, self.projects, host_home=self.host)
        self.assertFalse(paths)
        self.assertTrue(gaps)

    def test_capture_floor_idempotence_and_archive(self):
        self.enable()
        self.session('old', when='2026-09-19T12:00:00Z')
        path = self.session('current')
        self.assertEqual(runtime.capture_project(self.p, self.projects, host_home=self.host)['written'], 1)
        self.assertEqual(runtime.capture_project(self.p, self.projects, host_home=self.host)['written'], 0)
        archived = self.host / 'archived_sessions' / path.name
        archived.parent.mkdir()
        path.rename(archived)
        with closing(sqlite3.connect(self.db, isolation_level=None)) as conn:
            conn.execute('update threads set rollout_path=? where id=?', (str(archived), 'current'))
        self.assertEqual(runtime.capture_project(self.p, self.projects, host_home=self.host)['written'], 0)

    def test_ui_authorization_revoke_and_project_removal(self):
        settings = report_settings(self.home)
        settings = save_settings({'expected_revision': settings['revision'], 'project_ids': [self.p['id']], 'capture_hosts': ['codex']}, self.home)
        self.assertTrue(runtime.read_consent(self.p)['capture_enabled'])
        settings = save_settings({'expected_revision': settings['revision'], 'project_ids': []}, self.home)
        self.assertFalse(settings['activity_db_paths'])
        self.assertFalse(runtime.read_consent(self.p)['capture_enabled'])

    def test_reader_is_bounded_and_partial_line_can_resume(self):
        path = self.session('huge', text='x' * (9 * 1024 * 1024))
        first = RolloutReader().read(path)
        self.assertTrue(first.truncated)
        self.assertGreater(first.next_offset, 0)
        with self.assertRaises(CaptureInputError):
            RolloutReader().read(path, offset=first.next_offset)

    def test_timezone_floor_is_not_lexicographic(self):
        self.enable()
        self.session('before', when='2026-09-19T20:00:00+08:00')
        self.session('after', when='2026-09-19T22:00:00+08:00')
        self.assertEqual(runtime.capture_project(self.p, self.projects, host_home=self.host)['written'], 1)


if __name__ == '__main__':
    unittest.main()
