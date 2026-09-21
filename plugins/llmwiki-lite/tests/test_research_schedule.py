"""Shared workflow tests. Official receipts here are temporary fixtures, not live runs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import research_schedule as schedule
from research_reports import report_settings, save_settings, ReportError
from llmwiki_registry import register_project


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='schedule-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / 'source'
        source.mkdir()
        self.home = str(self.root / 'home')
        self.host = self.root / 'host'
        self.p = register_project(str(source), home=self.home)['project']
        self.clock = datetime.now(timezone.utc)
        settings = report_settings(self.home)
        save_settings({'expected_revision': settings['revision'], 'enabled': True,
                       'project_ids': [self.p['id']], 'daily_time': '00:00', 'weekly_weekday': 7,
                       'weekly_time': '23:00', 'weekly_owner_project_id': self.p['id'],
                       'knowledge_enabled': True, 'literature_enabled': True}, self.home)
        self.receipt = self.host / 'automations' / 'test-task' / 'automation.toml'
        self.receipt.parent.mkdir(parents=True)
        self.official()
        schedule.bind_runtime('test-task', 'thread-test', self.home, host_home=self.host, now=self.clock)

    def official(self, status='ACTIVE', prompt=None):
        prompt = prompt if prompt is not None else schedule.automation_prompt(self.home)
        self.receipt.write_text('id = "test-task"\nkind = "heartbeat"\ntarget_thread_id = "thread-test"\nstatus = ' + json.dumps(status) + '\nprompt = ' + json.dumps(prompt, ensure_ascii=False) + '\n', encoding='utf-8')

    def begin(self):
        return schedule.schedule_begin(self.home, host_home=self.host, now=self.clock)

    def call(self, cid, name, args=None):
        return schedule.schedule_call(cid, 'llmwiki_' + name, args, self.home, now=self.clock)

    def finish(self, cid):
        return schedule.schedule_finish(cid, self.home, now=self.clock)

    def test_binding_sets_all_stage_receipts_and_rejects_fake_paused(self):
        runtime = report_settings(self.home)['runtime']
        self.assertTrue(runtime['knowledge_bound_at'])
        self.assertTrue(runtime['literature_bound_at'])
        with self.assertRaises(ReportError):
            schedule.bind_runtime('missing', 'thread-test', self.home, host_home=self.host)
        self.official('PAUSED')
        with self.assertRaises(ReportError):
            schedule.bind_runtime('test-task', 'thread-test', self.home, host_home=self.host)
        self.assertFalse(self.begin()['ok'])
        self.official(prompt='Different configuration')
        self.assertFalse(self.begin()['ok'])

    def test_new_project_is_selected_without_rebinding_shared_plan(self):
        before = report_settings(self.home)
        receipt = self.receipt.read_bytes()
        source = self.root / 'new-project'
        source.mkdir()
        added = register_project(str(source), home=self.home)['project']
        selected = [self.p['id'], added['id']]
        saved = report_settings(self.home)
        self.assertEqual(saved['project_ids'], selected)
        self.assertEqual(saved['runtime'], before['runtime'])
        self.assertEqual(saved['connection'], 'configured')
        self.assertEqual(self.receipt.read_bytes(), receipt)
        with patch('research_capture_runtime.capture_project', return_value={'written': 0}) as capture:
            started = self.begin()
        self.assertTrue(started['cycle_id'])
        self.assertCountEqual([call.args[0]['id'] for call in capture.call_args_list], selected)
        self.assertEqual(started['budgets']['knowledge_runs'], 2)
        cycle = schedule._json(schedule._cycle_path(self.home, started['cycle_id']))
        self.assertEqual(cycle['project_ids'], selected)
        self.assertEqual(self.receipt.read_bytes(), receipt)

    def test_nonoverlap_expiry_and_no_source_read_when_paused(self):
        first = self.begin()['cycle_id']
        self.assertIsNotNone(first)
        self.assertIsNone(self.begin()['cycle_id'])
        self.clock += timedelta(hours=3)
        self.assertNotEqual(self.begin()['cycle_id'], first)
        config = report_settings(self.home)
        save_settings({'expected_revision': config['revision'], 'enabled': False}, self.home)
        with patch('research_capture_runtime.capture_project', side_effect=AssertionError('read')):
            self.assertIsNone(self.begin()['cycle_id'])

    def test_all_three_empty_stages_and_finish_idempotence(self):
        cid = self.begin()['cycle_id']
        with patch('mcp_server.dispatch', return_value={'ok': True, 'runs': []}):
            for stage in schedule.STAGES:
                self.call(cid, stage + '_plan')
        result = self.finish(cid)
        self.assertTrue(result['ok'])
        self.assertEqual(self.finish(cid), result)
        self.assertEqual(report_settings(self.home)['runtime']['last_success_at'], self.clock.isoformat())

    def test_stage_failure_does_not_block_later_stages_or_fake_success(self):
        cid = self.begin()['cycle_id']
        with patch('mcp_server.dispatch', side_effect=[ValueError('private material'), {'ok': True, 'run_id': None}, {'ok': True, 'runs': []}]):
            failure = self.call(cid, 'report_plan')
            self.assertNotIn('private material', json.dumps(failure))
            self.assertTrue(self.call(cid, 'knowledge_plan')['ok'])
            self.assertTrue(self.call(cid, 'literature_plan')['ok'])
        result = self.finish(cid)
        self.assertFalse(result['ok'])
        self.assertEqual(result['stages']['report']['status'], 'failed')
        self.assertEqual(result['stages']['knowledge']['status'], 'ok')
        self.assertNotIn('last_success_at', report_settings(self.home)['runtime'])

    def test_receipts_track_known_runs_and_failed_results(self):
        cid = self.begin()['cycle_id']
        with patch('mcp_server.dispatch', return_value={'ok': True, 'runs': [{'run_id': 'run1'}]}):
            self.call(cid, 'report_plan')
        with self.assertRaises(ReportError):
            self.call(cid, 'report_finish', {'run_id': 'other', 'outcome': 'generated'})
        with patch('mcp_server.dispatch', return_value={'ok': False, 'error': {'code': 'REAL_FAILURE'}}):
            self.call(cid, 'report_finish', {'run_id': 'run1', 'outcome': 'generated'})
        result = self.finish(cid)
        self.assertFalse(result['ok'])
        self.assertEqual(result['stages']['report']['unfinished_runs'], 1)
        self.assertIn('REAL_FAILURE', result['stages']['report']['errors'])

    def test_order_scope_and_expired_calls(self):
        cid = self.begin()['cycle_id']
        for name, args in [('knowledge_plan', {}), ('report_plan', {'home': 'outside'}), ('report_plan', [])]:
            with self.assertRaises(ReportError):
                self.call(cid, name, args)
        self.clock += timedelta(hours=3)
        with self.assertRaises(ReportError):
            self.call(cid, 'report_plan')
        self.assertFalse(self.finish(cid)['ok'])

    def test_configuration_thread_is_not_excluded_from_capture(self):
        with patch('research_capture_runtime.capture_project', return_value={
                'project_id': self.p['id'], 'written': 1, 'gaps': []}) as capture:
            result = self.begin()
        self.assertTrue(result['ok'])
        self.assertEqual(result['capture'][0]['written'], 1)
        capture.assert_called_once()
        self.assertEqual(capture.call_args.args[0]['id'], self.p['id'])
        self.assertNotIn('excluded_sessions', capture.call_args.kwargs)

    def test_capture_failure_is_a_gap_not_a_blocker(self):
        with patch('research_capture_runtime.capture_project', side_effect=Exception('private')):
            result = self.begin()
        self.assertTrue(result['ok'])
        self.assertTrue(result['capture'][0]['gaps'])
        self.assertNotIn('private', json.dumps(result))

    def test_slow_stage_does_not_hold_settings_lock(self):
        cid = self.begin()['cycle_id']
        entered, release = Event(), Event()
        def delayed(*_):
            entered.set()
            if not release.wait(10):
                raise AssertionError('foreground could not release the stage')
            return {'ok': True, 'runs': []}
        with patch('mcp_server.dispatch', side_effect=delayed), ThreadPoolExecutor(max_workers=2) as pool:
            worker = pool.submit(self.call, cid, 'report_plan')
            self.assertTrue(entered.wait(5))
            try:
                config = report_settings(self.home)
                pause = pool.submit(save_settings, {'expected_revision': config['revision'], 'enabled': False}, self.home)
                pause.result(timeout=3)
                self.assertFalse(report_settings(self.home)['enabled'])
            finally:
                release.set()
            worker.result(timeout=5)
        with self.assertRaises(ReportError):
            self.call(cid, 'knowledge_plan')

    def test_multi_pass_budgets_cover_all_selected_projects(self):
        ids = [self.p['id']]
        for index in range(4):
            source = self.root / f'project-{index}'
            source.mkdir()
            ids.append(register_project(str(source), home=self.home)['project']['id'])
        config = report_settings(self.home)
        save_settings({'expected_revision': config['revision'], 'project_ids': ids}, self.home)
        started = self.begin()
        self.assertEqual(started['budgets'], {'report_runs': 3, 'knowledge_runs': 5, 'literature_runs': 5})
        cid = started['cycle_id']
        counts = dict.fromkeys(schedule.STAGES, 0)
        batch_sizes = []

        def dispatch(name, args):
            stage, verb = name.split('_')[1:]
            if verb == 'finish':
                return {'ok': True}
            if stage == 'knowledge':
                self.assertEqual(args['trigger'], 'scheduled')
                counts[stage] += 1
                return {'ok': True, 'run_id': f'{stage}-{counts[stage]}'}
            size = args['max_projects' if stage == 'literature' else 'max_reports']
            batch_sizes.append((stage, size))
            runs = [{'run_id': f'{stage}-{i}'} for i in range(counts[stage], counts[stage] + size)]
            counts[stage] += size
            return {'ok': True, 'runs': runs}

        with patch('mcp_server.dispatch', side_effect=dispatch) as mocked:
            # Daily and weekly may be planned in successive calls of the same cycle.
            for stage, batches in [('report', [1, 1, 1]), ('knowledge', [1] * 5), ('literature', [3, 3])]:
                for size in batches:
                    args = {'max_reports': size} if stage == 'report' else ({'max_projects': size} if stage == 'literature' else {})
                    planned = self.call(cid, stage + '_plan', args)
                    for run in planned.get('runs', [planned] if planned.get('run_id') else []):
                        self.call(cid, stage + '_finish', {'run_id': run['run_id'], 'outcome': 'reviewed'})
                before = mocked.call_count
                self.assertEqual(self.call(cid, stage + '_plan')['reason'], 'cycle_budget_reached')
                self.assertEqual(mocked.call_count, before)
        self.assertEqual(counts, {'report': 3, 'knowledge': 5, 'literature': 5})
        self.assertEqual(batch_sizes[-2:], [('literature', 3), ('literature', 2)])
        self.assertTrue(self.finish(cid)['ok'])

    def test_cli_status_is_one_shot_json(self):
        cli = Path(schedule.__file__).with_name('research_cycle.py')
        result = subprocess.run([sys.executable, '-I', '-B', str(cli), '--home', self.home, 'status'], capture_output=True, text=True, encoding='utf-8', timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['connection'], 'configured')


if __name__ == '__main__':
    unittest.main()
