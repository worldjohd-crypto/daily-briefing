"""Offline delivery identity, provenance and publication-gate mutations."""

import copy
import datetime as dt
import hashlib
import html
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

import housing_story as story
import story_delivery as delivery
from content_rendering import render_history_links, render_sources, split_content_sources


REPO = Path(__file__).resolve().parents[1]


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def card(name, content):
    body, sources = split_content_sources(content)
    footer = '<div class="source-note">' + render_sources(sources) + '</div>' if sources else ''
    return (f'<section><button class="copy-btn" onclick="copySection(\'{name}\')">복사</button>'
            + f'<div id="{name}" data-copy-text="{html.escape(body, quote=True)}">'
            + render_history_links(body) + '</div>' + footer + '</section>')


class StoryDeliveryTests(unittest.TestCase):
    def setUp(self):
        root = REPO / 'reports'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='delivery-unit-', dir=root)
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.now = dt.datetime.now(story.KST).replace(second=0, microsecond=0) - dt.timedelta(days=2)
        self.catalog = json.loads((REPO / 'housing-history-articles.json').read_text(encoding='utf-8'))
        self.write('housing-history-articles.json', self.catalog)
        # Explicit synthetic fixture, never published or downloaded.
        raw = ('시군구,단지명,전용면적(㎡),계약년월,계약일,거래금액(만원),층,해제사유발생일\n'
               '테스트 서울구,검증용 아파트,84.9,201708,29,99000,21,-\n'
               '테스트 서울구,검증용 아파트,84.9,201908,1,142000,17,-\n').encode('utf-8')
        self.csv = self.repo / 'housing-story-evidence' / 'synthetic.csv'
        self.csv.parent.mkdir()
        self.csv.write_bytes(raw)
        records = [{'date': date, 'address': '테스트 서울구', 'apartment': '검증용 아파트',
                    'areaSqm': '84.9000', 'priceManwon': price, 'floor': floor,
                    'cancelled': False, 'sourceUrl': story.PAGE,
                    'evidenceFile': 'housing-story-evidence/synthetic.csv',
                    'evidenceSha256': hashlib.sha256(raw).hexdigest()}
                   for date, price, floor in (('2017-08-29', 99000, 21), ('2019-08-01', 142000, 17))]
        self.data = {'schemaVersion': 1, 'reviewedAt': (self.now - dt.timedelta(days=1)).isoformat(),
                     'source': {'provider': '합성 테스트', 'url': story.PAGE,
                                'method': '명확히 표시한 오프라인 합성 fixture'},
                     'cases': [{'id': 'unit-fixture', 'location': '테스트 서울구',
                                'apartment': '검증용 아파트', 'areaSqm': '84.9',
                                'selectionMethod': '오프라인 합성 fixture', 'topics': ['credit'],
                                'historicalArticleIds': ['moon-credit-20170802'],
                                'transactions': records}]}
        self.write('housing-story-evidence.json', self.data)
        self.render()

    def write(self, name, value):
        (self.repo / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def render(self, headline='주택 대출 규제 강화', now=None):
        now = now or self.now
        news = '🏠 [부동산 주요뉴스]\n오늘\n\n' + headline + '\nhttps://n.news.naver.com/mnews/article/001/0000000001'
        section = story.build_story_section(news, self.no_network, now,
                                            catalog_path=self.repo / 'housing-history-articles.json',
                                            evidence_path=self.repo / 'housing-story-evidence.json',
                                            evidence_base_dir=self.repo)
        self.page_path = self.repo / 'briefing.html'
        self.page_path.write_text('<html><body>' + card('realestate', news)
                                  + card('housingstory', section) + '</body></html>', encoding='utf-8')
        page = BeautifulSoup(self.page_path.read_text(encoding='utf-8'), 'html.parser')
        box = page.find(id='housingstory')
        self.health = {'schemaVersion': 1, 'status': 'passed', 'runId': '1234567890',
                       'headSha': 'a' * 40, 'generatedAtUtc': now.astimezone(dt.timezone.utc).isoformat(),
                       'checks': {key: True for key in delivery.REQUIRED_CHECKS},
                       'cards': {'housingstory': {
                           'copyTextSha256': digest(box['data-copy-text']),
                           'sourceTextSha256': digest(box.find_parent('section').select_one('.source-note').get_text().strip())}}}
        self.write('history-health.json', self.health)

    @staticmethod
    def no_network(*args, **kwargs):
        raise AssertionError('No network or paid AI permitted')

    def prepare(self):
        with patch('requests.get', side_effect=self.no_network), patch('requests.post', side_effect=self.no_network):
            return delivery.prepare_delivery(self.repo)

    def test_verified_manifest_preserves_actual_and_fiction_labels_without_sources(self):
        result = self.prepare()
        self.assertEqual(result['classification'], 'actual_prices_fictional_person')
        self.assertEqual(result['bodySha256'], digest(result['bodyText']))
        self.assertIn('[실거래 확인]', result['bodyText'])
        self.assertIn('[가상 인물·재구성]', result['bodyText'])
        self.assertIn('[비용 가정]', result['bodyText'])
        self.assertNotIn('자료:', result['bodyText'])
        self.assertNotIn(story.PAGE, result['bodyText'])
        self.assertEqual(result['identity']['areaSqm'], '84.9')
        self.assertFalse(result['networkUsed'])
        self.assertFalse(result['paidAiUsed'])
        self.assertEqual(len(result['sourceHashes']['officialCsv']), 1)
        json.dumps(result, allow_nan=False)

    def test_new_heading_and_current_article_do_not_create_new_story_identity(self):
        initial = self.prepare()
        self.render(headline='아파트 대출 규제 확대', now=self.now + dt.timedelta(days=1))
        updated = self.prepare()
        self.assertNotEqual(initial['bodySha256'], updated['bodySha256'])
        self.assertEqual(initial['storyKey'], updated['storyKey'])
        self.assertEqual(initial['semanticFingerprint'], updated['semanticFingerprint'])
        self.render(headline='대표이사 부고', now=self.now + dt.timedelta(days=1))
        reference = self.prepare()
        self.assertEqual(reference['selectionMode'], 'reference')
        self.assertEqual(reference['storyKey'], initial['storyKey'])

    def test_exact_contract_fact_changes_semantic_identity(self):
        case = story.load_story_evidence(self.repo / 'housing-story-evidence.json')['cases'][0]
        identity = delivery.semantic_identity(case)
        baseline = delivery.fingerprint_identity(identity)
        for field in ('location', 'apartment', 'areaSqm'):
            changed = copy.deepcopy(identity)
            changed[field] += '1'
            self.assertNotEqual(baseline, delivery.fingerprint_identity(changed))
        for contract in ('firstContract', 'lastContract'):
            for field in ('date', 'priceManwon', 'floor'):
                changed = copy.deepcopy(identity)
                changed[contract][field] = ('2019-08-02' if field == 'date'
                                             else changed[contract][field] + 1)
                self.assertNotEqual(baseline, delivery.fingerprint_identity(changed))

    def test_original_manually_delivered_case_has_explicit_stable_seed_identity(self):
        case = next(item for item in story.load_story_evidence(REPO / 'housing-story-evidence.json')['cases']
                    if item['id'] == 'mapo1-848919-201708-credit')
        self.assertEqual(case['id'], 'mapo1-848919-201708-credit')
        identity = delivery.semantic_identity(case)
        self.assertEqual(identity['location'], '서울특별시 마포구 아현동')
        self.assertEqual(identity['apartment'], '마포래미안푸르지오1단지')
        self.assertEqual(identity['areaSqm'], '84.8919')
        self.assertEqual(identity['firstContract'], {'date': '2017-08-29', 'priceManwon': 99000, 'floor': 21})
        self.assertEqual(identity['lastContract'], {'date': '2019-08-01', 'priceManwon': 142000, 'floor': 17})
        self.assertEqual(len(delivery.fingerprint_identity(identity)), 64)

    def test_second_official_case_has_new_identity_without_reidentifying_first(self):
        cases = story.load_story_evidence(REPO / 'housing-story-evidence.json')['cases']
        original = next(case for case in cases if case['id'] == 'mapo1-848919-201708-credit')
        addition = next(case for case in cases if case['id'] == 'seoseoul-samsung-4892-201708-credit')
        old_fingerprint = delivery.fingerprint_identity(delivery.semantic_identity(original))
        new_fingerprint = delivery.fingerprint_identity(delivery.semantic_identity(addition))
        self.assertEqual(old_fingerprint, 'e3b390119bc97a3ae50ea2161be6739e8298803d2fa586e4888eb18a26bd4be0')
        self.assertNotEqual(new_fingerprint, old_fingerprint)
        changed = dict(addition, fictionalBackground='다른 표현의 가상 인물 이야기', reviewedAt='later')
        self.assertEqual(new_fingerprint, delivery.fingerprint_identity(delivery.semantic_identity(changed)))

    def test_evidence_tamper_fails_even_with_previously_passed_health(self):
        self.csv.write_bytes(self.csv.read_bytes().replace(b'142000', b'143000'))
        with self.assertRaisesRegex(delivery.DeliveryError, '^STORY_VERIFICATION_FAILED$'):
            self.prepare()

    def test_source_label_in_copy_fails_even_when_health_copy_hash_changed(self):
        page = BeautifulSoup(self.page_path.read_text(encoding='utf-8'), 'html.parser')
        page.find(id='housingstory')['data-copy-text'] += '\n자료: 잘못된 복사 출처'
        self.page_path.write_text(str(page), encoding='utf-8')
        self.health['cards']['housingstory']['copyTextSha256'] = digest(page.find(id='housingstory')['data-copy-text'])
        self.write('history-health.json', self.health)
        with self.assertRaisesRegex(delivery.DeliveryError, '^STORY_VERIFICATION_FAILED$'):
            self.prepare()

    def test_missing_classification_fails_even_if_gate_hash_is_replaced(self):
        raw = self.page_path.read_text(encoding='utf-8').replace('[가상 인물·재구성]', '[확인된 실화]')
        self.page_path.write_text(raw, encoding='utf-8')
        with self.assertRaisesRegex(delivery.DeliveryError, '^STORY_VERIFICATION_FAILED$'):
            self.prepare()

    def test_missing_failed_or_stale_health_card_prevents_delivery(self):
        for mutation, code in ((lambda h: h.update(status='failed'), 'HEALTH_NOT_PASSED'),
                               (lambda h: h['checks'].update(story=False), 'HEALTH_CHECKS_INCOMPLETE'),
                               (lambda h: h['cards']['housingstory'].update(copyTextSha256='0' * 64), 'HEALTH_CARD_MISMATCH'),
                               (lambda h: h['cards']['housingstory'].update(sourceTextSha256='0' * 64), 'HEALTH_CARD_MISMATCH')):
            updated = copy.deepcopy(self.health)
            mutation(updated)
            self.write('history-health.json', updated)
            with self.assertRaisesRegex(delivery.DeliveryError, '^' + code + '$'):
                self.prepare()

    def test_cli_writes_complete_manifest_only_after_verification(self):
        with patch('requests.get', side_effect=self.no_network), patch('requests.post', side_effect=self.no_network):
            self.assertEqual(delivery.main(['--repo-dir', str(self.repo)]), 0)
        output = self.repo / 'housing-story-delivery.json'
        original = output.read_bytes()
        self.assertEqual(json.loads(original)['caseId'], 'unit-fixture')
        self.csv.write_bytes(b'broken')
        with patch('requests.get', side_effect=self.no_network), patch('requests.post', side_effect=self.no_network):
            self.assertEqual(delivery.main(['--repo-dir', str(self.repo)]), 1)
        self.assertEqual(output.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
