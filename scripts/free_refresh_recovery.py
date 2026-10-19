"""At-most-once missed production refresh recovery; no local generation or paid AI.

Only --apply writes to GitHub: fixed daily.yml/main, refresh_comments=false.
Save a claim before POST. Reconcile uncertain responses; never retry them.
Keep monitor, publication validation and story sending independent.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.parse
import urllib.request

UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))
REPO = 'worldjohd-crypto/daily-briefing'
WORKFLOW = 'daily.yml'
BRANCH = 'main'
AI_KEYS = ('politics', 'economy', 'realestate', 'world', 'finance', 'ai')
GRACE_MINUTES = 45


def instant(value):
    value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return value.astimezone(UTC)


def latest_slot(now):
    local = now.astimezone(KST)
    return max(local.replace(hour=h, minute=30, second=0, microsecond=0)
               - dt.timedelta(days=day) for day in (0, 1) for h in range(6, 22)
               if local.replace(hour=h, minute=30, second=0, microsecond=0)
               - dt.timedelta(days=day) <= local)


def marker(slot):
    return 'Free recovery [' + slot.isoformat() + ']'


def save(path, state):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def alert(state, code, now):
    fingerprint = hashlib.sha256(code.encode()).hexdigest()
    if not any(a['fingerprint'] == fingerprint and not a.get('resolvedAtUtc')
               for a in state.setdefault('pendingAlerts', [])):
        state['pendingAlerts'].append({'fingerprint': fingerprint, 'code': code,
                                      'detectedAtUtc': now.isoformat(), 'notifiedAtUtc': None})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    def __init__(self, cwd):
        result = subprocess.run(['git', 'credential', 'fill'],
                                input='protocol=https\nhost=github.com\n\n', text=True,
                                capture_output=True, check=True, cwd=cwd)
        credentials = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        self._token = credentials.get('password')
        if not self._token:
            raise ValueError('GITHUB_ACCESS_UNAVAILABLE')
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, suffix, payload=None):
        url = 'https://api.github.com/repos/' + REPO + '/' + suffix
        headers = {'Authorization': 'Bearer ' + self._token,
                   'Accept': 'application/vnd.github+json',
                   'User-Agent': 'daily-briefing-free-recovery', 'Cache-Control': 'no-cache'}
        body = None if payload is None else json.dumps(payload).encode()
        if body is not None:
            headers['Content-Type'] = 'application/json'
        with self.opener.open(urllib.request.Request(url, data=body, headers=headers), timeout=45) as response:
            data = response.read(5_000_001)
            if len(data) > 5_000_000:
                raise ValueError('GITHUB_RESPONSE_TOO_LARGE')
            return json.loads(data) if data else {}

    def runs(self, now):
        rows = []
        cutoff = now - dt.timedelta(days=2)
        for page in range(1, 21):
            batch = self.request('actions/workflows/daily.yml/runs?' + urllib.parse.urlencode(
                {'branch': BRANCH, 'per_page': 100, 'page': page}))['workflow_runs']
            rows.extend(batch)
            if len(batch) < 100 or any(instant(r['created_at']) < cutoff for r in batch):
                return rows
        raise ValueError('RUN_PAGINATION_LIMIT')

    def text(self, head, path):
        data = self.request('contents/' + urllib.parse.quote(path, safe='/') + '?ref=' + head)
        return base64.b64decode(data['content']).decode('utf-8')

    def check_contract(self):
        workflow = self.request('actions/workflows/daily.yml')
        if workflow['state'] != 'active':
            raise ValueError('WORKFLOW_NOT_ACTIVE')
        head = self.request('git/ref/heads/main')['object']['sha']
        text = self.text(head, '.github/workflows/daily.yml')
        paid_guard = "github.event_name == 'workflow_dispatch' && inputs.recovery_slot == '' && inputs.refresh_comments"
        if (text.count(paid_guard) != 4 or 'recovery_slot:' not in text
                or 'python scripts/free_refresh_recovery.py --validate-request' not in text
                or "format('Free recovery [{0}]', inputs.recovery_slot)" not in text):
            raise ValueError('FREE_RECOVERY_CONTRACT_MISSING')
        if self.text(head, 'scripts/free_refresh_recovery.py') != Path(__file__).read_text(encoding='utf-8'):
            raise ValueError('FREE_RECOVERY_SOURCE_MISMATCH')
        cache = json.loads(self.text(head, 'comment-cache.json'))
        if not all(isinstance(cache.get(k), dict) and cache[k].get('text')
                   and cache[k].get('updated') for k in AI_KEYS):
            raise ValueError('AI_CACHE_INCOMPLETE')
        return head

    def dispatch(self, slot):
        return self.request('actions/workflows/daily.yml/dispatches',
                            {'ref': BRANCH, 'inputs': {'refresh_comments': False,
                                                      'recovery_slot': slot.isoformat()}})


def recover(client, state, now, persist, apply=False):
    slot = latest_slot(now)
    key = slot.isoformat()
    runs = client.runs(now)
    claims = state.setdefault('claims', {})
    for claim in claims.values():
        matches = [r for r in runs if r.get('display_title') == claim['marker']
                   and r.get('event') == 'workflow_dispatch' and r.get('head_branch') == BRANCH
                   and instant(r['created_at']) >= instant(claim['requestedAtUtc']) - dt.timedelta(seconds=5)]
        if len(matches) > 1:
            alert(state, 'RECOVERY_MULTIPLE_RUNS_FOR_SLOT', now)
            return {'status': 'blocked', 'reason': 'RECOVERY_MULTIPLE_RUNS_FOR_SLOT'}
        if matches:
            run = matches[0]
            claim.update(status='observed', runId=str(run['id']), runUrl=run.get('html_url'),
                         runStatus=run['status'], conclusion=run.get('conclusion'), headSha=run['head_sha'])
        elif claim['status'] in ('dispatching', 'accepted', 'uncertain'):
            if now - instant(claim['requestedAtUtc']) > dt.timedelta(minutes=90):
                alert(state, 'RECOVERY_REQUEST_NOT_OBSERVED', now)
            return {'status': 'awaiting_dispatch_confirmation', 'slotKst': claim['slotKst'],
                    'requestState': claim['status'], 'dispatchAttempted': False}
    if key in claims:
        return {'status': 'already_requested', 'slotKst': key,
                'runId': claims[key].get('runId'), 'dispatchAttempted': False}
    active = [r for r in runs if r['status'] != 'completed']
    if active:
        return {'status': 'workflow_active', 'runId': str(active[0]['id']), 'dispatchAttempted': False}
    fresh = [r for r in runs if instant(r['created_at']) >= slot.astimezone(UTC)]
    if fresh:
        latest = max(fresh, key=lambda r: (r['created_at'], r['id']))
        return {'status': 'run_already_exists', 'runId': str(latest['id']), 'dispatchAttempted': False}
    if now < slot + dt.timedelta(minutes=GRACE_MINUTES):
        return {'status': 'schedule_grace', 'slotKst': key, 'dispatchAttempted': False}
    head = client.check_contract()
    if not apply:
        return {'status': 'would_dispatch', 'slotKst': key, 'headSha': head, 'dispatchAttempted': False}
    claim = {'slotKst': key, 'marker': marker(slot), 'status': 'dispatching',
             'requestedAtUtc': now.isoformat(), 'requestedHeadSha': head, 'refreshComments': False}
    claims[key] = claim
    persist()
    try:
        client.dispatch(slot)
    except (OSError, ValueError, urllib.error.URLError) as error:
        claim['status'] = 'uncertain'
        claim['errorType'] = type(error).__name__
        claim['httpStatus'] = getattr(error, 'code', None)
        alert(state, 'RECOVERY_DISPATCH_UNCERTAIN', now)
        persist()
        return {'status': 'uncertain', 'slotKst': key, 'dispatchAttempted': True}
    claim['status'] = 'accepted'
    persist()
    return {'status': 'accepted', 'slotKst': key, 'refreshComments': False, 'dispatchAttempted': True}


def validate_request(slot, refresh, event, now):
    if not slot:
        return {'status': 'passed', 'recovery': False}
    parsed = instant(slot)
    if (event != 'workflow_dispatch' or refresh.lower() != 'false'
            or parsed.astimezone(KST).isoformat() != slot
            or parsed.astimezone(KST).hour not in range(6, 22)
            or parsed.minute != 30 or parsed.second or parsed.microsecond
            or not dt.timedelta(0) <= now - parsed <= dt.timedelta(hours=24)):
        raise ValueError('FREE_RECOVERY_REQUEST_INVALID')
    return {'status': 'passed', 'recovery': True, 'slotKst': slot, 'refreshComments': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path('state/free-refresh-recovery.json'))
    parser.add_argument('--monitor-state', type=Path, default=Path('state/history-monitor.json'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--ack-alert', action='append', default=[])
    parser.add_argument('--validate-request', action='store_true')
    args = parser.parse_args()
    now = dt.datetime.now(UTC)
    if args.validate_request:
        try:
            result = validate_request(os.environ.get('RECOVERY_SLOT', ''),
                                      os.environ.get('RECOVERY_REFRESH_REQUESTED', 'false'),
                                      os.environ.get('RECOVERY_EVENT', ''), now)
        except ValueError:
            result = {'status': 'failed', 'code': 'FREE_RECOVERY_REQUEST_INVALID'}
        Path('reports').mkdir(exist_ok=True)
        save(Path('reports/recovery-request.json'), result)
        print(json.dumps(result))
        return 0 if result['status'] == 'passed' else 1
    lock = args.state.with_suffix('.lock')
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        print(json.dumps({'status': 'locked', 'dispatchAttempted': False}))
        return 1
    try:
        os.close(fd)
        state = json.loads(args.state.read_text(encoding='utf-8-sig')) if args.state.exists() else {
            'schemaVersion': 1, 'repo': REPO, 'workflow': WORKFLOW, 'branch': BRANCH,
            'graceMinutes': GRACE_MINUTES, 'initializedAtUtc': now.isoformat(), 'claims': {}, 'pendingAlerts': []}
        if (state.get('schemaVersion'), state.get('repo'), state.get('workflow'), state.get('branch')) != (1, REPO, WORKFLOW, BRANCH):
            raise ValueError('RECOVERY_STATE_IDENTITY_INVALID')
        if args.ack_alert:
            for item in state['pendingAlerts']:
                if item['fingerprint'] in args.ack_alert and not item.get('notifiedAtUtc'):
                    item['notifiedAtUtc'] = now.isoformat()
            save(args.state, state)
            return 0
        monitor = json.loads(args.monitor_state.read_text(encoding='utf-8-sig'))
        config = monitor['config']
        schedule = config['expectedScheduleKst']
        if ((config['repo'], config['workflow'], config['branch']) != (REPO, WORKFLOW, BRANCH)
                or config.get('enabled') is not True or schedule['hours'] != list(range(6, 22))
                or schedule['minute'] != 30
                or not dt.timedelta(0) <= now - instant(monitor['lastCheckedAtUtc']) <= dt.timedelta(minutes=5)
                or monitor.get('monitorReadErrorFirstSeenUtc')
                or any(x.get('code', '').startswith('monitor_access_') for x in monitor.get('activeIssues', {}).values())):
            result = {'status': 'awaiting_fresh_monitor', 'dispatchAttempted': False}
        else:
            try:
                result = recover(GitHub(config['repositoryDirectory']), state, now,
                                 lambda: save(args.state, state), args.apply)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError, urllib.error.URLError) as error:
                code = 'RECOVERY_CHECK_' + type(error).__name__
                if isinstance(error, ValueError) and str(error).isupper():
                    code = str(error)
                alert(state, code, now)
                result = {'status': 'unavailable', 'code': code, 'dispatchAttempted': False}
        if result['status'] not in ('uncertain', 'blocked', 'unavailable', 'awaiting_dispatch_confirmation', 'awaiting_fresh_monitor'):
            for item in state['pendingAlerts']:
                item.setdefault('resolvedAtUtc', now.isoformat())
        state.update(lastCheckedAtUtc=now.isoformat(), lastResult=result)
        save(args.state, state)
        result['pendingAlerts'] = [a for a in state['pendingAlerts'] if not a.get('notifiedAtUtc')]
        print(json.dumps(result, ensure_ascii=True))
        return 0
    finally:
        lock.unlink()


if __name__ == '__main__':
    raise SystemExit(main())
