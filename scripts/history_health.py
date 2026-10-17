"""Gate publication on free history checks; no provider or AI requests."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess

from bs4 import BeautifulSoup

AI_KEYS = ('politics', 'economy', 'realestate', 'world', 'finance', 'ai')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def preflight(args):
    stamp = now()
    cache = {}
    try:
        cache = read_json(args.cache)
        valid = isinstance(cache, dict) and all(isinstance(cache.get(k), dict)
                and isinstance(cache[k].get('text'), str) and cache[k]['text'].strip()
                and isinstance(cache[k].get('updated'), str) and cache[k]['updated'].strip() for k in AI_KEYS)
    except (OSError, ValueError, TypeError):
        valid = False
    refresh = args.refresh_comments == 'true'
    passed = bool(valid or refresh)
    report = {'schemaVersion': 1, 'status': 'passed' if passed else 'failed',
              'checkedAtUtc': stamp, 'refreshComments': refresh, 'cacheCount': len(cache) if isinstance(cache, dict) else 0,
              'checks': {'sixNonemptyCaches': bool(valid), 'freeRunCannotFallBackToPaidAI': passed},
              'errors': [] if passed else [{'code': 'AI_CACHE_INCOMPLETE', 'message': '무료 갱신에 필요한 AI 캐시 6개가 없어 생성 전에 중단했습니다.'}],
              'summary': 'AI cache preflight passed' if passed else 'AI cache preflight failed'}
    write_json(args.report, report)
    if passed:
        write_json(args.snapshot, {'aiCache': cache, 'capturedAtUtc': stamp, 'refreshComments': refresh})
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
                stream.write('started_at=' + stamp + '\n')
    print(report['summary'])
    return 0 if passed else 1


def cards(html):
    soup = BeautifulSoup(Path(html).read_text(encoding='utf-8'), 'html.parser')
    result = {}
    for sid in ('historycompare', 'realestate', 'housingstory'):
        box = soup.find(id=sid)
        if box is None or not box.has_attr('data-copy-text'):
            raise ValueError('Required card missing')
        source = box.parent.select_one('.source-note')
        body = box['data-copy-text'].replace('\r\n', '\n')
        attribution = source.get_text().replace('\r\n', '\n').strip() if source else ''
        result[sid] = {'copyTextSha256': sha(body), 'sourceTextSha256': sha(attribution)}
    return result


def browser_proof(report, card_hashes):
    summary = report.get('summary', {})
    if not isinstance(summary, dict):
        return False
    if summary.get('actualBrowser') is not True or summary.get('engine') != 'chromium':
        return False
    expected = {('generated', 'realestate'), ('generated', 'historycompare'),
                ('fixture-matched', 'historycompare'), ('fixture-nomatch', 'historycompare')}
    rows = report.get('checks', [])
    if not isinstance(rows, list) or len(rows) != len(expected) or not all(isinstance(row, dict) for row in rows):
        return False
    if {(row.get('page'), row.get('card')) for row in rows} != expected:
        return False
    for row in rows:
        if (row.get('status') != 'passed' or row.get('clipboardEqualsDataCopyText') is not True
                or row.get('attributionExcluded') is not True
                or type(row.get('articleDetailUrlsRetained')) is not int or row['articleDetailUrlsRetained'] < 1):
            return False
        if row['card'] == 'historycompare' and row.get('visibleSourceFooter') is not True:
            return False
        if row['page'] == 'generated' and row.get('copyTextSha256') != card_hashes.get(row['card'], {}).get('copyTextSha256'):
            return False
        if row['page'].startswith('fixture-') and row.get('fixtureMode') != row['page'].split('-', 1)[1]:
            return False
    return True


def story_browser_proof(report, card_hashes):
    row = report.get('storyCheck', {})
    expected_hash = card_hashes.get('housingstory', {}).get('copyTextSha256')
    return (report.get('summary', {}).get('actualBrowser') is True
            and bool(expected_hash)
            and row.get('page') == 'generated' and row.get('card') == 'housingstory'
            and row.get('status') == 'passed' and row.get('clipboardEqualsDataCopyText') is True
            and row.get('attributionExcluded') is True and row.get('visibleSourceFooter') is True
            and type(row.get('articleDetailUrlsRetained')) is int and row['articleDetailUrlsRetained'] > 0
            and row.get('copyTextSha256') == expected_hash)


def finalize(args):
    reports = {}
    errors = []
    for name in ('preflight', 'content', 'browser', 'story'):
        try:
            report = read_json(Path(args.report_dir) / (name + '.json'))
            if not isinstance(report, dict):
                raise TypeError('Invalid report')
            if report.get('status') != 'passed':
                errors.append({'code': name.upper() + '_CHECK_FAILED', 'message': name + ' validation did not pass'})
        except (OSError, ValueError, TypeError, AttributeError):
            report = {'status': 'failed', 'errors': [{'code': 'REPORT_MISSING', 'message': name + ' report unavailable'}]}
            errors.append({'code': name.upper() + '_REPORT_MISSING', 'message': name + ' report unavailable'})
        reports[name] = report
    card_hashes = {}
    try:
        card_hashes = cards(args.html)
    except (OSError, ValueError, TypeError, KeyError):
        errors.append({'code': 'GENERATED_CARDS_UNAVAILABLE', 'message': 'Generated card metadata unavailable'})
    refresh = reports['preflight'].get('refreshComments', False)
    content = reports['content'].get('checks', {})
    if not isinstance(content, dict):
        content = {}
    content_passed = reports['content'].get('status') == 'passed'
    ai = content_passed and content.get('aiCacheNonempty') is True
    if not refresh:
        ai = ai and content.get('aiSnapshotCompared') is True and content.get('aiCacheUnchanged') is True
    checks = {'content': content_passed and content.get('articleMatching') is True and content.get('sourceSeparation') is True,
              'prices': content_passed and content.get('prices') is True,
              'clipboard': reports['browser'].get('status') == 'passed' and browser_proof(reports['browser'], card_hashes),
              'ai': bool(ai) and reports['preflight'].get('status') == 'passed'}
    story = reports['story'].get('checks', {})
    checks['story'] = (reports['story'].get('status') == 'passed'
                       and all(story.get(key) is True for key in (
                           'actualCsvEvidence', 'currentLinkage', 'scenarioAccounting', 'sourceSeparation'))
                       and story_browser_proof(reports['browser'], card_hashes))
    for name, passed in checks.items():
        if not passed:
            errors.append({'code': name.upper() + '_PROOF_INCOMPLETE', 'message': name + ' evidence is missing or incomplete'})
    stamp = now()
    run_id = os.environ.get('GITHUB_RUN_ID', 'local-check')
    head = os.environ.get('GITHUB_SHA', '')
    aggregate = {'schemaVersion': 1, 'status': 'failed' if errors else 'passed',
                 'runId': run_id, 'headSha': head, 'checkedAtUtc': stamp,
                 'checks': checks, 'errors': errors, 'components': reports,
                 'summary': 'History validation failed' if errors else 'History validation passed'}
    report_path = Path(args.report_dir) / 'history-validation.json'
    write_json(report_path, aggregate)
    health = {'schemaVersion': 1, 'status': aggregate['status'], 'runId': run_id, 'headSha': head,
              'generatedAtUtc': stamp, 'checks': checks, 'cards': card_hashes,
              'reportArtifact': 'history-validation-' + run_id,
              'reportSha256': hashlib.sha256(report_path.read_bytes()).hexdigest(),
              'ai': {'refreshComments': bool(refresh), 'cacheCount': content.get('ai', {}).get('cacheCount', 0),
                     'unchanged': None if refresh else checks['ai']}}
    write_json(args.health, health)
    print(aggregate['summary'])
    return 1 if errors else 0


def deployment(args):
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    write_json(args.output, {'schemaVersion': 1, 'runId': os.environ.get('GITHUB_RUN_ID', 'local-check'),
                             'headSha': os.environ.get('GITHUB_SHA', ''), 'generatedCommit': head,
                             'pushedAtUtc': now()})
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    pre = sub.add_parser('preflight')
    pre.add_argument('--cache', default='comment-cache.json')
    pre.add_argument('--snapshot', required=True)
    pre.add_argument('--report', default='reports/preflight.json')
    pre.add_argument('--refresh-comments', choices=('true', 'false'), default='false')
    pre.set_defaults(func=preflight)
    finish = sub.add_parser('finalize')
    finish.add_argument('--report-dir', default='reports')
    finish.add_argument('--health', default='history-health.json')
    finish.add_argument('--html', default='briefing.html')
    finish.set_defaults(func=finalize)
    deploy = sub.add_parser('deployment')
    deploy.add_argument('--output', default='reports/deployment.json')
    deploy.set_defaults(func=deployment)
    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
