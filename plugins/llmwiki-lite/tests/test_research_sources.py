"""确定性取材回归：全部 Git/SQLite/授权/提示均为临时合成夹具，不读取账户数据。"""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import research_capture as capture  # noqa: E402
import research_sources as sources  # noqa: E402
from workbench_store import SCHEMA_SQL  # noqa: E402

NOW = datetime(2026, 9, 19, 10, tzinfo=timezone.utc)
DAY = '2026-09-19'
EVENT = '2026-09-19T08:00:00Z'
SECRET = 'sk-' + 'syntheticsecret1234567890'
SOURCE_FIELDS = {'id', 'role', 'project_id', 'kind', 'occurred_at', 'observed_at', 'locator',
                 'revision', 'text', 'certainty'}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='llmwiki-source-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.state = self.root / 'state'
        self.wiki = self.root / 'wiki'
        for path in (self.source, self.state, self.wiki):
            path.mkdir()
        self.project = {'id': 'synthetic-project', 'source_root': str(self.source),
                        'state_root': str(self.state), 'wiki_root': str(self.wiki)}
        self.db = self.state / 'workbench' / 'runtime.sqlite3'
        self.settings = {'project_ids': [self.project['id']], 'activity_db_paths': {self.project['id']: str(self.db)},
                         'capture_hosts': ['codex'], 'start_date': '2026-01-01'}
        self.env = mock.patch.dict(os.environ, {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                                               'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.scan = mock.patch.object(capture.RolloutCaptureAdapter, 'scan', side_effect=AssertionError('禁止调用采集器'))
        self.scan.start()
        self.addCleanup(self.scan.stop)

    def authorize(self, **patch):
        values = {'capture_enabled': True, 'allow_source_text': True,
                  'hosts': {'codex': True, 'claude_code': False, 'opencode': False}}
        values.update(patch)
        capture.write_consent(self.project, values, now='2026-01-01T00:00:00.000Z')

    def database(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute('PRAGMA user_version=1')
        self.session()

    def session(self, sid='session-1', *, pid=None, host='codex', thread='thread-user', state='active'):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('INSERT INTO sessions(id,project_id,host,host_session_id,started_at,last_seen_at,consent_revision,state) '
                         'VALUES(?,?,?,?,?,?,?,?)',
                         (sid, pid or self.project['id'], host, thread, EVENT, EVENT, 'fixture-revision', state))

    def activity(self, aid='a1', *, pid=None, sid='session-1', kind='user_message', occurred=EVENT,
                 observed='2026-09-19T09:00:00Z', status='active', evidence=None, text='讨论实验，还未验证。', revision='r1'):
        evidence = {'text': text, 'host': 'codex', 'kind': kind} if evidence is None else evidence
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('INSERT INTO activities VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (aid, pid or self.project['id'], sid, kind, 'source:' + aid, revision, occurred,
                          observed, json.dumps(evidence, ensure_ascii=False), status))

    def activities(self, **kwargs):
        return sources.activity_sources(self.project, settings=kwargs.get('settings', self.settings),
                                        start_date=kwargs.get('start_date', DAY), now=kwargs.get('now', NOW),
                                        known_sources=kwargs.get('known_sources'))

    def read(self, aid='a1', **kwargs):
        return sources.read_activity_source(self.project, 'conversation:' + aid,
                                            now=kwargs.pop('now', NOW), **kwargs)

    def extra(self, day=DAY, **kwargs):
        return sources.extra_sources(self.project, day, settings=kwargs.get('settings', self.settings),
                                     now=kwargs.get('now', NOW))

    def hints(self, events):
        (self.state / 'events.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events), encoding='utf-8')

    def hint(self, paths, **extra):
        return {'kind': 'file-change-hint', 'timestamp': EVENT, 'paths': paths, **extra}

    def assert_contract(self, items):
        for item in items:
            self.assertEqual(set(item), SOURCE_FIELDS)
            self.assertEqual(item['project_id'], self.project['id'])
            self.assertEqual(item['role'], 'source')
            self.assertIn(item['certainty'], ('event', 'observation'))
        json.dumps(items, ensure_ascii=False)

    def assert_unavailable(self, code, function):
        with self.assertRaises(sources.ActivitySourceUnavailable) as caught:
            function()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()


class ActivityTests(Fixture):
    def test_contract_only_user_assistant_and_stable_locator(self):
        self.authorize()
        self.database()
        self.activity()
        self.activity('a2', kind='assistant_message', text='下一步建议，而非已完成结果。')
        self.activity('tool', kind='tool_call', text=SECRET)
        self.activity('result', kind='tool_result', text=SECRET)
        items, gaps = self.activities()
        self.assertEqual([x['locator'] for x in items], ['conversation:a1', 'conversation:a2'])
        self.assertEqual({x['kind'] for x in items}, {'conversation'})
        self.assert_contract(items)
        self.assertFalse(gaps)
        self.assertEqual(items[0], self.read())
        self.assertEqual(items[0], self.read(settings=self.settings))

    def test_three_consent_gates_precede_database_connection(self):
        self.database()
        self.activity()
        cases = ({'capture_enabled': False}, {'allow_source_text': False},
                 {'hosts': {'codex': False, 'claude_code': False, 'opencode': False}})
        for patch in cases:
            with self.subTest(patch=patch):
                self.authorize(**patch)
                with mock.patch.object(sources.sqlite3, 'connect', side_effect=AssertionError('不应开库')):
                    items, gaps = self.activities()
                    self.assertFalse(items)
                    self.assertTrue(gaps)
                    self.assert_unavailable('NOT_AUTHORIZED', self.read)
                    self.assert_unavailable('NOT_AUTHORIZED', lambda: self.read('absent'))

    def test_no_implicit_database_for_bulk_or_configured_single_read(self):
        self.authorize()
        self.database()
        self.activity()
        config = {**self.settings, 'activity_db_paths': {}}
        with mock.patch.object(sources.sqlite3, 'connect', side_effect=AssertionError('不可猜路径')):
            self.assertFalse(self.activities(settings=config)[0])
            self.assert_unavailable('NOT_CONFIGURED', lambda: self.read(settings=config))
        self.assertIsNotNone(self.read())  # 单条重验的唯一 canonical 回退。

    def test_settings_host_project_and_start_gates(self):
        self.authorize()
        self.database()
        self.activity()
        for config in ({**self.settings, 'project_ids': []}, {**self.settings, 'capture_hosts': []}):
            with self.subTest(config=config), mock.patch.object(sources.sqlite3, 'connect') as connect:
                self.assertFalse(self.activities(settings=config)[0])
                connect.assert_not_called()
        self.assertFalse(self.activities(settings={**self.settings, 'start_date': '2026-09-20'})[0])

    def test_project_host_status_filters_without_selecting_body(self):
        self.authorize()
        self.database()
        self.session('foreign', pid='other-project', thread='foreign-thread')
        self.session('host2', host='claude_code', thread='foreign-host')
        self.session('excluded', thread='excluded-thread', state='excluded')
        self.activity('foreign', pid='other-project')
        self.activity('cross-bind', sid='foreign')
        self.activity('wrong-host', sid='host2')
        self.activity('excluded', sid='excluded')
        self.activity('redacted', status='redacted')
        real_connect = sqlite3.connect

        def guarded(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_authorizer(lambda action, table, column, *rest:
                                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'activities'
                                and column == 'evidence_json' else sqlite3.SQLITE_OK)
            return conn

        with mock.patch.object(sources.sqlite3, 'connect', side_effect=guarded):
            items, gaps = self.activities()
        self.assertFalse(items)
        self.assertFalse(gaps, gaps)

    def test_beijing_half_open_day_and_future_filter(self):
        self.authorize()
        self.database()
        self.activity('boundary', occurred='2026-09-18T16:00:00Z')
        self.activity('before', occurred='2026-09-18T15:59:59.999999Z')
        self.activity('offset', occurred='2026-09-19T01:00:00+08:00')
        self.activity('future', occurred='2026-09-19T10:00:00.000001Z')
        self.activity('now', occurred=NOW.isoformat(), observed=NOW.isoformat())
        self.activity('future-observed', occurred=EVENT, observed='2026-09-20T00:00:00Z')
        items, _ = self.activities()
        self.assertEqual({x['locator'] for x in items}, {'conversation:boundary', 'conversation:offset', 'conversation:now'})
        with mock.patch.object(sources, '_GitProject', side_effect=sources._Unavailable('非 Git')):
            historical, _ = self.extra('2026-09-18')
        self.assertEqual([x['locator'] for x in historical], ['conversation:before'])

    def test_observation_fallback_never_reread_time_or_yesterday(self):
        self.authorize()
        self.database()
        self.activity(occurred=None, observed='2026-09-18T17:00:00Z')
        items, gaps = self.activities()
        self.assertEqual(items[0]['certainty'], 'observation')
        self.assertIsNone(items[0]['occurred_at'])
        self.assertEqual(items[0]['observed_at'], '2026-09-18T17:00:00+00:00')
        self.assertTrue(any('观察' in gap for gap in gaps))
        self.assertEqual(items, self.activities(now=NOW + timedelta(minutes=2))[0])

    def test_authorization_floor_and_invalid_time(self):
        self.authorize(authorized_at='2026-09-19T07:00:00.000Z')
        self.database()
        self.activity('old', occurred='2026-09-19T06:59:59Z')
        self.activity('invalid', occurred='2026-09-19T08:00:00')
        self.activity('allowed')
        items, gaps = self.activities()
        self.assertEqual([x['locator'] for x in items], ['conversation:allowed'])
        self.assertTrue(any('时间' in g for g in gaps))

    def test_automation_report_tool_and_injection_exclusion(self):
        self.authorize()
        self.database()
        self.session('automation', thread='runtime-thread')
        self.settings['runtime'] = {'target_thread_id': 'runtime-thread'}
        self.activity('runtime', sid='automation')
        self.activity('normal')
        variants = [
            {'thread_source': 'automation'}, {'metadata': {'thread_source': 'heartbeat'}},
            {'generated_report': True}, {'file': 'records/reports/daily/2026-09-19.md'},
            {'tool_name': 'execute'}, {'call_id': 'call-1'}, {'origin': 'function_call_output'},
            {'text': '# AGENTS.md instructions\n注入块'}, {'text': '写入 records/reports/daily/report.md'},
            {'host': 'claude_code'}, {'project_id': 'other-project'}, {'role': 'tool'},
        ]
        for index, patch in enumerate(variants):
            evidence = {'text': '不应读取为科研成果', 'kind': 'user_message', 'host': 'codex', **patch}
            self.activity(f'excluded-{index}', evidence=evidence)
        items, _ = self.activities()
        self.assertEqual([x['locator'] for x in items], ['conversation:normal'])
        self.assert_unavailable('SOURCE_EXCLUDED', lambda: self.read('runtime', settings=self.settings))

    def test_optional_session_provenance_fields(self):
        self.authorize()
        self.database()
        self.activity()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('ALTER TABLE sessions ADD COLUMN thread_source TEXT')
            conn.execute("UPDATE sessions SET thread_source='automation'")
        self.assertFalse(self.activities()[0])
        self.assert_unavailable('SOURCE_EXCLUDED', self.read)

    def test_sensitive_text_and_locator_are_redacted(self):
        self.authorize()
        self.database()
        self.activity(text='试验失败。api_key=fixture-password-value ' + SECRET)
        items, gaps = self.activities()
        serialized = json.dumps(items)
        self.assertNotIn(SECRET, serialized)
        self.assertNotIn('fixture-password-value', serialized)
        self.assertIn('[REDACTED]', serialized)
        self.assertTrue(any('脱敏' in g for g in gaps))
        self.activity('sensitive', text='读取 private/keys.txt', evidence=None)
        self.authorize(exclude_patterns=['private/**'])
        self.assertNotIn('conversation:sensitive', [x['locator'] for x in self.activities()[0]])

    def test_body_and_row_limits_have_explicit_gaps(self):
        self.authorize()
        self.database()
        self.activity('large', text='资料' * 300)
        with mock.patch.object(sources, 'MAX_ACTIVITY_BYTES', 128):
            items, gaps = self.activities()
        self.assertFalse(items)
        self.assertTrue(any('上限' in g for g in gaps))
        self.activity('second')
        with mock.patch.object(sources, 'MAX_ACTIVITIES', 1):
            items, gaps = self.activities()
        self.assertEqual(len(items), 1)
        self.assertTrue(any('条上限' in g for g in gaps))

    def test_revision_independent_of_now_observed_time_and_database_location(self):
        self.authorize()
        self.database()
        self.activity()
        before = self.activities()[0][0]
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE activities SET observed_at='2026-09-19T09:30:00Z'")
        alternate = self.state / 'copy.sqlite3'
        shutil.copyfile(self.db, alternate)
        config = {**self.settings, 'activity_db_paths': {self.project['id']: str(alternate)}}
        after = self.activities(settings=config, now=NOW + timedelta(minutes=30))[0][0]
        self.assertNotEqual(before['observed_at'], after['observed_at'])
        self.assertEqual((before['id'], before['revision']), (after['id'], after['revision']))
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE activities SET source_revision='r2'")
        self.assertNotEqual(before['revision'], self.read()['revision'])

    def test_readonly_connection_does_not_change_database_or_create_files(self):
        self.authorize()
        self.database()
        self.activity()
        before = {p.relative_to(self.state): self.digest(p) for p in self.state.rglob('*') if p.is_file()}
        real_connect = sqlite3.connect
        with mock.patch.object(sources.sqlite3, 'connect', wraps=real_connect) as connect:
            self.assertTrue(self.activities()[0])
            self.assertTrue(self.read())
        for call in connect.call_args_list:
            self.assertIn('?mode=ro', call.args[0])
            self.assertTrue(call.kwargs['uri'])
        after = {p.relative_to(self.state): self.digest(p) for p in self.state.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_deletion_distinct_from_denied_missing_corrupt_and_redacted(self):
        self.authorize()
        self.database()
        self.assertIsNone(self.read('absent'))
        self.activity(status='redacted')
        self.assert_unavailable('SOURCE_EXCLUDED', self.read)
        self.db.unlink()
        self.assert_unavailable('UNREADABLE', self.read)
        self.assertFalse(self.db.exists())
        self.db.write_bytes(b'synthetic non-sqlite data')
        self.assert_unavailable('UNREADABLE', self.read)

    def test_schema_missing_view_and_future_are_not_deletions(self):
        self.authorize()
        self.database()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('PRAGMA user_version=2')
        self.assert_unavailable('UNSUPPORTED_SCHEMA', self.read)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('PRAGMA user_version=1')
            conn.execute('DROP TABLE activities')
            conn.execute('CREATE VIEW activities AS SELECT 1 AS id')
        self.assert_unavailable('UNSUPPORTED_SCHEMA', self.read)
        self.assertFalse(self.activities()[0])

    def test_outside_traversal_and_symlink_do_not_open_database(self):
        self.authorize()
        outside = self.root / 'outside.sqlite3'
        outside.write_bytes(b'not a database')
        for raw in (str(outside), '../outside.sqlite3'):
            config = {**self.settings, 'activity_db_paths': {self.project['id']: raw}}
            with self.subTest(raw=raw), mock.patch.object(sources.sqlite3, 'connect') as connect:
                self.assert_unavailable('UNREADABLE', lambda: self.read(settings=config))
                connect.assert_not_called()
        link = self.state / 'outside-link.sqlite3'
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('本机不允许临时符号链接；越界/遍历断言已执行')
        config = {**self.settings, 'activity_db_paths': {self.project['id']: str(link)}}
        self.assert_unavailable('UNREADABLE', lambda: self.read(settings=config))

    def test_canonical_only_and_invalid_locator(self):
        self.authorize()
        self.database()
        self.activity()
        alternate = self.state / 'alternate.sqlite3'
        self.db.rename(alternate)
        self.assert_unavailable('UNREADABLE', self.read)
        config = {**self.settings, 'activity_db_paths': {self.project['id']: str(alternate)}}
        self.assertIsNotNone(self.read(settings=config))
        self.assert_unavailable('INVALID_LOCATOR', lambda: sources.read_activity_source(self.project, '../a1'))

    def test_revocation_between_metadata_and_body_reads(self):
        self.authorize()
        self.database()
        self.activity()
        original = sources._authorization
        first = original(self.project, self.settings)
        denied = sources.ActivitySourceUnavailable('NOT_AUTHORIZED', '授权已撤回')
        with mock.patch.object(sources, '_authorization', side_effect=[first, denied]):
            items, gaps = self.activities()
        self.assertFalse(items)
        self.assertIn('授权已撤回', gaps)

    def test_malformed_evidence_is_a_gap_not_deleted(self):
        self.authorize()
        self.database()
        self.activity(evidence=[])
        self.assert_unavailable('INVALID_SOURCE', self.read)
        self.assertTrue(self.activities()[1])


class ActivityIncrementalTests(Fixture):
    def setUp(self):
        super().setUp()
        self.authorize()
        self.database()

    def batch(self, count):
        # A single synthetic transaction keeps the >1000-row regression cheap on Windows.
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executemany('INSERT INTO activities VALUES(?,?,?,?,?,?,?,?,?,?)', (
                (f'a{index:04}', self.project['id'], 'session-1', 'user_message',
                 f'source:a{index:04}', 'r1', EVENT, EVENT,
                 json.dumps({'text': f'合成消息 {index}', 'host': 'codex', 'kind': 'user_message'}), 'active')
                for index in range(count)))

    def test_more_than_1000_consumed_rows_do_not_starve_next_batch(self):
        self.assertEqual(sources.MAX_ACTIVITIES, 1000)
        self.batch(1003)
        first, gaps = self.activities()
        self.assertEqual(len(first), 1000)
        self.assertTrue(any('1000 条上限' in gap for gap in gaps))
        known = {item['locator']: item['revision'] for item in first}
        before = known.copy()
        second, gaps = self.activities(known_sources=known)
        self.assertEqual([item['locator'] for item in second],
                         ['conversation:a1000', 'conversation:a1001', 'conversation:a1002'])
        self.assertFalse(gaps)
        self.assertEqual(known, before, 'consumer owns the checkpoint; failed consumption must not advance it')
        self.assert_contract(first + second)
        known.update((item['locator'], item['revision']) for item in second)
        self.assertEqual(self.activities(known_sources=known), ([], []))
        # Existing callers (daily reports) retain the bounded, explicitly incomplete snapshot.
        repeated, gaps = self.activities()
        self.assertEqual(repeated, first)
        self.assertTrue(gaps)

    def test_same_id_revision_and_late_old_message_are_not_lost(self):
        self.batch(1001)
        known = {}
        for _ in range(2):
            items, _ = self.activities(known_sources=known)
            known.update((item['locator'], item['revision']) for item in items)
        old = self.read('a0000')
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('UPDATE activities SET source_revision=?,evidence_json=? WHERE id=?',
                         ('r2', json.dumps({'text': '修订后的实验讨论', 'host': 'codex', 'kind': 'user_message'}),
                          'a0000'))
        self.activity('late-arrival', occurred='2026-09-19T07:00:00Z')
        items, gaps = self.activities(known_sources=known)
        self.assertEqual({item['locator'] for item in items}, {'conversation:a0000', 'conversation:late-arrival'})
        revised = next(item for item in items if item['locator'] == old['locator'])
        self.assertEqual(revised, self.read('a0000'))
        self.assertNotEqual((revised['id'], revised['revision']), (old['id'], old['revision']))
        self.assertIn('修订后的实验讨论', revised['text'])
        self.assertFalse(gaps)
        known.update((item['locator'], item['revision']) for item in items)
        self.assertEqual(self.activities(known_sources=known), ([], []))

    def test_known_revision_ignores_observation_clock_and_does_not_read_body(self):
        self.activity()
        item = self.read()
        known = {item['locator']: item['revision']}
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE activities SET observed_at='2026-09-19T10:00:00Z'")
        with mock.patch.object(sources._Activities, 'source', side_effect=AssertionError('unchanged source body read')):
            self.assertEqual(self.activities(known_sources=known, now=NOW + timedelta(hours=1)), ([], []))
        self.assertEqual(self.read()['revision'], item['revision'])
        # Correcting the actual event time changes the version, unlike an observation refresh.
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE activities SET occurred_at='2026-09-19T07:00:00Z'")
        items, _ = self.activities(known_sources=known)
        self.assertEqual(len(items), 1)
        self.assertNotEqual(items[0]['revision'], item['revision'])

    def test_known_sources_none_settings_uses_only_canonical_and_checks_consent(self):
        self.activity()
        first, gaps = self.activities(settings=None)
        self.assertFalse(gaps)
        self.assertEqual(first, [self.read()])
        known = {item['locator']: item['revision'] for item in first}
        self.activity('second')
        self.assertEqual([item['locator'] for item in self.activities(settings=None, known_sources=known)[0]],
                         ['conversation:second'])
        self.authorize(allow_source_text=False)
        with mock.patch.object(sources.sqlite3, 'connect', side_effect=AssertionError('revoked consent must gate DB')):
            items, gaps = self.activities(settings=None, known_sources=known)
            self.assertFalse(items)
            self.assertTrue(gaps)
            self.assert_unavailable('NOT_AUTHORIZED', self.read)
        self.authorize()
        self.db.rename(self.state / 'other.sqlite3')
        items, gaps = self.activities(settings=None, known_sources=known)
        self.assertFalse(items)
        self.assertTrue(gaps)
        self.assertFalse(self.db.exists())

    def test_text_budget_does_not_checkpoint_unreturned_items(self):
        self.batch(3)
        known = {}
        one_message_size = len(self.read('a0000')['text'].encode('utf-8'))
        with mock.patch.object(sources, 'MAX_TOTAL_TEXT_BYTES', one_message_size):
            for index in range(3):
                items, gaps = self.activities(known_sources=known)
                self.assertEqual([item['locator'] for item in items], [f'conversation:a{index:04}'])
                self.assertEqual(bool(gaps), index < 2)
                known.update((item['locator'], item['revision']) for item in items)
            self.assertEqual(self.activities(known_sources=known), ([], []))

    def test_invalid_known_sources_are_rejected_before_database_access(self):
        for invalid in (['conversation:a1'], {'conversation:a1': None}, {1: 'revision'}):
            with self.subTest(invalid=invalid), mock.patch.object(sources.sqlite3, 'connect') as connect:
                with self.assertRaises(ValueError):
                    self.activities(known_sources=invalid)
                connect.assert_not_called()

    def test_connections_explicitly_closed_after_incremental_queries_and_failure(self):
        self.activity()
        real_connect = sqlite3.connect
        connections = []

        def connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            connections.append(conn)
            return conn

        with mock.patch.object(sources.sqlite3, 'connect', side_effect=connect):
            first, _ = self.activities()
            known = {item['locator']: item['revision'] for item in first}
            self.assertEqual(self.activities(known_sources=known), ([], []))
            self.assertIsNotNone(self.read())
            with mock.patch.object(sources, '_schema', side_effect=sqlite3.DatabaseError('synthetic failure')):
                self.assertTrue(self.activities(known_sources=known)[1])
                self.assert_unavailable('UNREADABLE', self.read)
        self.assertEqual(len(connections), 5)
        for conn in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute('SELECT 1')


class HintTests(Fixture):
    def test_non_git_hints_are_scoped_observations_and_redacted(self):
        self.hints([self.hint(['src/main.py', '.env', 'records/reports/daily/report.md']),
                    self.hint(['other.py'], project_id='other'),
                    self.hint(['auto.py'], automation_self=True),
                    self.hint([SECRET + '.txt'])])
        items, gaps = self.extra()
        self.assertEqual({i['kind'] for i in items}, {'file_change_hint'})
        self.assertEqual(len(items), 2)
        self.assert_contract(items)
        self.assertEqual(items[0]['certainty'], 'observation')
        self.assertIsNone(items[0]['occurred_at'])
        self.assertNotIn(SECRET, json.dumps(items))
        self.assertNotIn('records/reports', json.dumps(items))
        self.assertTrue(any('Git' in g for g in gaps))
        self.assertTrue(any('授权' in g for g in gaps))

    def test_undated_hints_only_today_and_stable_on_reobservation(self):
        self.hints([self.hint(['src/main.py'], timestamp=None)])
        today, gaps = self.extra()
        self.assertTrue(any('缺少可信时间' in g for g in gaps))
        self.assertFalse(self.extra('2026-09-18')[0])
        later, _ = self.extra(now=NOW + timedelta(minutes=20))
        self.assertNotEqual(today[0]['observed_at'], later[0]['observed_at'])
        self.assertEqual(today[0]['id'], later[0]['id'])
        self.assertEqual(today[0]['revision'], later[0]['revision'])

    def test_hint_limits_and_invalid_lines_are_transparent(self):
        self.hints([self.hint(['a.py']), self.hint(['b.py'])])
        with mock.patch.object(sources, 'MAX_HINTS', 1):
            items, gaps = self.extra()
        self.assertEqual(len(items), 1)
        self.assertTrue(any('上限' in g for g in gaps))
        (self.state / 'events.jsonl').write_bytes(b'{invalid}\n' + b'x' * 200)
        with mock.patch.object(sources, 'MAX_HINT_LINE_BYTES', 64):
            items, gaps = self.extra()
        self.assertFalse(items)
        self.assertTrue(any('格式' in g for g in gaps))
        self.assertTrue(any('上限' in g for g in gaps))

    def test_future_unselected_and_before_start_do_not_read(self):
        with mock.patch.object(sources, '_Policy') as policy, mock.patch.object(sources.sqlite3, 'connect') as connect:
            self.assertFalse(self.extra('2026-09-20')[0])
            self.assertFalse(self.extra(settings={**self.settings, 'project_ids': []})[0])
            self.assertFalse(self.extra('2025-12-31')[0])
            policy.assert_not_called()
            connect.assert_not_called()

    def test_git_timeout_does_not_block_file_hints(self):
        self.hints([self.hint(['a.py'])])
        with mock.patch.object(sources, '_git', side_effect=sources._Unavailable('Git 超时')):
            items, gaps = self.extra()
        self.assertEqual(len(items), 1)
        self.assertIn('Git 超时', gaps)


@unittest.skipUnless(shutil.which('git'), '需要本机 Git；不会安装依赖')
class GitTests(Fixture):
    def setUp(self):
        super().setUp()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.autocrlf', 'false')

    def git(self, *args, stamp=EVENT, author=None):
        env = os.environ.copy()
        env.update(GIT_COMMITTER_DATE=stamp, GIT_AUTHOR_DATE=author or stamp)
        result = subprocess.run(['git', '-C', str(self.source), *args], shell=False, env=env,
                                capture_output=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace')[-1000:])
        return result.stdout.decode('utf-8').strip()

    def commit(self, files, *, stamp=EVENT, message='Synthetic code change', author=None):
        for name, content in files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        self.git('add', '-f', '--', *files)
        self.git('commit', '-q', '-m', message, stamp=stamp, author=author)
        return self.git('rev-parse', 'HEAD')

    def test_root_commit_metadata_diff_and_committer_beijing_day(self):
        oid = self.commit({'src/main.py': 'print("unverified")\n'}, stamp='2026-09-18T16:00:00Z',
                          author='2025-01-01T00:00:00Z')
        items, _ = self.extra()
        self.assertEqual(len(items), 1)
        self.assert_contract(items)
        self.assertEqual(items[0]['locator'], 'git:commit:' + oid)
        self.assertIn('+print("unverified")', items[0]['text'])
        self.assertEqual(items[0]['occurred_at'], '2026-09-18T16:00:00+00:00')
        self.assertEqual(items[0]['certainty'], 'event')
        self.assertEqual(items, self.extra(now=NOW + timedelta(minutes=20))[0])

    def test_staged_unstaged_and_untracked_are_today_only_observations(self):
        self.commit({'main.py': 'base\n'}, stamp='2026-09-18T08:00:00Z')
        (self.source / 'main.py').write_text('staged\n', encoding='utf-8')
        self.git('add', 'main.py')
        (self.source / 'main.py').write_text('unstaged\n', encoding='utf-8')
        (self.source / 'new.py').write_text('UNTRACKED_BODY_NOT_READ', encoding='utf-8')
        before = self.digest(self.source / '.git/index')
        items, gaps = self.extra()
        worktree = next(x for x in items if x['kind'] == 'git_worktree')
        self.assertEqual(worktree['certainty'], 'observation')
        self.assertIsNone(worktree['occurred_at'])
        self.assertIn('+staged', worktree['text'])
        self.assertIn('+unstaged', worktree['text'])
        self.assertIn('?? new.py', worktree['text'])
        self.assertNotIn('UNTRACKED_BODY_NOT_READ', worktree['text'])
        self.assertTrue(any('未跟踪' in g for g in gaps))
        self.assertEqual(before, self.digest(self.source / '.git/index'))
        history, gaps = self.extra('2026-09-18')
        self.assertNotIn('git_worktree', [x['kind'] for x in history])
        self.assertTrue(any('历史工作区' in g for g in gaps))
        later = next(x for x in self.extra(now=NOW + timedelta(minutes=3))[0] if x['kind'] == 'git_worktree')
        self.assertEqual((worktree['id'], worktree['revision']), (later['id'], later['revision']))

    def test_sensitive_generated_and_ignored_paths_never_enter_diff(self):
        self.commit({'.llmwikiignore': 'private/\n', '.gitignore': 'ignored.txt\n'}, stamp='2026-09-18T08:00:00Z')
        blocked = {'.env': 'PRIVATE_ENV_CONTENT', 'keys/a.pem': 'PRIVATE_KEY_CONTENT',
                   'private/a.txt': 'PRIVATE_IGNORED_CONTENT', 'ignored.txt': 'GIT_IGNORED_CONTENT',
                   'records/reports/daily/a.md': 'GENERATED_REPORT_CONTENT',
                   'node_modules/pkg/a.js': 'GENERATED_DEPENDENCY_CONTENT', '.llmwiki/cache.json': 'STATE_CONTENT'}
        self.commit({'safe.py': 'SAFE_CODE\n', **blocked}, message='Code and generated files')
        self.hints([self.hint(list(blocked) + ['safe.py'])])
        items, _ = self.extra()
        serialized = json.dumps(items)
        self.assertIn('SAFE_CODE', serialized)
        for name, body in blocked.items():
            with self.subTest(path=name):
                self.assertNotIn(body, serialized)
                self.assertNotIn(name, serialized)
        self.commit({'records/reports/daily/a.md': 'NEXT_GENERATED_REPORT'})
        items2, _ = self.extra()
        self.assertEqual([x['id'] for x in items], [x['id'] for x in items2])

    def test_sensitive_rename_both_endpoints_excluded(self):
        self.commit({'.env': 'privatevalue\n'}, stamp='2026-09-18T08:00:00Z')
        self.git('mv', '.env', 'innocent.txt')
        self.assertFalse(self.extra()[0])
        self.git('commit', '-q', '-m', 'Rename')
        self.assertFalse(self.extra()[0])

    def test_diff_budget_discards_partial_secrets_with_explicit_gap(self):
        self.commit({'safe.txt': 'START\n-----BEGIN PRIVATE KEY-----\n' + 'sensitive' * 500 + '\n-----END PRIVATE KEY-----\n'})
        with mock.patch.object(sources, 'MAX_DIFF_BYTES', 128):
            items, gaps = self.extra()
        self.assertEqual(len(items), 1)  # 提交元数据仍可用，正文未假装完整。
        self.assertNotIn('sensitive', items[0]['text'])
        self.assertNotIn('BEGIN PRIVATE', items[0]['text'])
        self.assertTrue(any('文本差异' in g and '上限' in g for g in gaps))

    def test_commit_and_path_limits_have_gaps(self):
        self.commit({'a.txt': 'a\n'})
        self.commit({'b.txt': 'b\n', 'c.txt': 'c\n'})
        with mock.patch.object(sources, 'MAX_COMMITS', 1), mock.patch.object(sources, 'MAX_PATHS', 1):
            items, gaps = self.extra()
        self.assertEqual(len(items), 1)
        self.assertTrue(any('提交超过' in g for g in gaps))
        self.assertTrue(any('路径超过' in g for g in gaps))

    def test_git_secrets_are_redacted_in_metadata_and_body(self):
        self.commit({'safe.txt': SECRET + '\n'}, message='password=synthetic-long-password')
        items, gaps = self.extra()
        raw = json.dumps(items)
        self.assertNotIn(SECRET, raw)
        self.assertNotIn('synthetic-long-password', raw)
        self.assertTrue(any('脱敏' in g for g in gaps))

    def test_git_argv_environment_no_window_and_no_external_processors(self):
        self.commit({'safe.txt': 'before\n', '.gitattributes': '*.txt diff=fixture\n'}, stamp='2026-09-18T08:00:00Z')
        marker = self.root / 'external-processor-ran'
        helper = self.root / 'processor.py'
        helper.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("ran")\n', encoding='utf-8')
        command = '"' + sys.executable.replace('\\', '/') + '" "' + str(helper).replace('\\', '/') + '"'
        self.git('config', 'diff.external', command)
        self.git('config', 'diff.fixture.command', command)
        self.git('config', 'diff.fixture.textconv', command)
        self.commit({'safe.txt': 'after\n'})
        real_popen = subprocess.Popen
        with mock.patch.object(sources.subprocess, 'Popen', wraps=real_popen) as popen:
            items, _ = self.extra()
        self.assertTrue(items)
        self.assertFalse(marker.exists())
        for call in popen.call_args_list:
            self.assertFalse(call.kwargs['shell'])
            self.assertEqual(call.kwargs['env']['GIT_OPTIONAL_LOCKS'], '0')
            self.assertEqual(call.kwargs['creationflags'], getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertIn('core.fsmonitor=false', call.args[0])
            if 'diff' in call.args[0] or 'diff-tree' in call.args[0]:
                self.assertIn('--no-ext-diff', call.args[0])
                self.assertIn('--no-textconv', call.args[0])

    def test_nested_project_never_reads_sibling_changes(self):
        self.commit({'inner/ok.py': 'project\n', 'sibling/no.py': 'OTHER_PROJECT_SECRET\n'})
        self.project['source_root'] = str(self.source / 'inner')
        (self.source / 'inner/ok.py').write_text('project changed\n', encoding='utf-8')
        (self.source / 'sibling/no.py').write_text('SIBLING_WORKTREE_CONTENT\n', encoding='utf-8')
        items, _ = self.extra()
        raw = json.dumps(items)
        self.assertIn('project changed', raw)
        self.assertIn('ok.py', raw)
        self.assertNotIn('OTHER_PROJECT_SECRET', raw)
        self.assertNotIn('SIBLING_WORKTREE_CONTENT', raw)
        self.assertNotIn('sibling', raw)

    def test_git_timeout_kills_process_at_ten_seconds(self):
        proc = mock.Mock(stdout=io.BytesIO(b''), stdin=None, returncode=-9)
        proc.wait.side_effect = [subprocess.TimeoutExpired('git', 10), -9]
        with mock.patch.object(sources.subprocess, 'Popen', return_value=proc):
            with self.assertRaises(sources._Unavailable) as caught:
                sources._git(self.source, ['status'])
        self.assertIn('10秒', str(caught.exception))
        proc.wait.assert_any_call(timeout=10)
        proc.kill.assert_called_once()


if __name__ == '__main__':
    unittest.main()
