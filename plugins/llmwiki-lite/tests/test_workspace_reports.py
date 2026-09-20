"""Workspace-wide reports: real isolated storage/HTTP, deterministic model fixtures."""
from datetime import datetime, timedelta, timezone
from html import unescape
import re
import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import research_reports as reports
from llmwiki_registry import register_project, list_projects
from research_records import write_record
from web_server import create_server


class WorkspaceReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='workspace-reports-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = str(root / 'home')
        self.projects = []
        for name in ('项目甲', '项目乙'):
            source = root / name
            source.mkdir()
            self.projects.append(register_project(str(source), name=name, home=self.home)['project'])
        self.ids = [p['id'] for p in self.projects]
        self.owner = reports.workspace(self.home)
        self.day = '2026-09-25'  # Friday, 18:00 China time.
        self.clock = datetime(2026, 9, 25, 10, tzinfo=timezone.utc)
        initial = reports.report_settings(self.home)
        reports.save_settings({'expected_revision': initial['revision'], 'enabled': True,
                              'project_ids': self.ids, 'daily_time': '18:00',
                              'weekly_weekday': 5, 'weekly_time': '18:00', 'start_date': self.day}, self.home)
        target = Path(self.home) / 'reports-settings.json'
        config = json.loads(target.read_text(encoding='utf-8'))
        # Fixture only: never asserted to be an official scheduled run.
        config['runtime'] = {'automation_id': 'fixture-only', 'target_thread_id': 'fixture-only', 'status': 'ACTIVE'}
        reports._write_json(target, config)

    def test_sunday_enablement_does_not_backfill_last_fridays_weekly(self):
        self.day = '2026-09-20'  # Enable Sunday, after the preceding Friday slot.
        self.clock = datetime(2026, 9, 20, 10, tzinfo=timezone.utc)
        current = reports.report_settings(self.home)
        reports.save_settings({'expected_revision': current['revision'], 'start_date': self.day}, self.home)
        self.records()
        first = reports.report_plan(self.home, now=self.clock)
        self.assertEqual([run['kind'] for run in first['runs']], ['daily'])
        self.finish(first['runs'][0], '# 多项目日报\n\n项目甲与项目乙完成代码修改，尚未完成实测。')
        second = reports.report_plan(self.home, now=self.clock)
        self.assertEqual(second['runs'], [])
        self.assertFalse(reports._state(self.owner, 'weekly-2026-09-14.json').exists())

    def records(self):
        for p in self.projects:
            write_record(p['source_root'], state_root=p['state_root'], title=p['name'] + '进展',
                         understanding=p['name'] + '完成代码修改；未完成硬件验证。', recorded_at=self.day + 'T17:00:00+08:00')

    def finish(self, run, body):
        items, cursor = [], None
        while True:
            page = reports.report_sources(run['run_id'], cursor, self.home, now=self.clock)
            items.extend(page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        source_ids = list(dict.fromkeys(i['id'] for i in items if i.get('role') == 'source'))
        result = reports.report_finish(run['run_id'], 'generated', body=body, source_ids=source_ids,
                                       source_summaries={sid: '隔离测试材料，代码变动不代表硬件验证。' for sid in source_ids},
                                       home=self.home, now=self.clock)
        return result, items

    def test_no_owner_configuration_and_no_read_before_six(self):
        config = reports.report_settings(self.home)
        self.assertIsNone(config['weekly_owner_project_id'])
        self.assertEqual(config['report_scope'], 'workspace')
        with patch.object(reports, 'collect_sources', side_effect=AssertionError('too early')):
            self.assertEqual(reports.report_plan(self.home, now=self.clock - timedelta(seconds=1))['runs'], [])
        self.assertNotIn(reports.WORKSPACE_ID, {p['id'] for p in list_projects(home=self.home)['projects']})

    def test_daily_is_one_document_for_two_projects_then_weekly_same_cycle(self):
        self.records()
        plan = reports.report_plan(self.home, now=self.clock)
        self.assertEqual(len(plan['runs']), 1)
        daily = plan['runs'][0]
        self.assertEqual(daily['kind'], 'daily')
        self.assertEqual(daily['project_ids'], self.ids)
        self.assertIsNone(daily['owner_project_id'])
        self.assertEqual(plan['pending_count'], 1)  # Friday's weekly waits for this daily.
        result, items = self.finish(daily, '# 综合日报\n\n## 项目甲\n代码修改完成，硬件未验证。\n\n## 项目乙\n代码修改完成，硬件未验证。')
        self.assertEqual(result['url'], '/reports/daily/' + self.day)
        self.assertEqual({i['project_id'] for i in items if i.get('role') == 'source'}, set(self.ids))
        self.assertEqual({i['project_name'] for i in items if i.get('role') == 'source'}, {'项目甲', '项目乙'})
        for p in self.projects:
            self.assertFalse(reports._state(p, 'daily-' + self.day + '.json').exists())
            self.assertFalse((Path(p['wiki_root']) / 'records/reports').exists())
        weekly = reports.report_plan(self.home, now=self.clock)['runs']
        self.assertEqual(len(weekly), 1)
        self.assertEqual(weekly[0]['kind'], 'weekly')
        self.assertEqual(weekly[0]['period_start'], '2026-09-21')
        _, items = self.finish(weekly[0], '# 刘亚宁周报（2026年09月21日—09月27日）\n\n## 本周工作\n\n### 一、项目甲\n完成代码修改。\n\n### 二、项目乙\n完成代码修改。\n\n## 下周计划\n开展硬件验证。\n\n## 需要协调与帮助\n暂无。')
        dailies = [i for i in items if i.get('kind') == 'daily_report']
        self.assertEqual(len(dailies), 1)  # Not duplicated once per participating project.
        self.assertEqual(dailies[0]['project_ids'], self.ids)
        self.assertEqual(reports.report_plan(self.home, now=self.clock)['runs'], [])

    def test_withdrawn_project_invalidates_frozen_global_run(self):
        self.records()
        run = reports.report_plan(self.home, now=self.clock)['runs'][0]
        settings = reports.report_settings(self.home)
        reports.save_settings({'expected_revision': settings['revision'], 'project_ids': self.ids[:1]}, self.home)
        with self.assertRaises(reports.ReportError):
            reports.report_sources(run['run_id'], home=self.home, now=self.clock)
        with self.assertRaises(reports.ReportError):
            self.finish(run, 'Cannot save withdrawn sources')

    def test_weekly_does_not_reuse_daily_containing_withdrawn_project(self):
        self.records()
        run = reports.report_plan(self.home, now=self.clock)['runs'][0]
        self.finish(run, '包含两个项目的原始综合日报。')
        items, gaps = reports._report_sources(self.owner, 'weekly', '2026-09-21', self.ids[:1], self.home, self.clock)
        self.assertFalse(any(i.get('kind') == 'daily_report' for i in items))
        self.assertEqual({i['project_id'] for i in items}, set(self.ids[:1]))

    def test_scope_change_preserves_human_text_and_scope_until_candidate_adopted(self):
        item = reports.create(self.owner, 'daily', self.day, self.ids, self.home)
        item = reports.update(self.owner, 'daily', self.day, {'action': 'save', 'body': '人工综合结论', 'expected_revision': item['revision']})
        reports.publish(self.owner, 'daily', self.day, '新范围草稿', generation_id='fixture', sources=[], fingerprint='new', project_ids=self.ids[:1], coverage_until=self.clock.isoformat(), gaps=[])
        current = reports.load(self.owner, 'daily', self.day)
        self.assertEqual(current['body'], '人工综合结论')
        self.assertEqual(current['metadata']['project_ids'], self.ids)
        candidate = reports.load(self.owner, 'daily', self.day, view='candidate')
        adopted = reports.update(self.owner, 'daily', self.day, {'action': 'adopt_candidate', 'expected_revision': current['revision'], 'expected_candidate_sha256': candidate['body_sha256']})
        self.assertEqual(adopted['metadata']['project_ids'], self.ids[:1])
        self.assertEqual(reports.load(self.owner, 'daily', self.day, view='previous')['body'], '人工综合结论')

    def test_new_registration_does_not_silently_expand_authorization(self):
        path = Path(self.tmp.name) / 'new-project'
        path.mkdir()
        new = register_project(str(path), home=self.home)['project']
        self.assertNotIn(new['id'], reports.report_settings(self.home)['project_ids'])

    def test_legacy_report_links_and_bodies_are_preserved(self):
        legacy = reports.create(self.projects[0], 'daily', self.day, home=self.home)
        fresh = reports.create(self.owner, 'daily', self.day, self.ids, self.home)
        self.assertEqual(reports.create(self.owner, 'daily', self.day, list(reversed(self.ids)), self.home)['url'], fresh['url'])
        self.assertNotEqual(fresh['url'], legacy['url'])
        self.assertEqual(reports.listing(self.owner, 'daily', home=self.home)['total'], 2)
        self.assertEqual(reports.listing(self.owner, 'daily', home=self.home, project_filter=self.ids[1])['total'], 1)

    def test_report_navigation_separates_context_filter_and_owner(self):
        from research_web_ui import layout
        before = list_projects(home=self.home)
        self.assertEqual(before['current_project_id'], self.ids[1])
        for i in range(31):
            day = (datetime(2026, 8, 1) + timedelta(days=i)).date().isoformat()
            reports.create(self.owner, 'daily', day, self.ids, self.home)
        params = {'context': [self.ids[0]], 'project': [self.ids[1]], 'status': ['draft'], 'q': ['日报']}
        page = reports.list_page(self.home, self.owner, 'daily', params)
        self.assertIn('title="项目甲"', page)
        self.assertIn(f'data-project="{reports.WORKSPACE_ID}"', page)
        self.assertIn(f'name="context" value="{self.ids[0]}"', page)
        self.assertIn(f'value="{self.ids[1]}" selected', page)
        for suffix in ('/todos', '/records', '/literature', '/code'):
            self.assertIn(f'href="/project/{self.ids[0]}{suffix}"', page)
        urls = [urlparse(unescape(href)) for href in re.findall(r'href="([^"]+)"', page)]
        report_links = [url for url in urls if url.path.startswith('/reports')]
        self.assertTrue(report_links)
        for url in report_links:
            query = parse_qs(url.query)
            self.assertIn(query['context'][0], self.ids)
            if '/daily/' in url.path:
                self.assertEqual(query['context'], [self.ids[0]])
                back = parse_qs(urlparse(query['return'][0]).query)
                self.assertEqual(back['project'], [self.ids[1]])
                self.assertEqual(back['context'], [self.ids[0]])
        weekly = next(url for url in report_links if parse_qs(url.query).get('view') == ['weekly'])
        self.assertEqual(parse_qs(weekly.query)['project'], [self.ids[1]])
        next_page = next(url for url in urls if parse_qs(url.query).get('offset') == ['30'])
        self.assertEqual(parse_qs(next_page.query)['context'], [self.ids[0]])
        shell = layout('科研记录', '', project_id=self.ids[0], active='records', home=self.home)
        self.assertIn(f'href="/reports?context={self.ids[0]}"', shell)
        self.assertEqual(list_projects(home=self.home), before)

    def test_report_editor_context_does_not_change_legacy_or_workspace_owner(self):
        before = list_projects(home=self.home)
        for owner in (self.projects[0], self.owner):
            reports.create(owner, 'daily', self.day, self.ids if reports.is_workspace(owner) else [owner['id']], self.home)
            original = reports.load(owner, 'daily', self.day)
            api = '/api/reports' if reports.is_workspace(owner) else f'/api/project/{owner["id"]}/reports'
            for context in self.ids:
                page = reports.editor_page(self.home, owner['id'], 'daily', self.day, {'context': [context]})
                self.assertIn(f'href="/project/{context}/records"', page)
                self.assertIn(f'data-api="{api}"', page)
                self.assertIn(f'data-project="{owner["id"]}"', page)
                self.assertEqual(reports.load(owner, 'daily', self.day), original)
        legacy = reports.editor_page(self.home, self.ids[0], 'daily', self.day)
        self.assertIn('title="项目甲"', legacy)
        self.assertEqual(list_projects(home=self.home), before)

    def test_invalid_context_does_not_fall_back_to_registry_default(self):
        before = list_projects(home=self.home)
        for value in ('deleted-project', ''):
            params = {'context': [value]}
            self.assertEqual(reports.navigation_context(self.home, params), '')
            page = reports.list_page(self.home, self.owner, 'daily', params)
            self.assertIn('data-context=""', page)
            self.assertIn('title="选择项目"', page)
            self.assertNotIn('class="is-current"', page)
            self.assertNotIn(f'href="/project/{self.ids[1]}/records"', page)
            self.assertIn('切换研究项目', page)
        self.assertEqual(list_projects(home=self.home), before)

    def test_workspace_http_navigation_storage_upload_and_csrf(self):
        from run_reports_browser import _png_bytes
        server = create_server(self.home, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = f'http://127.0.0.1:{server.server_port}'
        def call(path, payload=None):
            req = Request(origin + path, data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Content-Type': 'application/json', 'X-Notebook-Request': '1', 'Origin': origin})
            with urlopen(req) as response:
                return json.load(response)
        with urlopen(origin + '/reports') as response:
            self.assertIn('日报与周报', response.read().decode())
        with urlopen(origin + '/project/' + self.ids[0] + '/records?view=daily') as response:
            self.assertEqual(response.url, origin + '/reports?view=daily&context=' + self.ids[0])
        for query in ('context=', 'context=deleted-project'):
            with urlopen(origin + '/reports?' + query) as response:
                page = response.read().decode()
                self.assertIn('data-context=""', page)
                self.assertNotIn('class="is-current"', page)
        item = call('/api/reports', {'action': 'create', 'kind': 'daily', 'period_start': self.day, 'project_ids': self.ids})
        self.assertIsNone(item['metadata']['owner_project_id'])
        api = '/api/reports/daily/' + self.day
        upload = call('/api/reports/upload', {'data': base64.b64encode(_png_bytes()).decode()})
        body = '## 综合日报\n\n![截图](../../assets/' + upload['image'] + ')'
        html = call('/api/reports/preview', {'kind': 'daily', 'period_start': self.day, 'body': body})['html']
        self.assertIn('/reports/asset/records/assets/' + upload['image'], html)
        call(api, {'action': 'save', 'expected_revision': item['revision'], 'body': body})
        with urlopen(origin + '/reports/asset/records/assets/' + upload['image']) as response:
            self.assertEqual(response.read(), _png_bytes())
        with self.assertRaises(HTTPError):
            call('/reports/asset/records/assets/../../../reports-settings.json')
        with self.assertRaises(HTTPError):
            urlopen(Request(origin + '/api/reports/upload', data=b'{}', headers={'Content-Type': 'application/json', 'Origin': 'https://untrusted.invalid'}))
        with urlopen(origin + item['url']) as response:
            self.assertIn('data-api="/api/reports"', response.read().decode())
        self.assertEqual(call(api)['body'], body)
        settings = reports.settings_section(self.home)
        self.assertNotIn('id="report-owner"', settings)
        for project in self.projects:
            self.assertFalse((Path(project['wiki_root']) / 'records/assets').exists())


if __name__ == '__main__':
    unittest.main()
