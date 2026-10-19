"""Offline failure/race/scope regressions; never dispatch a real workflow."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import free_refresh_recovery as r


def at(value):
    return r.instant(value)


def run(created='2026-10-19T12:30:00Z', status='completed', title='Daily Briefing', event='schedule'):
    return {'id': 123, 'created_at': created, 'status': status, 'conclusion': 'success',
            'display_title': title, 'event': event, 'head_branch': 'main', 'head_sha': 'a' * 40,
            'html_url': 'https://github.com/worldjohd-crypto/daily-briefing/actions/runs/123'}


class Client:
    def __init__(self, rows=(), error=None):
        self.rows, self.error, self.posts = list(rows), error, []

    def runs(self, now):
        return self.rows

    def check_contract(self):
        return 'a' * 40

    def dispatch(self, slot):
        self.posts.append(slot.isoformat())
        if self.error:
            raise self.error


class RecoveryTests(unittest.TestCase):
    def test_kst_slots_boundaries_and_overnight_catchup(self):
        for now, expected in [('2026-10-19T21:29:59+09:00', '2026-10-19T20:30:00+09:00'),
                              ('2026-10-19T21:30:00+09:00', '2026-10-19T21:30:00+09:00'),
                              ('2026-10-20T06:29:59+09:00', '2026-10-19T21:30:00+09:00'),
                              ('2026-10-20T06:30:00+09:00', '2026-10-20T06:30:00+09:00')]:
            self.assertEqual(r.latest_slot(at(now)).isoformat(), expected)

    def test_waits_45_minutes_then_requests_latest_only(self):
        client, state = Client(), {}
        self.assertEqual(r.recover(client, state, at('2026-10-19T22:14:59+09:00'), lambda: None, True)['status'], 'schedule_grace')
        self.assertEqual(client.posts, [])
        self.assertEqual(r.recover(client, state, at('2026-10-19T22:15:00+09:00'), lambda: None, True)['status'], 'accepted')
        self.assertEqual(client.posts, ['2026-10-19T21:30:00+09:00'])

    def test_active_or_existing_failed_run_prevents_retry(self):
        now = at('2026-10-19T23:00:00+09:00')
        for row in (run(status='queued'), run(status='in_progress'),
                    dict(run(), conclusion='failure')):
            client = Client([row])
            self.assertFalse(r.recover(client, {}, now, lambda: None, True)['dispatchAttempted'])
            self.assertEqual(client.posts, [])

    def test_dry_run_never_claims_or_posts(self):
        state, client = {}, Client()
        self.assertEqual(r.recover(client, state, at('2026-10-19T23:00:00+09:00'), lambda: None)['status'], 'would_dispatch')
        self.assertEqual(state['claims'], {})
        self.assertEqual(client.posts, [])

    def test_persist_before_post_and_restart_does_not_post_twice(self):
        state, client, snapshots = {}, Client(), []
        now = at('2026-10-19T23:00:00+09:00')
        result = r.recover(client, state, now, lambda: snapshots.append(copy.deepcopy(state)), True)
        claim = next(iter(snapshots[0]['claims'].values()))
        self.assertEqual(claim['status'], 'dispatching')
        self.assertEqual(result['status'], 'accepted')
        restarted = json.loads(json.dumps(state))
        self.assertEqual(r.recover(client, restarted, now, lambda: None, True)['status'], 'awaiting_dispatch_confirmation')
        self.assertEqual(len(client.posts), 1)

    def test_lost_response_reconciles_exact_run_and_never_retries(self):
        state, client = {}, Client(error=TimeoutError('SECRET_SENTINEL_MUST_NOT_BE_SAVED'))
        now = at('2026-10-19T23:00:00+09:00')
        self.assertEqual(r.recover(client, state, now, lambda: None, True)['status'], 'uncertain')
        self.assertNotIn('SECRET_SENTINEL', json.dumps(state))
        self.assertEqual(r.recover(client, state, now, lambda: None, True)['status'], 'awaiting_dispatch_confirmation')
        slot = r.latest_slot(now)
        client.rows = [run('2026-10-19T14:00:02Z', title=r.marker(slot), event='workflow_dispatch')]
        result = r.recover(client, state, now + dt.timedelta(minutes=1), lambda: None, True)
        self.assertEqual(result['status'], 'already_requested')
        self.assertEqual(result['runId'], '123')
        self.assertEqual(len(client.posts), 1)

    def test_uncertain_previous_day_blocks_new_slot(self):
        state, client = {}, Client(error=TimeoutError())
        now = at('2026-10-19T23:00:00+09:00')
        r.recover(client, state, now, lambda: None, True)
        result = r.recover(client, state, now + dt.timedelta(hours=9), lambda: None, True)
        self.assertEqual(result['status'], 'awaiting_dispatch_confirmation')
        self.assertEqual(len(client.posts), 1)

    def test_wrong_title_or_old_or_wrong_event_cannot_confirm_claim(self):
        state, client = {}, Client()
        now = at('2026-10-19T23:00:00+09:00')
        r.recover(client, state, now, lambda: None, True)
        for row in (run('2026-10-19T14:00:02Z', event='workflow_dispatch'),
                    run('2026-10-19T13:00:02Z', title=r.marker(r.latest_slot(now)), event='workflow_dispatch'),
                    run('2026-10-19T14:00:02Z', title=r.marker(r.latest_slot(now)), event='schedule')):
            client.rows = [row]
            self.assertEqual(r.recover(client, state, now, lambda: None, True)['status'], 'awaiting_dispatch_confirmation')
        self.assertEqual(len(client.posts), 1)

    def test_dispatch_transport_payload_is_fixed_and_free(self):
        client = object.__new__(r.GitHub)
        with patch.object(client, 'request', return_value={}) as call:
            client.dispatch(r.latest_slot(at('2026-10-19T23:00:00+09:00')))
        self.assertEqual(call.call_args.args, ('actions/workflows/daily.yml/dispatches',
                         {'ref': 'main', 'inputs': {'refresh_comments': False,
                          'recovery_slot': '2026-10-19T21:30:00+09:00'}}))

    def test_source_fix_replacement_requires_exact_failed_native_and_passed_gates(self):
        client = object.__new__(r.GitHub)
        row = dict(run(), conclusion='failure', head_sha='b'*40)
        names = ['Check AI cache before free generation', 'Validate historical matching and monthly prices',
                 'Check actual browser clipboard and both matching modes',
                 'Validate official transactions and reconstructed story',
                 'Require all checks before publication', 'Prepare verified story delivery manifest']
        steps = [{'name': name, 'conclusion': 'success'} for name in names]
        steps.append({'name':'Commit and push if changed', 'conclusion':'failure'})
        with patch.object(client, 'request', side_effect=[{'status':'ahead'}, {'jobs':[{'steps':steps}]}]):
            self.assertTrue(client.can_replace_failed_source(row, 'a'*40))
        steps[0]['conclusion'] = 'failure'
        with patch.object(client, 'request', side_effect=[{'status':'ahead'}, {'jobs':[{'steps':steps}]}]):
            self.assertFalse(client.can_replace_failed_source(row, 'a'*40))
        for changed in (dict(row, event='workflow_dispatch'), dict(row, head_sha='a'*40),
                        dict(row, conclusion='success'), dict(row, status='in_progress')):
            self.assertFalse(client.can_replace_failed_source(changed, 'a'*40))

    def test_operator_source_recovery_still_claims_once_and_default_does_not_retry(self):
        client = Client([dict(run(), conclusion='failure', head_sha='b'*40)])
        client.can_replace_failed_source = lambda row, head: True
        state, now = {}, at('2026-10-19T23:00:00+09:00')
        self.assertEqual(r.recover(client, state, now, lambda:None, True)['status'], 'run_already_exists')
        result = r.recover(client, state, now, lambda:None, True, superseded_run=123)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(next(iter(state['claims'].values()))['supersededNativeRunId'], '123')
        r.recover(client, state, now, lambda:None, True, superseded_run=123)
        self.assertEqual(len(client.posts), 1)

    def test_server_rejects_paid_invalid_future_or_stale_recovery(self):
        now = at('2026-10-19T23:00:00+09:00')
        slot = '2026-10-19T21:30:00+09:00'
        self.assertTrue(r.validate_request(slot, 'false', 'workflow_dispatch', now)['recovery'])
        for s, refresh, event in [(slot, 'true', 'workflow_dispatch'), (slot, 'false', 'schedule'),
                                  ('2026-10-19T21:31:00+09:00', 'false', 'workflow_dispatch'),
                                  ('2026-10-20T06:30:00+09:00', 'false', 'workflow_dispatch'),
                                  ('2026-10-18T21:30:00+09:00', 'false', 'workflow_dispatch')]:
            with self.assertRaises(ValueError):
                r.validate_request(s, refresh, event, now)
        self.assertFalse(r.validate_request('', 'true', 'workflow_dispatch', now)['recovery'])

    def test_workflow_guard_preserves_all_publication_checks(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/daily.yml').read_text(encoding='utf-8')
        guard = "github.event_name == 'workflow_dispatch' && inputs.recovery_slot == '' && inputs.refresh_comments"
        self.assertEqual(workflow.count(guard), 4)
        for evidence in ('history_health.py preflight', 'verify_history_content.py', 'check_history_browser.py',
                         'verify_housing_story.py', 'history_health.py finalize', 'story_delivery.py',
                         'history-validation-${{ github.run_id }}'):
            self.assertIn(evidence, workflow)
        self.assertLess(workflow.index('--validate-request'), workflow.index('run: python scripts/generate_briefing.py'))

    def test_remote_contract_rejects_missing_paid_guard_or_empty_cache(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / '.github/workflows/daily.yml').read_text(encoding='utf-8')
        script = (root / 'scripts/free_refresh_recovery.py').read_text(encoding='utf-8')
        client = object.__new__(r.GitHub)
        def text(head, path):
            return {'.github/workflows/daily.yml': workflow,
                    'scripts/free_refresh_recovery.py': script,
                    'comment-cache.json': json.dumps({k: {'text': 'cached', 'updated': 'unchanged'} for k in r.AI_KEYS})}[path]
        with patch.object(client, 'request', side_effect=[{'state':'active'}, {'object':{'sha':'a'*40}}]), patch.object(client, 'text', side_effect=text):
            self.assertEqual(client.check_contract(), 'a'*40)
        for unsafe in (workflow.replace("inputs.recovery_slot == '' && ", '', 1),):
            with patch.object(client, 'request', side_effect=[{'state':'active'}, {'object':{'sha':'a'*40}}]), patch.object(client, 'text', return_value=unsafe):
                with self.assertRaisesRegex(ValueError, 'FREE_RECOVERY_CONTRACT_MISSING'):
                    client.check_contract()
        with patch.object(client, 'request', side_effect=[{'state':'active'}, {'object':{'sha':'a'*40}}]), patch.object(client, 'text', side_effect=[workflow, script, '{}']):
            with self.assertRaisesRegex(ValueError, 'AI_CACHE_INCOMPLETE'):
                client.check_contract()

    def test_stale_monitor_and_lock_never_access_github(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root / 'recovery.json'; monitor = root / 'monitor.json'
            monitor.write_text(json.dumps({'config': {'repo': r.REPO, 'workflow': r.WORKFLOW,
                'branch': r.BRANCH, 'enabled': True, 'expectedScheduleKst': {'hours': list(range(6,22)), 'minute':30}},
                'lastCheckedAtUtc': '2026-01-01T00:00:00Z'}), encoding='utf-8')
            argv = ['recovery', '--state', str(state), '--monitor-state', str(monitor), '--apply']
            with patch('sys.argv', argv), patch.object(r, 'GitHub') as github, patch('builtins.print'):
                self.assertEqual(r.main(), 0)
                github.assert_not_called()
                self.assertEqual(json.loads(state.read_text())['lastResult']['status'], 'awaiting_fresh_monitor')
                state.with_suffix('.lock').touch()
                self.assertEqual(r.main(), 1)
                github.assert_not_called()


if __name__ == '__main__':
    unittest.main()
