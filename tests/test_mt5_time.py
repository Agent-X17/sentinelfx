"""Time interpretation tests. No native terminal/network is used."""
import ast
import copy
import io
import os
import json
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from engine.domain import InvalidData
from engine.mt5_evidence import validate_snapshot
from engine.mt5_time import (TIMESTAMP_POLICY_SCHEMA, tick_time_evidence,
                             validate_clock_observation)
from scripts import verify_mt5_readonly as verifier
from test_mt5_evidence import fixture

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


def tick(age=1):
    millis = int(NOW.timestamp() * 1000) - int(age * 1000)
    return {'time': millis // 1000, 'time_msc': millis, 'bid': 1.1, 'ask': 1.1001}


class TickTimeTests(unittest.TestCase):
    def policy(self):
        return {
            'schema':TIMESTAMP_POLICY_SCHEMA,'status':'INDEPENDENTLY_VERIFIED',
            'policy_id':'reviewed-fixture','revision':1,'reviewed_by':'independent-reviewer',
            'source_url':'https://broker.example/timestamp-contract',
            'source_published_at':'2026-01-01T00:00:00+00:00',
            'source_retrieved_at':'2026-09-20T00:00:00+00:00','source_sha256':'a'*64,
            'broker_server_sha256':'b'*64,'package_version':'5.0.test','terminal_build':5000,
            'symbol':'EURUSD','timestamp_semantics':'MT5_PYTHON_TICK_EPOCH_OFFSET_INDEPENDENTLY_CONFIRMED',
            'dst_policy':{'status':'DOCUMENTED','timezone_name':'Broker/Published'},
            'validity_intervals':[{'utc_start':'2026-03-29T00:00:00+00:00',
                                   'utc_end':'2026-10-25T00:00:00+00:00',
                                   'offset_seconds':10800,'dst_state':'DAYLIGHT'}],
        }

    def context(self):
        return {'broker_server_sha256':'b'*64,'package_version':'5.0.test',
                'terminal_build':5000,'symbol':'EURUSD'}

    def test_documented_utc_is_unchanged_and_raw_is_preserved(self):
        raw = tick()
        before = copy.deepcopy(raw)
        result = tick_time_evidence(raw, NOW)
        self.assertEqual(result['freshness'], 'PASS')
        self.assertEqual(result['normalized_utc'], '2026-09-25T11:59:59.000+00:00')
        self.assertEqual(result['raw_time_msc'], raw['time_msc'])
        self.assertEqual(result['applied_offset_seconds'], 0)
        self.assertEqual(raw, before)

    def test_exact_freshness_boundaries(self):
        for age, expected in [(0, 'PASS'), (30, 'PASS'), (30.001, 'BLOCKED'), (-0.001, 'BLOCKED')]:
            with self.subTest(age=age):
                self.assertEqual(tick_time_evidence(tick(age), NOW)['freshness'], expected)

    def test_future_without_independent_offset_is_rejected(self):
        result = tick_time_evidence(tick(-10797), NOW)
        self.assertEqual(result['code'], 'MT5_TICK_STALE_OR_FUTURE')
        self.assertIsNone(result['normalized_utc'])
        self.assertIsNotNone(result['documented_utc_candidate'])
        self.assertIn('UNAVAILABLE', result['trusted_offset_status'])

    def test_stable_broker_like_offset_is_not_proof_of_utc(self):
        # Stability alone cannot authorize acceptance: stale replay can be stable too.
        for index in range(5):
            now = NOW + timedelta(seconds=index)
            raw = tick(-10800 - index)
            self.assertEqual(tick_time_evidence(raw, now)['freshness'], 'BLOCKED')

    def test_validated_offset_requires_complete_versioned_evidence(self):
        result = tick_time_evidence(tick(-10799), NOW, self.policy(), self.context())
        self.assertEqual(result['freshness'], 'PASS')
        self.assertEqual(result['applied_offset_seconds'], 10800)
        self.assertEqual(result['trusted_offset_status'], 'INDEPENDENTLY_VERIFIED_VERSIONED_POLICY')
        incomplete = self.policy(); incomplete.pop('source_sha256')
        result = tick_time_evidence(tick(-10799), NOW, incomplete, self.context())
        self.assertEqual(result['freshness'], 'BLOCKED')
        self.assertEqual(result['code'], 'MT5_TIMESTAMP_POLICY_INCOMPLETE')

    def test_verified_offset_change_is_rejected(self):
        context = self.context(); context['last_verified_offset_seconds'] = 7200
        result = tick_time_evidence(tick(-10799), NOW, self.policy(), context)
        self.assertEqual(result['code'], 'MT5_TIMESTAMP_OFFSET_CHANGED')
        self.assertEqual(result['freshness'], 'BLOCKED')

    def test_dst_policy_ambiguity_is_rejected(self):
        policy = self.policy()
        policy['validity_intervals'].append(dict(policy['validity_intervals'][0]))
        result = tick_time_evidence(tick(-10799), NOW, policy, self.context())
        self.assertEqual(result['code'], 'MT5_TIMESTAMP_POLICY_DST_AMBIGUOUS')
        self.assertEqual(result['freshness'], 'BLOCKED')

    def test_unstable_offsets_remain_blocked(self):
        for offset in (10800, 7200, 10815, 3600):
            self.assertEqual(tick_time_evidence(tick(-offset), NOW)['freshness'], 'BLOCKED')

    def test_offset_change_after_utc_success_does_not_recalibrate(self):
        self.assertEqual(tick_time_evidence(tick(), NOW)['freshness'], 'PASS')
        self.assertEqual(tick_time_evidence(tick(-3600), NOW)['freshness'], 'BLOCKED')

    def test_inconsistent_seconds_and_millis_rejected(self):
        raw = tick(); raw['time'] -= 1
        self.assertEqual(tick_time_evidence(raw, NOW)['code'], 'MT5_TICK_TIMESTAMP_INCONSISTENT')

    def test_malformed_millis_never_falls_back_to_seconds(self):
        for value in (None, True, 'private-secret', 0, -1, float('nan'), float('inf'), 123.4, 10**30):
            with self.subTest(value=value):
                raw = tick(); raw['time_msc'] = value
                result = tick_time_evidence(raw, NOW)
                self.assertEqual(result['freshness'], 'BLOCKED')
                self.assertIsNone(result['raw_time_msc'])
                self.assertNotIn('private-secret', str(result))

    def test_seconds_must_also_be_a_native_integer(self):
        for value in (None, False, '123', 0, -1, float('inf'), NOW.timestamp()):
            raw = tick(); raw['time'] = value
            self.assertEqual(tick_time_evidence(raw, NOW)['freshness'], 'BLOCKED')

    def test_missing_tick_and_naive_clock_fail_closed(self):
        self.assertEqual(tick_time_evidence(None, NOW)['freshness'], 'BLOCKED')
        self.assertEqual(tick_time_evidence(tick(), NOW.replace(tzinfo=None))['code'], 'MT5_CLOCK_INVALID')

    def test_os_timezone_does_not_change_epoch_interpretation(self):
        baseline = tick_time_evidence(tick(), NOW)
        with patch.dict(os.environ, {'TZ': 'Africa/Nairobi'}):
            self.assertEqual(tick_time_evidence(tick(), NOW), baseline)
        self.assertEqual(tick_time_evidence(tick(), NOW.astimezone(timezone(timedelta(hours=3)))), baseline)

    def test_offset_flags_in_tick_and_environment_cannot_authorize_correction(self):
        raw = tick(-10797)
        raw.update(trusted_offset_seconds=10800, trusted=True, normalized_utc=NOW.isoformat())
        with patch.dict(os.environ, {'MT5_TICK_OFFSET_SECONDS': '10800'}):
            self.assertEqual(tick_time_evidence(raw, NOW)['freshness'], 'BLOCKED')


class ClockObservationTests(unittest.TestCase):
    def observation(self):
        return {'wall_start': NOW.timestamp()-0.1, 'wall_end': NOW.timestamp(), 'monotonic_elapsed': 0.1}

    def test_consistent_elapsed_clock_is_accepted(self):
        validate_clock_observation(self.observation(), NOW, NOW)

    def test_clock_step_is_blocked(self):
        for delta in (-10800, -1, 1, 10800):
            raw = self.observation(); raw['wall_start'] += delta
            with self.assertRaisesRegex(InvalidData, 'MT5_CLOCK_DRIFT'):
                validate_clock_observation(raw, NOW, NOW)

    def test_inconsistent_capture_source_is_blocked(self):
        with self.assertRaisesRegex(InvalidData, 'MT5_CLOCK_SOURCE_INCONSISTENT'):
            validate_clock_observation(self.observation(), NOW, NOW+timedelta(seconds=1))

    def test_parent_clock_drift_is_blocked(self):
        for delta in (-1, 31):
            with self.assertRaisesRegex(InvalidData, 'MT5_CLOCK_SOURCE_INCONSISTENT'):
                validate_clock_observation(self.observation(), NOW+timedelta(seconds=delta), NOW)

    def test_missing_and_invalid_clock_evidence_blocked(self):
        for raw in (None, {}, {'wall_start': True}, {'wall_start': float('nan')}):
            with self.assertRaises(InvalidData):
                validate_clock_observation(raw, NOW, NOW)

    def test_proposal_evidence_requires_clock_observation(self):
        data = fixture(); data.pop('clock_observation')
        with self.assertRaisesRegex(InvalidData, 'MT5_CLOCK_EVIDENCE_MISSING'):
            validate_snapshot(data, {'mt5_symbol': 'EURUSD.a'}, '900001', 'DEMO-SERVER')

    def test_proposal_evidence_rejects_inconsistent_fields(self):
        data = fixture(); data['symbol_info_tick']['data']['time'] += 3600
        with self.assertRaisesRegex(InvalidData, 'MT5_TICK_TIMESTAMP_INCONSISTENT'):
            validate_snapshot(data, {'mt5_symbol': 'EURUSD.a'}, '900001', 'DEMO-SERVER')

    def test_proposal_evidence_requires_each_call_clock(self):
        data = fixture(); data['call_observations']['symbol_info_tick']['utc_after'] += 2
        with self.assertRaisesRegex(InvalidData, 'MT5_CALL_CLOCK_DRIFT'):
            validate_snapshot(data, {'mt5_symbol': 'EURUSD.a'}, '900001', 'DEMO-SERVER')


class VerifierTimeTests(unittest.TestCase):
    def test_same_tick_polling_spread_is_not_offset_instability(self):
        samples = [{'raw_time_msc':1790369298165,
                    'raw_minus_call_midpoint_seconds':offset}
                   for offset in (10799.4646778, 10798.0338173, 10796.6524187)]
        with redirect_stdout(io.StringIO()):
            result = verifier.summarize_samples(samples)
        self.assertEqual(result['apparent_offset_status'], 'UNVERIFIED_SAME_TICK_REPEATED')
        self.assertEqual(result['tick_progression'], 'BLOCKED_NOT_ADVANCING')
        self.assertFalse(result['trusted_for_normalization'])
        self.assertAlmostEqual(result['apparent_offset_spread_seconds'], 2.8122591)

    def test_advancing_ticks_do_not_prove_offset_stability(self):
        samples = [{'raw_time_msc':1790369298165 + index * 1000,
                    'raw_minus_call_midpoint_seconds':10799.4} for index in range(3)]
        with redirect_stdout(io.StringIO()):
            result = verifier.summarize_samples(samples)
        self.assertEqual(result['apparent_offset_status'], 'UNVERIFIED_TICK_DELIVERY_AGE_UNKNOWN')
        self.assertFalse(result['trusted_for_normalization'])

    def test_report_save_error_preserves_timestamp_failure(self):
        data = fixture()
        data['symbol_info_tick']['data']['time'] += 10800
        data['symbol_info_tick']['data']['time_msc'] += 10800000
        original_parse = verifier.argparse.ArgumentParser.parse_args
        def arguments(parser, *args, **kwargs):
            result = original_parse(parser, *args, **kwargs)
            result.report_file = 'unwritable-report.json'
            return result
        with patch.object(verifier.argparse.ArgumentParser, 'parse_args', arguments), \
             patch.object(Path, 'write_text', side_effect=OSError('private path')):
            code, output = self.run_verifier([data])
        self.assertEqual(code, 1)
        self.assertIn('Support report: SAVE_FAILED', output)
        self.assertIn('REASON: MT5_TICK_STALE_OR_FUTURE', output)
        self.assertNotIn('private path', output)

    def run_verifier(self, snapshots, samples=1):
        output = io.StringIO()
        env = {'SYSTEM_MODE': 'SIMULATION', 'LIVE_EXECUTION_ENABLED': 'false',
               'MT5_DIAGNOSTIC_MODE': 'real', 'DEMO_TRADE_PROPOSALS_ENABLED': 'false',
               'DEMO_TRADE_PROPOSAL_KILL_SWITCH': 'true',
               'DEMO_EXPECTED_ACCOUNT_LOGIN': '900001', 'DEMO_EXPECTED_BROKER_SERVER': 'DEMO-SERVER'}
        with patch.dict(os.environ, env, clear=True), patch.object(verifier, 'IsolatedMT5Service') as service, \
             patch('sys.argv', ['verify', '--symbol', 'EURUSD.a', '--time-samples', str(samples)]), \
             patch.object(verifier.time, 'sleep'), redirect_stdout(output):
            service.return_value.snapshot.side_effect = snapshots
            code = verifier.main()
            service.return_value.read_only_order_check.assert_not_called()
            service.return_value.shutdown.assert_called_once()
        return code, output.getvalue()

    def test_blocked_future_prints_safe_diagnostics(self):
        data = fixture(); raw = data['symbol_info_tick']['data']
        raw['time'] += 10800; raw['time_msc'] += 10800000
        code, output = self.run_verifier([data])
        self.assertEqual(code, 1)
        for text in ('Raw tick time', 'Trusted offset status: UNAVAILABLE', 'Normalized UTC tick timestamp: UNAVAILABLE',
                     'Timestamp freshness (30-second limit): BLOCKED', 'MT5_TICK_STALE_OR_FUTURE', 'No order was sent.'):
            self.assertIn(text, output)
        for secret in ('900001', 'DEMO-SERVER'):
            self.assertNotIn(secret, output)

    def test_valid_utc_sample_passes(self):
        code, output = self.run_verifier([fixture()])
        self.assertEqual(code, 0)
        self.assertIn('SNAPSHOT: PASS', output)
        self.assertIn('DEMO ORDER NOT SENT', output)

    def test_a_later_pass_does_not_erase_offset_change_failure(self):
        bad = fixture(); raw = bad['symbol_info_tick']['data']
        raw['time'] += 3600; raw['time_msc'] += 3600000
        code, output = self.run_verifier([fixture(), bad, fixture()], 3)
        self.assertEqual(code, 1)
        self.assertNotIn('RESULT: PASS', output)

    def test_untrusted_tick_strings_are_not_printed(self):
        data = fixture(); data['symbol_info_tick']['data'].update(time='SECRET', time_msc='PASSWORD')
        code, output = self.run_verifier([data])
        self.assertEqual(code, 1)
        self.assertNotIn('SECRET', output)
        self.assertNotIn('PASSWORD', output)

    def test_clock_drift_suppresses_normalized_timestamp(self):
        data = fixture(); data['clock_observation']['wall_start'] -= 5
        code, output = self.run_verifier([data])
        self.assertEqual(code, 1)
        self.assertIn('Collection clock consistency: BLOCKED', output)
        self.assertIn('Normalized UTC tick timestamp: UNAVAILABLE', output)

    def test_redacted_support_report_preserves_raw_diagnostics(self):
        data = fixture(); raw = data['symbol_info_tick']['data']
        raw['time'] += 10800; raw['time_msc'] += 10800000
        output = io.StringIO()
        env = {'SYSTEM_MODE':'SIMULATION','LIVE_EXECUTION_ENABLED':'false','MT5_DIAGNOSTIC_MODE':'real',
               'DEMO_TRADE_PROPOSALS_ENABLED':'false','DEMO_TRADE_PROPOSAL_KILL_SWITCH':'true',
               'DEMO_EXPECTED_ACCOUNT_LOGIN':'900001','DEMO_EXPECTED_BROKER_SERVER':'DEMO-SERVER'}
        with tempfile.TemporaryDirectory() as folder:
            target = str(Path(folder)/'report.json')
            with patch.dict(os.environ,env,clear=True),patch.object(verifier,'IsolatedMT5Service') as service, \
                 patch('sys.argv',['verify','--symbol','EURUSD.a','--time-samples','1','--report-file',target]), \
                 redirect_stdout(output):
                service.return_value.snapshot.return_value=data
                self.assertEqual(verifier.main(),1)
            report=json.loads(Path(target).read_text())
        self.assertEqual(report['samples'][0]['raw_time_msc'],raw['time_msc'])
        self.assertFalse(report['offset_applied'])
        self.assertFalse(report['repeated_analysis']['trusted_for_normalization'])
        self.assertEqual(report['result'],'BLOCKED_NO_TRADE')
        for secret in ('900001','DEMO-SERVER'):
            self.assertNotIn(secret,json.dumps(report))

    def test_no_submission_call_added_to_production(self):
        root = Path(__file__).resolve().parent.parent
        for path in [root/'server.py', *list((root/'engine').glob('*.py')), *list((root/'scripts').glob('*.py'))]:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, 'attr', getattr(node.func, 'id', ''))
                    self.assertNotEqual(name, 'order_send', str(path))


if __name__ == '__main__':
    unittest.main()
