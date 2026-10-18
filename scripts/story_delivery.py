"""Export a verified, attribution-free housing story for a separate sender.

This publication step reads local official-record evidence and completed health
checks only. It does not contact Telegram, download prices, import the full
briefing generator or invoke AI. A semantic identity survives daily headings
and changing current-news links so the sender can deduplicate the same case.
"""

import argparse
import datetime as dt
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup

import housing_story
from verify_housing_story import read_json, verify_story


ROOT = Path(__file__).resolve().parents[1]
CLASSIFICATION = 'actual_prices_fictional_person'
REQUIRED_CHECKS = ('content', 'prices', 'clipboard', 'ai', 'story')
SOURCE_LABEL = re.compile(r'(?m)^\s*(?:자료|공식\s*출처|출처)\s*:')
HASH = re.compile(r'^[a-f0-9]{64}$')


class DeliveryError(ValueError):
    """A fixed, safe error code without source payloads or credentials."""


def _require(condition, code):
    if not condition:
        raise DeliveryError(code)


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _path(repo, value):
    value = Path(value)
    return value if value.is_absolute() else repo / value


def semantic_identity(case):
    """Return exact observed facts, excluding headings, articles and prose."""
    first, last = case['transactions'][0], case['transactions'][-1]

    def contract(record):
        return {'date': record['date'].isoformat(),
                'priceManwon': record['priceManwon'], 'floor': record['floor']}

    return {'location': case['location'], 'apartment': case['apartment'],
            'areaSqm': format(Decimal(case['areaSqm']).normalize(), 'f'),
            'firstContract': contract(first), 'lastContract': contract(last)}


def fingerprint_identity(identity):
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False)
    return _sha(canonical)


def prepare_delivery(repo_dir=ROOT, html_path='briefing.html', health_path=None):
    """Return a JSON-safe manifest only for the current checked story card.

    The health marker proves the actual browser publication gate completed;
    the independent story verifier also rechecks CSVs, arithmetic, article
    selection, rendered content and source separation against local inputs.
    All error strings are fixed codes suitable for CI logs.
    """
    repo = Path(repo_dir).resolve()
    html_path = _path(repo, html_path)
    health_path = _path(repo, health_path or 'history-health.json')
    try:
        health = read_json(health_path)
        _require(isinstance(health, dict) and health.get('schemaVersion') == 1
                 and health.get('status') == 'passed', 'HEALTH_NOT_PASSED')
        checks = health.get('checks')
        _require(isinstance(checks, dict)
                 and all(checks.get(key) is True for key in REQUIRED_CHECKS)
                 and all(value is True for value in checks.values()),
                 'HEALTH_CHECKS_INCOMPLETE')
        _require(isinstance(health.get('runId'), str) and health['runId'].isdigit()
                 and re.fullmatch(r'[a-f0-9]{40}', str(health.get('headSha', ''))),
                 'HEALTH_RUN_IDENTITY_INVALID')
        health_stamp = dt.datetime.fromisoformat(health['generatedAtUtc'].replace('Z', '+00:00'))
        _require(health_stamp.tzinfo is not None, 'HEALTH_TIMEZONE_MISSING')

        verification = verify_story(repo, html_path)
        _require(verification.get('status') == 'passed'
                 and all(verification.get('checks', {}).get(key) is True for key in (
                     'actualCsvEvidence', 'currentLinkage', 'scenarioAccounting', 'sourceSeparation')),
                 'STORY_VERIFICATION_FAILED')
        page = BeautifulSoup(html_path.read_text(encoding='utf-8-sig'), 'html.parser')
        story_card = page.find(id='housingstory')
        body = story_card['data-copy-text']
        _require(isinstance(body, str) and body.strip() and not SOURCE_LABEL.search(body),
                 'BODY_SOURCE_LEAK')
        _require(all(label in body for label in (
                     '[실거래 확인]', '[가상 인물·재구성]', '[비용 가정]', '[한계]')),
                 'CLASSIFICATION_MISSING')
        body_hash = _sha(body.replace('\r\n', '\n'))
        footer = story_card.find_parent('section').select_one('.source-note')
        footer_text = footer.get_text().replace('\r\n', '\n').strip()
        hashes = health.get('cards', {}).get('housingstory', {})
        _require(hashes.get('copyTextSha256') == body_hash
                 and hashes.get('sourceTextSha256') == _sha(footer_text),
                 'HEALTH_CARD_MISMATCH')
        _require(verification.get('summary', {}).get('copyTextSha256') == _sha(body),
                 'STORY_BODY_HASH_MISMATCH')

        evidence_path = repo / 'housing-story-evidence.json'
        catalog_path = repo / 'housing-history-articles.json'
        evidence = housing_story.load_story_evidence(evidence_path, base_dir=repo)
        catalog = read_json(catalog_path)
        news = page.find(id='realestate')['data-copy-text']
        selected = housing_story.select_story(news, evidence['cases'], catalog['articles'])
        _require(selected is not None, 'STORY_SELECTION_MISSING')
        case = selected['case']
        _require(verification.get('summary', {}).get('caseId') == case['id'],
                 'STORY_SELECTION_MISMATCH')
        identity = semantic_identity(case)
        fingerprint = fingerprint_identity(identity)
        _require(HASH.fullmatch(fingerprint), 'SEMANTIC_FINGERPRINT_INVALID')
        csv_hashes = sorted({(row['evidenceFile'], row['evidenceSha256'])
                             for row in case['transactions']})
        return {
            'schemaVersion': 1, 'caseId': case['id'],
            'storyKey': 'housingstory:' + case['id'] + ':' + fingerprint,
            'semanticFingerprint': fingerprint, 'identity': identity,
            'bodyText': body, 'bodySha256': body_hash,
            'classification': CLASSIFICATION,
            'selectionMode': selected['mode'],
            'evidenceReviewedAt': case['reviewedAt'].isoformat(),
            'sourceHashes': {
                'evidenceFileSha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                'catalogFileSha256': hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
                'officialCsv': [{'file': filename, 'sha256': digest}
                                for filename, digest in csv_hashes]},
            'healthRunId': health['runId'], 'healthHeadSha': health['headSha'],
            'generatedAt': health_stamp.isoformat(),
            'storyGeneratedAt': verification['summary']['generatedAtKst'],
            'networkUsed': False, 'paidAiUsed': False,
        }
    except DeliveryError:
        raise
    except (OSError, UnicodeError):
        raise DeliveryError('INPUT_UNAVAILABLE') from None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, ArithmeticError):
        raise DeliveryError('INPUT_INVALID') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-dir', type=Path, default=ROOT)
    parser.add_argument('--html', type=Path, default=Path('briefing.html'))
    parser.add_argument('--health', type=Path, default=Path('history-health.json'))
    parser.add_argument('--output', type=Path, default=Path('housing-story-delivery.json'))
    args = parser.parse_args(argv)
    output = _path(args.repo_dir.resolve(), args.output)
    try:
        result = prepare_delivery(args.repo_dir, args.html, args.health)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + '.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                        indent=2, allow_nan=False) + '\n', encoding='utf-8')
        temporary.replace(output)
    except DeliveryError as error:
        print(json.dumps({'status': 'failed', 'errorCode': str(error)}))
        return 1
    except OSError:
        print(json.dumps({'status': 'failed', 'errorCode': 'OUTPUT_UNAVAILABLE'}))
        return 1
    print(json.dumps({'status': 'passed', 'caseId': result['caseId'],
                      'semanticFingerprint': result['semanticFingerprint']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
