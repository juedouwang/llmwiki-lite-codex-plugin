"""Continuous notebook compatibility and report-comment snapshot regressions."""
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import research_notebook as nb  # noqa: E402
import research_reports as reports  # noqa: E402
from llmwiki_core import LLMWikiError  # noqa: E402
from llmwiki_registry import register_project  # noqa: E402
from run_reports_browser import _png_bytes  # noqa: E402


class ContinuousDocuments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='llmwiki-continuous-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        source = root / 'source'
        source.mkdir()
        self.home = str(root / 'home')
        self.p = register_project(str(source), home=self.home, wiki_root=str(root / 'wiki'))['project']
        self.nid = 'b' * 32

    def legacy(self):
        image = nb.upload(self.p, {'data': base64.b64encode(_png_bytes()).decode()})['image']
        blocks = [{'id': f'old{i}', 'type': kind, 'text': text, 'comments': ['批注\n完整保留  ', '第二条'], **extra}
                  for i, (kind, text, extra) in enumerate([
                      ('heading', '中文标题', {}), ('markdown', '**内容**\n多行  ', {}),
                      ('code', 'print("````")\n中文 = 1', {}), ('image', '图片说明', {'image': image}),
                      ('callout', '观察一\n观察二', {}), ('divider', '', {}), ('markdown', '', {})])]
        result = nb.save(self.p, self.nid, {'revision': '', 'document': {'title': '旧笔记', 'tags': ['实验'], 'blocks': blocks}})
        return result

    def test_read_and_noop_never_migrate_first_edit_keeps_original_bytes(self):
        first = self.legacy()
        path = nb._path(self.p, self.nid)
        raw = path.read_bytes()
        old = nb.load(self.p, self.nid)['document']
        current = nb.load(self.p, self.nid, continuous_view=True)
        doc = current['document']
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(len(doc['comments']), 14)
        self.assertEqual(doc['comments'][0]['text'], '批注\n完整保留  ')
        self.assertTrue(all(c['created_at'] is None for c in doc['comments']))
        self.assertEqual(doc['comments'], nb.continuous(old)['comments'])
        for needle in ['## 中文标题', '**内容**', '`````\n', '![截图](../assets/', '图片说明', '> 观察一\n> 观察二', '---']:
            self.assertIn(needle, doc['body'])
        self.assertNotIn('批注', doc['body'])
        noop = nb.save(self.p, self.nid, {'revision': first['revision'], 'document': doc})
        self.assertEqual(noop['revision'], first['revision'])
        self.assertEqual(raw, path.read_bytes())
        doc['body'] += '\n\n新观察'
        result = nb.save(self.p, self.nid, {'revision': first['revision'], 'document': doc})
        self.assertIn(b'llmwiki-notebook-v2:', path.read_bytes())
        snapshot = Path(self.p['wiki_root']) / '.notebook-history' / self.nid / (first['revision'] + '.snapshot')
        self.assertEqual(snapshot.read_bytes(), raw)
        loaded = nb.load(self.p, self.nid)['document']
        self.assertEqual(loaded['created_at'], old['created_at'])
        self.assertEqual(loaded['body'], doc['body'])
        self.assertEqual(loaded['comments'], doc['comments'])
        self.assertEqual(nb.history(self.p, self.nid, first['revision'])['document'], old)
        self.assertEqual(nb.history(self.p, self.nid, first['revision'], continuous_view=True)['document'], nb.continuous(old))
        with self.assertRaises(nb.NotebookConflict):
            nb.save(self.p, self.nid, {'revision': result['revision'], 'document': old})
        self.assertEqual(loaded, nb.load(self.p, self.nid)['document'])
        snapshot_raw = snapshot.read_bytes()
        nb.save(self.p, self.nid, {"revision": result["revision"], "restore_revision": first["revision"]})
        self.assertEqual(path.read_bytes(), snapshot_raw)
        self.assertEqual(nb.history(self.p, self.nid, result["revision"])["document"], loaded)
        with self.assertRaises(nb.NotebookConflict):
            nb.save(self.p, self.nid, {"revision": result["revision"], "restore_revision": first["revision"]})
        self.assertEqual(nb.load(self.p, self.nid)['document'], old)

    def test_continuous_notes_and_attachments_stay_in_their_project(self):
        other_source = Path(self.tmp.name) / 'other-source'
        other_source.mkdir()
        other = register_project(str(other_source), home=self.home, wiki_root=str(Path(self.tmp.name) / 'other-wiki'))['project']
        image = nb.upload(self.p, {'data': base64.b64encode(_png_bytes()).decode()})['image']
        first = {'format': 'markdown', 'title': '项目 A', 'body': f'A 正文 ![截图](../assets/{image})',
                 'comments': [{'id': 'c1', 'text': '仅 A 的批注', 'quote': 'A 正文', 'created_at': None}]}
        saved = nb.save(self.p, self.nid, {'revision': '', 'document': first})
        self.assertFalse(nb.load(other, self.nid, continuous_view=True)['exists'])
        nb.save(other, self.nid, {'revision': '', 'document': {'format': 'markdown', 'title': '项目 B', 'body': 'B 正文'}})
        self.assertEqual(nb.load(self.p, self.nid, continuous_view=True)['revision'], saved['revision'])
        self.assertEqual(nb.load(self.p, self.nid, continuous_view=True)['document']['comments'], first['comments'])
        self.assertEqual(nb.load(other, self.nid, continuous_view=True)['document']['body'], 'B 正文')
        self.assertTrue(nb.image_path(self.p, image).exists())
        self.assertFalse(nb.image_path(other, image).exists())

    def test_external_changes_readonly_and_cannot_overwrite(self):
        self.legacy()
        path = nb._path(self.p, self.nid)
        path.write_bytes(path.read_bytes().replace('图片说明'.encode(), '外部人工修改'.encode(), 1))
        raw = path.read_bytes()
        view = nb.load(self.p, self.nid, continuous_view=True)
        self.assertTrue(view['readonly'])
        self.assertIn('外部人工修改', view['document']['body'])
        self.assertNotIn('llmwiki-notebook-v1', view['document']['body'])
        with self.assertRaises(nb.NotebookConflict):
            nb.save(self.p, self.nid, {'document': view['document'], 'revision': view['revision']})
        self.assertEqual(raw, path.read_bytes())
        nb.save(self.p, 'c' * 32, {'revision': '', 'document': view['document']})
        self.assertIn('外部人工修改', nb.load(self.p, 'c' * 32)['document']['body'])

    def test_unicode_size_and_comments_are_not_silently_cut(self):
        with self.assertRaises(LLMWikiError):
            nb.validate({'format': 'markdown', 'body': '中' * (nb.MAX_DOCUMENT // 2), 'comments': []})
        with self.assertRaises(LLMWikiError):
            nb.validate_comments([{'id': 'same', 'text': 'one'}, {'id': 'same', 'text': 'two'}])

    def test_reader_has_body_and_explicit_comments_without_internal_marker(self):
        doc = {'format': 'markdown', 'title': '连续笔记', 'body': '**观察**\n\n```python\nx=1\n```',
               'comments': [{'id': 'comment1', 'quote': '观察', 'text': '待核对', 'created_at': None}]}
        nb.save(self.p, self.nid, {'revision': '', 'document': doc})
        raw = nb._path(self.p, self.nid).read_text(encoding='utf-8')
        text = nb.reader_markdown(raw, self.p['id'])
        self.assertIn('**观察**', text)
        self.assertIn('## 批注', text)
        self.assertNotIn('llmwiki-notebook-v2:', text)
        self.assertIn('> 观察', text)

    def test_report_comments_revisions_formal_candidate_restore(self):
        owner = reports.workspace(self.home)
        day = '2026-09-19'
        item = reports.create(owner, 'daily', day, [self.p['id']], home=self.home)
        def update(action, **kwargs):
            nonlocal item
            item = reports.update(owner, 'daily', day, {'action': action, 'expected_revision': item['revision'], **kwargs})
            return item
        update('save', body='已完成代码修改，未实测。')
        first = item['revision']
        comments = [{'id': 'comment1', 'quote': '未实测', 'text': '等待硬件', 'created_at': None}]
        update('save', body=item['body'], comments=comments)
        self.assertNotEqual(first, item['revision'])
        update('confirm')
        self.assertEqual(reports.load(owner, 'daily', day, version=1)['comments'], comments)
        update('start_edit')
        update('save', body='新增测试', comments=[])
        self.assertEqual(reports.load(owner, 'daily', day, version=1)['comments'], comments)
        update('restore', version=1)
        self.assertEqual(item['comments'], comments)
        rev = item['revision']
        reports.publish(owner, 'daily', day, '候选观察', generation_id='fixture', sources=[], fingerprint='changed',
                        project_ids=[self.p['id']], coverage_until='2026-09-19T18:00:00+08:00', gaps=[])
        candidate = reports.load(owner, 'daily', day, view='candidate')
        self.assertEqual(reports.load(owner, 'daily', day)['revision'], rev)
        update('adopt_candidate', expected_candidate_sha256=candidate['body_sha256'])
        self.assertEqual(item['comments'], comments)
        self.assertEqual(reports.load(owner, 'daily', day, view='previous')['comments'], comments)
        update('confirm')
        self.assertEqual(reports.load(owner, 'daily', day, version=2)['comments'], comments)
        # Comments are stored in version metadata, not spliced into research body.
        self.assertEqual(item['body'], '候选观察')
        self.assertTrue(json.dumps(item, ensure_ascii=False))

    def test_report_titles_are_versioned_conflict_checked_and_kept_on_adoption(self):
        owner = reports.workspace(self.home)
        day = '2026-09-19'
        item = reports.create(owner, 'daily', day, [self.p['id']], home=self.home)
        initial = item['revision']
        item = reports.update(owner, 'daily', day, {'action': 'save', 'expected_revision': initial,
                                                  'body': '正文不变', 'title': '人工科研标题'})
        self.assertNotEqual(item['revision'], initial)
        self.assertEqual(item['title'], '人工科研标题')
        self.assertEqual(reports.listing(owner, 'daily', home=self.home)['items'][0]['title'], '人工科研标题')
        original_revision = item['revision']
        item = reports.update(owner, 'daily', day, {'action': 'save', 'expected_revision': original_revision,
                                                  'body': item['body'], 'title': '只改标题'})
        self.assertNotEqual(item['revision'], original_revision)
        self.assertEqual(item['body'], '正文不变')
        with self.assertRaises(reports.ReportError):
            reports.update(owner, 'daily', day, {'action': 'save', 'expected_revision': original_revision,
                                               'body': '过时窗口', 'title': '不能覆盖'})
        item = reports.update(owner, 'daily', day, {'action': 'confirm', 'expected_revision': item['revision']})
        self.assertEqual(reports.load(owner, 'daily', day, version=1)['title'], '只改标题')
        item = reports.update(owner, 'daily', day, {'action': 'start_edit', 'expected_revision': item['revision']})
        item = reports.update(owner, 'daily', day, {'action': 'save', 'expected_revision': item['revision'],
                                                  'body': item['body'], 'title': '新的草稿标题'})
        reports.publish(owner, 'daily', day, '新候选正文', generation_id='test', sources=[], fingerprint='new',
                        project_ids=[self.p['id']], coverage_until='2026-09-19T18:00:00+08:00', gaps=[])
        candidate = reports.load(owner, 'daily', day, view='candidate')
        item = reports.update(owner, 'daily', day, {'action': 'adopt_candidate', 'expected_revision': item['revision'],
                                                  'expected_candidate_sha256': candidate['body_sha256']})
        self.assertEqual(item['title'], '新的草稿标题')
        self.assertEqual(reports.load(owner, 'daily', day, view='previous')['title'], '新的草稿标题')
        item = reports.update(owner, 'daily', day, {'action': 'restore', 'expected_revision': item['revision'], 'version': 1})
        self.assertEqual(item['title'], '只改标题')

    def test_invalid_report_title_never_changes_body_or_revision(self):
        owner = reports.workspace(self.home)
        item = reports.create(owner, 'weekly', '2026-09-14', [self.p['id']], home=self.home)
        for title in [None, 12, 'a' * 201, 'bad\x00title']:
            with self.assertRaises(reports.ReportError):
                reports.update(owner, 'weekly', '2026-09-14', {'action': 'save', 'expected_revision': item['revision'],
                                                              'title': title, 'body': '不能写入'})
            self.assertEqual(reports.load(owner, 'weekly', '2026-09-14')['revision'], item['revision'])
            self.assertEqual(reports.load(owner, 'weekly', '2026-09-14')['body'], '')

    def test_shared_editor_markup_has_autosave_retry_without_extra_controls(self):
        from document_editor import editor_markup
        for prefix, notebook in [('nb', True), ('report', False)]:
            html = editor_markup(prefix, '标题', '/records', notebook=notebook)
            for suffix in ['save', 'code', 'comment']:
                self.assertNotIn(f'id="{prefix}-{suffix}"', html)
            for suffix in ['retry', 'title', 'source', 'preview', 'comments']:
                self.assertIn(f'id="{prefix}-{suffix}"', html)
        self.assertIn('确认为正式版', editor_markup('report', '', '/reports'))

    def test_publish_return_revision_can_be_used_directly(self):
        owner = reports.workspace(self.home)
        result = reports.publish(owner, 'daily', '2026-09-19', '自动草稿', generation_id='fixture', sources=[], fingerprint='new',
                                 project_ids=[self.p['id']], coverage_until='2026-09-19T18:00:00+08:00', gaps=[])
        current = reports.load(owner, 'daily', '2026-09-19')
        self.assertEqual(result['revision'], current['revision'])


if __name__ == '__main__':
    unittest.main()
