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

    def test_capture_drains_multiple_chunks_and_repeat_is_incremental(self):
        self.enable()
        path = self.session('large')
        with path.open('ab') as handle:
            # Ignored tool payload creates a backlog without expensive message writes.
            line = json.dumps({'type': 'padding', 'payload': 'x' * 1024}).encode() + b'\n'
            for _ in range(17000):
                handle.write(line)
            handle.write(json.dumps({'timestamp': '2026-09-19T15:00:00Z',
                'type': 'event_msg', 'payload': {'type': 'user_message',
                'message': 'tail evidence'}}).encode() + b'\n')
        result = runtime.capture_project(self.p, self.projects, host_home=self.host)
        self.assertTrue(result['complete'], result)
        self.assertGreaterEqual(result['passes'], 3)
        db = Path(self.p['state_root']) / 'workbench/runtime.sqlite3'
        with closing(sqlite3.connect(db)) as conn:
            offsets = [json.loads(r[0])['offset'] for r in conn.execute('select position_json from cursors')]
            self.assertEqual(offsets, [path.stat().st_size])
            self.assertTrue(conn.execute("select count(*) from activities where occurred_at='2026-09-19T15:00:00Z'").fetchone()[0])
        again = runtime.capture_project(self.p, self.projects, host_home=self.host)
        self.assertEqual(again['written'], 0)
        self.assertEqual(again['passes'], 1)

    def test_partial_tail_stops_without_looping(self):
        self.enable()
        path = self.session('partial')
        with path.open('ab') as handle:
            handle.write(b'{"unfinished":')
        result = runtime.capture_project(self.p, self.projects, host_home=self.host)
        self.assertFalse(result['complete'])
        self.assertLessEqual(result['passes'], 2)
        self.assertTrue(result['gaps'])

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


    def append_records(self, path, records):
        with path.open('a', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')

    def message(self, role, text, *, origin='response_item'):
        payload = ({'type': 'message', 'role': role, 'content': [{'type': 'input_text', 'text': text}]}
                   if origin == 'response_item' else
                   {'type': 'user_message' if role == 'user' else 'agent_message', 'message': text})
        return {'timestamp': '2026-09-19T14:00:00Z', 'type': origin, 'payload': payload}

    def test_mixed_thread_keeps_work_but_not_automatic_turn_in_both_streams(self):
        path = self.session('mixed', text='实现工作台功能')
        heartbeat = '<heartbeat>\n<automation_id>test</automation_id>\n<instructions>写日报</instructions>\n</heartbeat>'
        self.append_records(path, [self.message('assistant', '功能已经实现'),
            self.message('user', heartbeat), self.message('user', heartbeat, origin='event_msg'),
            self.message('assistant', '自动摘要'), self.message('assistant', '自动摘要', origin='event_msg'),
            {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'exec', 'arguments': 'automatic-secret', 'call_id': 'auto'}},
            {'type': 'compacted', 'payload': {'message': 'compaction summary'}},
            self.message('user', '# AGENTS.md instructions'),
            self.message('user', 'Another language model started to solve this problem: summary'),
            self.message('user', '<subagent_notification>任务完成</subagent_notification>'),
            self.message('user', '<subagent_notification>任务完成</subagent_notification>', origin='event_msg'),
            self.message('assistant', '自动流程续跑'),
            self.message('user', '接着修复Git页面'), self.message('assistant', '修复验证通过')])
        parsed = RolloutReader().read(path)
        self.assertEqual({x['text'] for x in parsed.messages}, {'实现工作台功能', '功能已经实现', '接着修复Git页面', '修复验证通过'})
        self.assertFalse(parsed.automatic_turn)

    def test_incremental_auto_state_and_legacy_cursor_do_not_leak(self):
        self.enable()
        path = self.session('mixed', text='正常开发')
        hb = '<heartbeat><automation_id>test</automation_id></heartbeat>'
        self.append_records(path, [self.message('user', hb), self.message('assistant', '不应采集1')])
        first = RolloutReader().read(path)
        self.assertTrue(first.automatic_turn)
        self.assertEqual(runtime.capture_project(self.p, self.projects, host_home=self.host)['written'], 1)
        self.append_records(path, [self.message('assistant', '不应采集2'), self.message('user', '正常继续'), self.message('assistant', '正常结果')])
        for state in (None, True):
            tail = RolloutReader().read(path, offset=first.next_offset, automatic_turn=state)
            self.assertEqual({x['text'] for x in tail.messages}, {'正常继续', '正常结果'})
        self.assertEqual(runtime.capture_project(self.p, self.projects, host_home=self.host)['written'], 2)
        self.assertEqual(runtime.capture_project(self.p, self.projects, host_home=self.host)['written'], 0)

    def test_mixed_thread_reaches_report_knowledge_and_literature_source_provider(self):
        from datetime import datetime, timezone, timedelta
        from research_sources import activity_sources, read_activity_source
        self.enable()
        path = self.session('runtime-thread', text='工作台增加版本图')
        self.append_records(path, [self.message('user', '<heartbeat><automation_id>test</automation_id></heartbeat>'),
            self.message('assistant', '自动日报不算新成果'),
            self.message('user', '修复 records/reports/ 编辑功能'), self.message('assistant', '编辑回归通过')])
        self.assertEqual(runtime.capture_project(self.p, self.projects, host_home=self.host)['written'], 3)
        settings = {'project_ids': [self.p['id']], 'capture_hosts': ['codex'],
                    'activity_db_paths': {self.p['id']: 'workbench/runtime.sqlite3'},
                    'runtime': {'target_thread_id': 'runtime-thread'}}
        now = datetime.now(timezone.utc) + timedelta(seconds=1)
        items, gaps = activity_sources(self.p, settings=settings, start_date='2026-09-19', now=now)
        self.assertFalse(gaps)
        self.assertEqual({i['text'] for i in items}, {'user:\n工作台增加版本图',
            'user:\n修复 records/reports/ 编辑功能', 'assistant:\n编辑回归通过'})
        for item in items:
            self.assertEqual(read_activity_source(self.p, item['locator'], settings=settings, now=now), item)

    def test_mentioning_heartbeat_is_not_an_automatic_turn(self):
        path = self.session('normal', text='需要修复 <heartbeat> 的过滤规则')
        self.append_records(path, [self.message('assistant', '已修复'), self.message('user', '<heartbeat>只是示例</heartbeat>')])
        self.assertEqual(len(RolloutReader().read(path).messages), 3)

    def test_explicit_session_binding_only_reads_selected_thread_and_since(self):
        self.enable()
        cache = str(self.root / 'plugin-cache')
        path = self.session('selected', cwd=cache, when='2026-09-19T12:00:00Z', text='授权范围前的历史')
        self.session('unselected', cwd=cache, text='无关隐私')
        self.append_records(path, [self.message('user', '工作台当天改进')])
        self.assertFalse(runtime.indexed_rollouts(self.p, self.projects, host_home=self.host)[1])
        runtime.bind_project_session(self.p, 'selected', since='2026-09-19T13:00:00Z', host_home=self.host)
        self.assertEqual(runtime.indexed_rollouts(self.p, self.projects, host_home=self.host)[1], [path])
        self.assertEqual(runtime.capture_project(self.p, self.projects, host_home=self.host)['written'], 1)
        self.assertEqual(runtime.capture_project(self.p, self.projects, host_home=self.host)['written'], 0)
        runtime.configure_capture(self.p, [])
        self.append_records(path, [self.message('user', '撤权后不可读取')])
        self.assertEqual(runtime.capture_project(self.p, self.projects, host_home=self.host)['written'], 0)

    def test_binding_does_not_enable_capture_or_expand_existing_history(self):
        self.session('selected')
        with self.assertRaises(ValueError):
            runtime.bind_project_session(self.p, 'selected', host_home=self.host)
        self.enable()
        runtime.bind_project_session(self.p, 'selected', since='2026-09-19T13:00:00Z', host_home=self.host)
        with self.assertRaises(ValueError):
            runtime.bind_project_session(self.p, 'selected', since='2026-09-18T13:00:00Z', host_home=self.host)
        self.session('scheduled', thread_source='automation')
        with self.assertRaises(ValueError):
            runtime.bind_project_session(self.p, 'scheduled', host_home=self.host)

    def test_bound_thread_identity_and_path_are_rechecked(self):
        self.enable()
        self.session('selected', cwd=str(self.root / 'cache'))
        runtime.bind_project_session(self.p, 'selected', host_home=self.host)
        replacement = self.session('different', cwd=str(self.root / 'cache'))
        with closing(sqlite3.connect(self.db, isolation_level=None)) as conn:
            conn.execute('update threads set rollout_path=? where id=?', (str(replacement), 'selected'))
        self.assertFalse(runtime.indexed_rollouts(self.p, self.projects, host_home=self.host)[1])


if __name__ == '__main__':
    unittest.main()
