"""Actual dispatch/storage chain in isolated projects; model answers are labeled fixture inputs."""
from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import unittest
from uuid import uuid4

import test_research_schedule as schedule_tests
import research_schedule as schedule
import research_reports as reports
import knowledge_maintenance as knowledge
from research_capture import write_consent
from literature_catalog import LiteratureCatalog
from literature_collection import ReportSourceProvider


class WorkflowTests(unittest.TestCase):
    official = schedule_tests.ScheduleTests.official
    begin = schedule_tests.ScheduleTests.begin
    call = schedule_tests.ScheduleTests.call
    finish = schedule_tests.ScheduleTests.finish

    def setUp(self):
        schedule_tests.ScheduleTests.setUp(self)
        self.day = datetime.now(reports.CST).date().isoformat()
        source = Path(self.p['source_root'])
        (source / 'main.py').write_text('def compute():\n    return 42\n', encoding='utf-8')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        for args in [('init',), ('config', 'user.email', 'fixture@example.test'), ('config', 'user.name', 'fixture'), ('add', 'main.py'), ('commit', '-m', 'fixture implementation, not experiment validation')]:
            subprocess.run(['git', '-C', str(source), *args], capture_output=True, check=True, creationflags=flags)
        settings = reports.report_settings(self.home)
        reports.save_settings({'expected_revision': settings['revision'], 'capture_hosts': ['codex']}, self.home)
        floor = (self.clock - timedelta(minutes=5)).isoformat().replace('+00:00', 'Z')
        write_consent(self.p, {'authorized_at': floor})
        self.host.mkdir(parents=True, exist_ok=True)
        event = self.clock.isoformat()  # Do not accidentally create yesterday's event at midnight.
        self.text = '讨论计算模块：返回42只是代码示例，尚未硬件验证。推荐研究论文 https://doi.org/10.1234/test ，仅记录地址，不下载。'
        rollout = self.host / 'sessions' / 'fixture.jsonl'
        rollout.parent.mkdir()
        records = [
            {'type': 'session_meta', 'timestamp': event, 'payload': {'id': 'fixture', 'cwd': str(source), 'thread_source': 'user'}},
            {'type': 'response_item', 'timestamp': event, 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': self.text}]}},
        ]
        rollout.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
        with closing(sqlite3.connect(self.host / 'state_5.sqlite', isolation_level=None)) as conn:
            conn.execute('create table threads(id text,cwd text,rollout_path text,updated_at integer)')
            conn.execute('insert into threads values(?,?,?,?)', ('fixture', str(source), str(rollout), 1))

    def pages(self, cid, stage, run, **kw):
        items, cursor = [], None
        while True:
            result = self.call(cid, stage + '_sources', {'run_id': run, 'cursor': cursor, **kw})
            self.assertTrue(result['ok'], result)
            items.extend(result['items'])
            cursor = result['next_cursor']
            if cursor is None:
                return items

    def test_three_stages_take_original_conversation_save_independently_and_deduplicate(self):
        cid = self.begin()['cycle_id']
        plan = self.call(cid, 'report_plan')
        self.assertTrue(plan['runs'], plan)
        run = next(r for r in plan['runs'] if r['kind'] == 'daily')
        items = self.pages(cid, 'report', run['run_id'])
        conversation = next(i for i in items if i.get('kind') == 'conversation')
        self.assertIn('尚未硬件验证', conversation['text'])
        self.assertTrue(any(i.get('kind') == 'git_commit' for i in items))
        result = self.call(cid, 'report_finish', {'run_id': run['run_id'], 'outcome': 'generated',
            'body': '# 隔离测试日报\n讨论计算模块及论文线索，代码尚未完成硬件验证。',
            'source_ids': [conversation['id']], 'source_summaries': {conversation['id']: '人工指定的测试总结，不是模型生成验收。'}})
        self.assertTrue(result['ok'], result)
        report = reports.load(reports.workspace(self.home), 'daily', self.day)
        self.assertIn('尚未', report['body'])
        krun = self.call(cid, 'knowledge_plan')['run_id']
        changes = self.pages(cid, 'knowledge', krun, view='changes')
        self.pages(cid, 'knowledge', krun, view='catalog')
        source = next(i for i in changes if i['locator'].startswith('conversation:'))
        action = {'action_id': uuid4().hex, 'mode': 'create', 'page_path': 'knowledge/computation.md', 'base_sha256': None,
                  'content': '# 计算模块\n返回42是示例，尚未硬件验证。', 'reason': '隔离测试来源',
                  'evidence_refs': [{k: source[k] for k in ('source_id', 'locator', 'revision')} | {'excerpt': '尚未硬件验证'}]}
        result = self.call(cid, 'knowledge_finish', {'run_id': krun, 'outcome': 'reviewed',
                           'reviewed_source_ids': list({i['source_id'] for i in changes}), 'actions': [action]})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['created'], 1)
        lrun = self.call(cid, 'literature_plan')['runs'][0]['run_id']
        items = self.pages(cid, 'literature', lrun)
        source = next(i for i in items if i['kind'] == 'conversation')
        result = self.call(cid, 'literature_finish', {'run_id': lrun, 'outcome': 'reviewed', 'candidates': [
            {'locator': '10.1234/test', 'source_ids': [source['id']], 'evidence': {source['id']: 'https://doi.org/10.1234/test'}}]})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['counts']['created'], 1)
        self.assertTrue(self.finish(cid)['ok'])
        self.assertEqual(LiteratureCatalog(self.p['wiki_root']).list_items()['count'], 1)
        self.assertTrue((Path(self.p['wiki_root']) / 'knowledge/computation.md').exists())
        self.assertEqual(reports.load(reports.workspace(self.home), 'daily', self.day)['body'], report['body'])
        again = self.begin()
        self.assertEqual(again['capture'][0]['written'], 0)
        for stage in schedule.STAGES:
            result = self.call(again['cycle_id'], stage + '_plan')
            self.assertFalse(result.get('runs') or result.get('run_id'), result)
        self.assertTrue(self.finish(again['cycle_id'])['ok'])
        # Consumers must skip successfully checked conversations BEFORE the source limit.
        saved = knowledge.state(self.p)
        next_items, _ = knowledge.scan(self.p, saved['sources'])
        self.assertFalse(any(key.startswith('conversation:') for key in next_items))
        settings = reports.report_settings(self.home)
        provider = ReportSourceProvider()
        next_items, _ = provider.inventory(self.p, start_date=settings['start_date'],
                                           now=datetime.now(reports.CST), home=self.home)
        self.assertFalse(any(item['kind'] == 'conversation' for item in next_items))
        # An amendment to the same stable source id must become new work in both stages.
        db = Path(self.p['state_root']) / 'workbench/runtime.sqlite3'
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("UPDATE activities SET source_revision='fixture-amended', occurred_at=NULL")
        changed, _ = knowledge.scan(self.p, saved['sources'])
        self.assertTrue(any(key.startswith('conversation:') for key in changed))
        changed, _ = provider.inventory(self.p, start_date=settings['start_date'],
                                        now=datetime.now(reports.CST), home=self.home)
        self.assertTrue(any(item['kind'] == 'conversation' for item in changed))
        descriptor = next(item for item in changed if item['kind'] == 'conversation')
        observed = provider.read(self.p, descriptor, start_date=settings['start_date'],
                                 now=datetime.now(reports.CST), home=self.home)
        self.assertIsNone(observed['occurred_at'])
        self.assertEqual(observed['certainty'], 'observation')

    def test_frozen_report_and_knowledge_do_not_leak_after_revocation(self):
        cid = self.begin()['cycle_id']
        r = self.call(cid, 'report_plan')['runs'][0]['run_id']
        k = self.call(cid, 'knowledge_plan')['run_id']
        write_consent(self.p, {'allow_source_text': False})
        result = self.call(cid, 'report_sources', {'run_id': r})
        self.assertFalse(result['ok'])
        result = self.call(cid, 'knowledge_sources', {'run_id': k})
        self.assertFalse(result['ok'])
        items, gaps = knowledge.scan(self.p, {'conversation:old': {'checked_revision': 'old', 'text': 'old'}})
        self.assertNotIn('conversation:old', items)
        self.assertTrue(gaps)


if __name__ == '__main__':
    unittest.main()
