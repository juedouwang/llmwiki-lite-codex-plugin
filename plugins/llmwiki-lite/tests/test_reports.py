"""Report storage/HTTP regressions, isolated from all real research data."""
import hashlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import research_reports as reports  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from research_records import list_records, write_record  # noqa: E402
from web_server import create_server  # noqa: E402


class ReportFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='llmwiki-report-test-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.home = str(root / 'home')
        self.projects = []
        for name in ('alpha', 'beta'):
            source = root / name
            source.mkdir()
            self.projects.append(register_project(str(source), name=name, home=self.home)['project'])
        self.p = self.projects[0]
        self.kind, self.day = 'daily', '2026-09-19'

    def create(self):
        return reports.create(self.p, self.kind, self.day, home=self.home)

    def update(self, item, action, **kw):
        return reports.update(self.p, self.kind, self.day, {'action': action, 'expected_revision': item['revision'], **kw})

    def publish(self, text='自动草稿', **kw):
        return reports.publish(self.p, self.kind, self.day, text, generation_id='fixture-run', sources=[], fingerprint='fixture-input', project_ids=[self.p['id']], coverage_until='2026-09-19T12:00:00+00:00', gaps=[], **kw)


class ReportTests(ReportFixture):
    def test_identity_and_idempotent_create(self):
        a = self.create()
        self.assertEqual(a, self.create())
        self.assertEqual(reports.listing(self.p, 'daily', home=self.home)['total'], 1)
        for kind, day in [('x', self.day), ('weekly', self.day), ('daily', '../x'), ('daily', '2026-02-31')]:
            with self.assertRaises(reports.ReportError):
                reports.create(self.p, kind, day, home=self.home)
        with self.assertRaises(reports.ReportError):
            reports.project_for('', self.home)

    def test_confirm_and_revision_history(self):
        item = self.update(self.create(), 'save', body='第一版 **结论**')
        formal = self.update(item, 'confirm')
        self.assertEqual(formal['mode'], 'formal')
        with self.assertRaises(reports.ReportError):
            self.update(item, 'confirm')
        edit = self.update(formal, 'start_edit')
        edit = self.update(edit, 'save', body='修订结论')
        self.assertEqual(reports.load(self.p, self.kind, self.day, version=1)['body'], '第一版 **结论**')
        final = self.update(edit, 'confirm')
        self.assertEqual(len(final['metadata']['versions']), 2)
        self.assertEqual(reports.load(self.p, self.kind, self.day, version=2)['body'], '修订结论')

    def test_empty_and_pending_images_cannot_confirm(self):
        item = self.create()
        with self.assertRaises(reports.ReportError):
            self.update(item, 'confirm')
        item = self.update(item, 'save', body='文字 <!--report-upload:pending-->')
        with self.assertRaises(reports.ReportError):
            self.update(item, 'confirm')

    def test_human_clear_candidate_revision_isolation_and_adopt(self):
        self.publish()
        item = reports.load(self.p, self.kind, self.day)
        item = self.update(item, 'save', body='')
        self.publish('新的总结')
        current = reports.load(self.p, self.kind, self.day)
        self.assertEqual(item['revision'], current['revision'])
        self.assertEqual(current['body'], '')
        candidate = reports.load(self.p, self.kind, self.day, view='candidate')
        with self.assertRaises(reports.ReportError):
            self.update(current, 'adopt_candidate', expected_candidate_sha256='stale')
        adopted = self.update(current, 'adopt_candidate', expected_candidate_sha256=candidate['body_sha256'])
        self.assertTrue(adopted['metadata']['draft']['human_edited'])
        self.assertFalse(adopted['has_candidate'])
        self.assertEqual(reports.load(self.p, self.kind, self.day, view='previous')['body'], '')
        self.assertEqual(adopted['metadata']['versions'], [])

    def test_external_edit_protects_and_causes_conflict(self):
        self.publish()
        item = reports.load(self.p, self.kind, self.day)
        path = Path(self.p['wiki_root']) / f'records/reports/daily-{self.day}/draft.md'
        path.write_text('外部修改', encoding='utf-8')
        with self.assertRaises(reports.ReportError):
            self.update(item, 'save', body='错误覆盖')
        self.assertEqual(self.publish('新机器稿')['target'], 'candidate')
        self.assertEqual(reports.load(self.p, self.kind, self.day)['body'], '外部修改')

    def test_generated_draft_updates_and_formal_is_immutable(self):
        self.publish('第一轮')
        self.assertEqual(self.publish('第二轮')['target'], 'draft')
        item = reports.load(self.p, self.kind, self.day)
        self.assertFalse(item['metadata']['draft']['human_edited'])
        formal = self.update(item, 'confirm')
        self.assertEqual(self.publish('第三轮')['target'], 'candidate')
        self.assertEqual(reports.load(self.p, self.kind, self.day)['body'], '第二轮')
        self.assertEqual(reports.load(self.p, self.kind, self.day)['revision'], formal['revision'])

    def test_weekly_shared_link_and_no_body_list_read(self):
        ids = [p['id'] for p in self.projects]
        report = reports.create(self.p, 'weekly', '2026-09-14', ids, self.home)
        with patch.object(reports, '_read_body', side_effect=AssertionError('list read body')):
            listing = reports.listing(self.projects[1], 'weekly', home=self.home)
        self.assertEqual(listing['items'][0]['link'], report['url'])
        self.assertNotIn('body', listing['items'][0])
        self.assertFalse((Path(self.projects[1]['wiki_root']) / 'records/reports').exists())

    def test_old_records_exclude_reports_and_remain_unchanged(self):
        record = write_record(self.p['source_root'], state_root=self.p['state_root'], title='旧记录', understanding='真实记录内容', recorded_at='2026-09-19T09:00:00+08:00')
        old = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(self.p['wiki_root']).rglob('*.md')}
        self.update(self.create(), 'save', body='# 新日报')
        self.assertEqual(list_records(self.p['source_root'], state_root=self.p['state_root'])['count'], 1)
        self.assertTrue(record['ok'])
        for p, digest in old.items():
            self.assertEqual(hashlib.sha256(Path(p).read_bytes()).hexdigest(), digest)

    def test_redo_journal_recovers_interrupted_confirm(self):
        item = self.update(self.create(), 'save', body='待确认')
        original = reports._atomic
        def interrupted(path, data):
            if path.name == 'v0001.md':
                raise OSError('simulated interruption')
            return original(path, data)
        with patch.object(reports, '_atomic', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.update(item, 'confirm')
        restored = reports.load(self.p, self.kind, self.day)
        self.assertEqual(restored['mode'], 'formal')
        self.assertEqual(restored['body'], '待确认')
        self.assertEqual(len(restored['metadata']['versions']), 1)

    def test_http_routes_csrf_preview_and_conflict(self):
        server = create_server(self.home, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))
        origin = f'http://127.0.0.1:{server.server_port}'
        api = f'/api/project/{self.p["id"]}/reports'
        def call(path, data=None, origin_header=True):
            headers = {'Content-Type': 'application/json', 'X-Notebook-Request': '1'}
            if origin_header:
                headers['Origin'] = origin
            req = Request(origin + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
            with urlopen(req) as res:
                return json.loads(res.read())
        with self.assertRaises(HTTPError) as cm:
            call(api, {'action': 'create', 'kind': self.kind, 'period_start': self.day}, False)
        self.assertEqual(cm.exception.code, 403)
        item = call(api, {'action': 'create', 'kind': self.kind, 'period_start': self.day})
        preview = call(api + '/preview', {'kind': self.kind, 'period_start': self.day, 'body': '### 标题\n\n**结论**\n\n<script>alert(1)</script>\n\n![远程](https://example.invalid/x.png)'})['html']
        self.assertIn('<h3', preview)
        self.assertIn('<strong>', preview)
        self.assertNotIn('<script>', preview)
        self.assertNotIn('<img', preview)
        path = api + '/' + self.kind + '/' + self.day
        call(path, {'action': 'save', 'expected_revision': item['revision'], 'body': '先保存'})
        with self.assertRaises(HTTPError) as cm:
            call(path, {'action': 'save', 'expected_revision': item['revision'], 'body': '旧版本'})
        self.assertEqual(cm.exception.code, 409)
        with urlopen(origin + item['url']) as res:
            self.assertIn('report-source', res.read().decode())
        with self.assertRaises(HTTPError) as cm:
            call(api + '/preview', {'kind': self.kind, 'period_start': self.day, 'body': '中' * (2*1024*1024)})
        self.assertEqual(cm.exception.code, 413)


class ReportGenerationTests(ReportFixture):
    def setUp(self):
        super().setUp()
        self.owner = reports.workspace(self.home)

    def create(self):
        return reports.create(self.owner, self.kind, self.day, [self.p['id']], self.home)

    def update(self, item, action, **kw):
        return reports.update(self.owner, self.kind, self.day,
            {'action': action, 'expected_revision': item['revision'], **kw}, home=self.home)

    # Runtime below is a fixture, NOT real scheduling evidence.
    def configured(self):
        current = reports.report_settings(self.home)
        config = reports.save_settings({'expected_revision': current['revision'], 'enabled': True,
            'project_ids': [self.p['id']], 'daily_time': '20:00', 'weekly_weekday': 7,
            'weekly_time': '21:00', 'weekly_owner_project_id': self.p['id'],
            'start_date': self.day}, self.home)
        from llmwiki_registry import llmwiki_home
        target = llmwiki_home(self.home) / 'reports-settings.json'
        raw = json.loads(target.read_text(encoding='utf-8'))
        raw['runtime'] = {'automation_id': 'fixture-only', 'target_thread_id': 'fixture-only'}
        target.write_text(json.dumps(raw), encoding='utf-8')
        self.now = datetime(2026, 9, 19, 13, tzinfo=timezone.utc)
        return config

    def record(self, title='实验进展', understanding='代码修改完成，上板尚未验证。'):
        return write_record(self.p['source_root'], state_root=self.p['state_root'], title=title,
            understanding=understanding, recorded_at='2026-09-19T19:00:00+08:00')

    def next_run(self):
        return reports.report_plan(self.home, now=self.now)['runs'][0]

    def evidence(self, run):
        items, cursor = [], None
        while True:
            page = reports.report_sources(run['run_id'], cursor, self.home, now=self.now)
            self.assertLessEqual(sum(len(i['text']) for i in page['items']), 20000)
            items.extend(page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        ids = list(dict.fromkeys(i['id'] for i in items if i['role'] == 'source'))
        return ids, {i: '来源记录了代码修改；硬件结果待验证。' for i in ids}

    def finish(self, run, text='已完成代码修改；硬件验证尚未进行。'):
        ids, summaries = self.evidence(run)
        return reports.report_finish(run['run_id'], 'generated', body=text,
            source_ids=ids, source_summaries=summaries, home=self.home, now=self.now)

    def test_disabled_and_disconnected_do_not_read_sources(self):
        with patch.object(reports, 'collect_sources', side_effect=AssertionError('unauthorized read')):
            self.assertEqual(reports.report_plan(self.home)['runs'], [])
        settings = reports.report_settings(self.home)
        reports.save_settings({'expected_revision': settings['revision'], 'enabled': True,
            'project_ids': [self.p['id']], 'daily_time': '20:00', 'weekly_time': '21:00',
            'weekly_weekday': 7, 'weekly_owner_project_id': self.p['id']}, self.home)
        with patch.object(reports, 'collect_sources', side_effect=AssertionError('disconnected read')):
            self.assertEqual(reports.report_plan(self.home)['runs'], [])

    def test_plan_source_finish_idempotent_and_incremental(self):
        self.configured()
        self.record()
        run = self.next_run()
        with self.assertRaises(reports.ReportError):
            reports.report_finish(run['run_id'], 'generated', body='错误', source_ids=['unknown'], source_summaries={}, home=self.home, now=self.now)
        first = self.finish(run)
        self.assertEqual(first['target'], 'draft')
        self.assertEqual(self.finish(run), first)
        self.assertEqual(reports.report_plan(self.home, now=self.now)['runs'], [])
        item = reports.load(self.owner, self.kind, self.day)
        item = self.update(item, 'save', body='用户结论')
        self.record('新增记录', '第二次实验仍待上板。')
        second = self.next_run()
        self.assertEqual(self.finish(second, '第二轮机器总结')['target'], 'candidate')
        self.assertEqual(reports.load(self.owner, self.kind, self.day)['body'], '用户结论')

    def test_pause_expiry_and_pagination_enforced(self):
        self.configured()
        self.record(understanding='科研材料。' * 5000)
        run = self.next_run()
        page = reports.report_sources(run['run_id'], home=self.home, now=self.now)
        self.assertIsNotNone(page['next_cursor'])
        with self.assertRaisesRegex(reports.ReportError, '所有来源分页'):
            reports.report_finish(run['run_id'], 'no_evidence', home=self.home, now=self.now)
        self.evidence(run)
        with self.assertRaisesRegex(reports.ReportError, '过期'):
            reports.report_finish(run['run_id'], 'no_evidence', home=self.home, now=self.now + timedelta(minutes=11))
        settings = reports.report_settings(self.home)
        reports.save_settings({'expected_revision': settings['revision'], 'enabled': False}, self.home)
        with self.assertRaisesRegex(reports.ReportError, '暂停'):
            self.finish(run)

    def test_settings_runtime_protected_and_revision_stable(self):
        config = self.configured()
        self.assertEqual(config['revision'], reports.report_settings(self.home)['revision'])
        with self.assertRaises(reports.ReportError):
            reports.save_settings({'expected_revision': config['revision'], 'runtime': {'automation_id': 'forged'}}, self.home)
        with self.assertRaises(reports.ReportError):
            reports.save_settings({'expected_revision': config['revision'], 'daily_time': '25:00'}, self.home)
        item = self.create()
        reports.update(self.owner, self.kind, self.day, {'action': 'regenerate', 'expected_revision': item['revision']}, home=self.home)
        self.assertEqual(item['revision'], reports.load(self.owner, self.kind, self.day)['revision'])

    def test_retry_cap_and_no_evidence_do_not_make_empty_reports(self):
        self.configured()
        self.assertEqual(reports.report_plan(self.home, now=self.now)['runs'], [])
        empty = reports.load(self.owner, self.kind, self.day)
        self.assertIsNone(empty['metadata']['draft'])
        self.record()
        for _ in range(3):
            run = self.next_run()
            reports.report_finish(run['run_id'], 'failed', error_code='TEST_FAILURE', home=self.home, now=self.now)
            self.assertEqual(reports.report_plan(self.home, now=self.now)['runs'], [])
            self.now += timedelta(minutes=31)
        self.assertEqual(reports.report_plan(self.home, now=self.now)['runs'], [])

    def test_local_progress_link_uses_original_record_without_task_changes(self):
        import research_progress as progress
        self.configured()
        self.record()
        old = progress.load_summary(self.p)
        created = progress.update(self.p, {'action': 'create', 'revision': old['revision'],
            'task': {'title': '硬件验证', 'status': 'active', 'start': '', 'end': '',
                     'checkpoint': '', 'next_step': '', 'record_id': ''}})
        task = created['tasks'][0]
        tasks_path = Path(self.p['wiki_root']) / '.research-progress/tasks.json'
        before = tasks_path.read_bytes()
        run = self.next_run()
        page = reports.report_sources(run['run_id'], home=self.home, now=self.now)
        source = next(i for i in page['items'] if i['kind'] == 'research_record')
        self.finish(run)
        progress.write_context(self.p, {'task_id': task['id'], 'checkpoint': '代码已改，上板未验证',
            'next_step': '准备测试环境并进行上板验证', 'source_record_id': source['locator'], 'base_revision': ''})
        summary = progress.load_summary(self.p)
        self.assertEqual(summary['tasks'][0]['effective_context']['checkpoint'], '代码已改，上板未验证')
        self.assertEqual(tasks_path.read_bytes(), before)

    def test_finish_recovers_receipt_after_run_file_write_failure(self):
        self.configured()
        self.record()
        run = self.next_run()
        ids, summaries = self.evidence(run)
        original = reports._write_json
        def fail_run_result(path, value):
            if path.parent.name == 'runs' and value.get('result') is not None:
                raise OSError('simulated crash after report commit')
            original(path, value)
        with patch.object(reports, '_write_json', side_effect=fail_run_result):
            with self.assertRaises(OSError):
                reports.report_finish(run['run_id'], 'generated', body='代码已改；硬件待验证。',
                    source_ids=ids, source_summaries=summaries, home=self.home, now=self.now)
        result = reports.report_finish(run['run_id'], 'generated', body='代码已改；硬件待验证。',
            source_ids=ids, source_summaries=summaries, home=self.home, now=self.now)
        self.assertEqual(result['target'], 'draft')
        self.assertIsNone(reports.load(self.owner, self.kind, self.day)['metadata']['candidate'])
        with self.assertRaises(reports.ReportError):
            reports.report_finish(run['run_id'], 'generated', body='不同结果',
                source_ids=ids, source_summaries=summaries, home=self.home, now=self.now)

    def test_weekly_formal_priority_template_and_stable_input(self):
        self.configured()
        self.record()
        self.finish(self.next_run())
        item = reports.load(self.owner, self.kind, self.day)
        formal = self.update(item, 'confirm')
        draft = self.update(formal, 'start_edit')
        self.update(draft, 'save', body='尚未确认的修订内容')
        self.now = datetime(2026, 9, 20, 14, tzinfo=timezone.utc)
        runs = reports.report_plan(self.home, now=self.now)['runs']
        weekly = next(r for r in runs if r['kind'] == 'weekly')
        page = reports.report_sources(weekly['run_id'], home=self.home, now=self.now)
        daily = next(i for i in page['items'] if i.get('kind') == 'daily_report')
        self.assertNotIn('尚未确认的修订内容', daily['text'])
        self.assertTrue(daily['locator'].endswith(':v1'))
        template = Path(__file__).resolve().parents[1] / 'templates/weekly-report.md'
        original = Path(__file__).resolve().parents[3] / 'docs/liu-yaning-weekly-report-template.md'
        self.assertEqual(template.read_bytes(), original.read_bytes())
        ids, summaries = self.evidence(weekly)
        with self.assertRaises(reports.ReportError):
            reports.report_finish(weekly['run_id'], 'generated', body='不符合周报模板', source_ids=ids,
                source_summaries=summaries, home=self.home, now=self.now)
        text = '# 刘亚宁周报（2026年09月14日—09月20日）\n\n## 本周工作\n\n完成代码修改，硬件尚未验证。\n\n## 下周计划\n\n开展硬件验证。\n\n## 需要协调与帮助\n\n暂无。'
        reports.report_finish(weekly['run_id'], 'generated', body=text, source_ids=ids,
            source_summaries=summaries, home=self.home, now=self.now)
        self.assertEqual(reports.report_plan(self.home, now=self.now)['runs'], [])
        self.assertEqual(reports.identity('weekly', '2025-12-29')[1], '2026-01-04')


if __name__ == '__main__':
    unittest.main()
