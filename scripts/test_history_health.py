import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import history_health as h


class HistoryHealthTests(unittest.TestCase):
    def test_free_generation_stops_before_a_missing_cache_can_trigger_paid_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'cache.json').write_text('{}', encoding='utf-8')
            args = argparse.Namespace(cache=root / 'cache.json', snapshot=root / 'before.json',
                                      report=root / 'preflight.json', refresh_comments='false')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(h.preflight(args), 1)
            self.assertFalse((root / 'before.json').exists())
            self.assertEqual(json.loads((root / 'preflight.json').read_text(encoding='utf-8'))['errors'][0]['code'], 'AI_CACHE_INCOMPLETE')

    def test_a_passed_label_without_real_clipboard_evidence_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for component in ('preflight', 'content', 'browser'):
                h.write_json(root / (component + '.json'), {'status': 'passed'})
            args = argparse.Namespace(report_dir=root, html=root / 'missing.html', health=root / 'health.json')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(h.finalize(args), 1)
            report = json.loads((root / 'health.json').read_text(encoding='utf-8'))
            self.assertEqual(report['status'], 'failed')
            self.assertFalse(report['checks']['clipboard'])

    def test_real_clipboard_evidence_must_include_both_modes_and_exact_generated_body(self):
        hashes = {'historycompare': {'copyTextSha256': 'history'}, 'realestate': {'copyTextSha256': 'news'}}
        rows = []
        for page, card in (('generated', 'realestate'), ('generated', 'historycompare'),
                           ('fixture-matched', 'historycompare'), ('fixture-nomatch', 'historycompare')):
            row = {'page': page, 'card': card, 'status': 'passed', 'clipboardEqualsDataCopyText': True,
                   'attributionExcluded': True, 'articleDetailUrlsRetained': 1, 'visibleSourceFooter': card == 'historycompare',
                   'copyTextSha256': hashes[card]['copyTextSha256']}
            if page.startswith('fixture-'):
                row['fixtureMode'] = page.split('-', 1)[1]
            rows.append(row)
        report = {'summary': {'actualBrowser': True, 'engine': 'chromium'}, 'checks': rows}
        self.assertTrue(h.browser_proof(report, hashes))
        rows[1]['copyTextSha256'] = 'different'
        self.assertFalse(h.browser_proof(report, hashes))
        rows[1]['copyTextSha256'] = 'history'
        rows.pop()
        self.assertFalse(h.browser_proof(report, hashes))

    def test_story_requires_its_own_actual_copy_evidence_and_generated_hash(self):
        report = {'summary': {'actualBrowser': True}, 'storyCheck': {
            'page': 'generated', 'card': 'housingstory', 'status': 'passed',
            'clipboardEqualsDataCopyText': True, 'attributionExcluded': True,
            'visibleSourceFooter': True, 'articleDetailUrlsRetained': 1, 'copyTextSha256': 'story'}}
        self.assertTrue(h.story_browser_proof(report, {'housingstory': {'copyTextSha256': 'story'}}))
        self.assertFalse(h.story_browser_proof(report, {}))
        self.assertFalse(h.story_browser_proof(report, {'housingstory': {'copyTextSha256': 'different'}}))
        report['storyCheck']['attributionExcluded'] = False
        self.assertFalse(h.story_browser_proof(report, {'housingstory': {'copyTextSha256': 'story'}}))


if __name__ == '__main__':
    unittest.main()
