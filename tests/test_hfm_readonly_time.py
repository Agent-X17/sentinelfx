"""Synthetic broker clocks only: no terminal, orders or network."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from engine.domain import InvalidData
from engine.hfm_readonly_time import evaluate, seasonal_offset, SCHEMA, POLICY_REVISION
from engine.mt5_time import server_fingerprint
from engine.readonly_clock import decode, EPOCH
from engine.mt5 import MT5Service, MT5Result
from engine.mt5_worker import collect
from scripts import hfm_readonly
from test_mt5_evidence import fixture

UTC = timezone.utc


def batch(now, offset=None, age=5):
    offset = seasonal_offset(now) if offset is None else offset
    samples = []
    for i in range(3):
        call = now.timestamp() - 4 + i
        value = fixture()
        value['captured_at'] = datetime.fromtimestamp(call, UTC).isoformat()
        value['clock_observation'] = {'wall_start': call, 'wall_end': call, 'monotonic_elapsed': 0}
        for obs in value['call_observations'].values():
            obs.update(utc_before=call, utc_after=call, monotonic_elapsed=0)
        value['runtime_info']['python_version'] = '3.14.7'
        raw = round((now.timestamp() - age + i + offset) * 1000)
        value['symbol_info_tick']['data'].update(time=raw // 1000, time_msc=raw)
        value['tick_crosscheck'] = {}
        for method in ('copy_ticks_from', 'copy_ticks_range'):
            value['call_observations'][method] = {'utc_before': call, 'utc_after': call, 'monotonic_elapsed': 0}
            value['tick_crosscheck'][method] = {'matched': True,
                'utc_from': datetime.fromtimestamp(call - 30, UTC).isoformat(),
                'utc_to': datetime.fromtimestamp(call, UTC).isoformat()}
        samples.append(value)
    policy = {'schema': SCHEMA, 'revision': POLICY_REVISION, 'scope': 'READ_ONLY_DIAGNOSTIC',
              'year': 2026, 'server_fingerprint': server_fingerprint('DEMO-SERVER'),
              'symbol': 'EURUSD.a', 'package_version': '5.0.6180', 'terminal_build': 5000,
              'python_version': '3.14.7'}
    checks = [{'status': 'MEASURED', 'sources': ['time.windows.com', 'time.cloudflare.com'],
               'utc': now.timestamp() - seconds, 'monotonic': 100 - seconds,
               'uncertainty_seconds': .01} for seconds in (5, 1)]
    return samples, policy, checks


class HFMTimeTests(unittest.TestCase):
    now = datetime(2026, 9, 26, 12, tzinfo=UTC)

    def check(self, values, now=None, previous=None):
        return evaluate(*values, now or self.now, '900001', 'DEMO-SERVER', 'EURUSD.a', previous)

    def test_summer_and_winter(self):
        for month, offset in ((1, 7200), (7, 10800), (11, 7200)):
            now = self.now.replace(month=month)
            values = batch(now); original = copy.deepcopy(values)
            result = self.check(values, now)
            self.assertEqual(result['offset_seconds'], offset)
            self.assertEqual(result['result'], 'PASS_READ_ONLY_ONLY')
            self.assertEqual(values, original)
            self.assertEqual(result['samples'][0]['age_seconds'], 5)

    def test_both_dst_guard_boundaries_and_repeated_hour(self):
        for month, day in ((3, 29), (10, 25)):
            sunday = self.now.replace(month=month, day=day, hour=0)
            for instant in (sunday - timedelta(days=1), sunday, sunday + timedelta(hours=1),
                            sunday + timedelta(days=2, microseconds=-1)):
                with self.assertRaisesRegex(InvalidData, 'DST_UNCERTAIN'):
                    seasonal_offset(instant)
            before = seasonal_offset(sunday - timedelta(days=1, microseconds=1))
            after = seasonal_offset(sunday + timedelta(days=2))
            self.assertEqual((before, after), (7200, 10800) if month == 3 else (10800, 7200))

    def test_missing_policy(self):
        values = list(batch(self.now)); values[1] = None
        with self.assertRaisesRegex(InvalidData, 'POLICY_MISSING'): self.check(values)

    def test_unexpected_offset_future_and_stale(self):
        for offset, age in ((7200, 5), (10800, 3.999), (10800, 31)):
            with self.subTest(offset=offset, age=age):
                with self.assertRaisesRegex(InvalidData, 'STALE_OR_FUTURE'):
                    self.check(batch(self.now, offset, age))

    def test_identity_and_version_changes(self):
        for key, value in (('server_fingerprint', 'b'*64), ('package_version', 'new'),
                           ('terminal_build', 6183), ('python_version', '3.15.0')):
            values = batch(self.now); values[0][1]['runtime_info'][key] = value
            with self.assertRaisesRegex(InvalidData, 'RUNTIME_IDENTITY_MISMATCH'): self.check(values)

    def test_previous_offset_and_policy_changes(self):
        values = batch(self.now); result = self.check(values)
        self.check(values, previous=result)
        for change in ({'offset_seconds': 7200}, {'policy_digest': 'changed'}, {'blocked': True}):
            previous = dict(result, **change)
            with self.assertRaisesRegex(InvalidData, 'PREVIOUS_STATE'): self.check(values, previous=previous)

    def test_host_clock_drift(self):
        values = batch(self.now); values[2][1]['monotonic'] += 1
        with self.assertRaisesRegex(InvalidData, 'HOST_CLOCK_DRIFT'): self.check(values)

    def test_clock_missing_stale_uncertain_and_unbracketed(self):
        for change in ({'status': 'UNKNOWN'}, {'uncertainty_seconds': 1},
                       {'utc': self.now.timestamp()-40}, {'utc': self.now.timestamp()}):
            values = batch(self.now); values[2][0].update(change)
            with self.assertRaises(InvalidData): self.check(values)

    def test_time_fields_and_copy_ticks_disagreement(self):
        values = batch(self.now); values[0][0]['symbol_info_tick']['data']['time'] += 1
        with self.assertRaisesRegex(InvalidData, 'FIELDS_INCONSISTENT'): self.check(values)
        for method in ('copy_ticks_from', 'copy_ticks_range'):
            values = batch(self.now); values[0][0]['tick_crosscheck'][method]['matched'] = False
            with self.assertRaisesRegex(InvalidData, 'CROSS_API'): self.check(values)

    def test_three_distinct_ticks_required(self):
        values = batch(self.now)
        for row in values[0]:
            row['symbol_info_tick']['data'] = copy.deepcopy(values[0][0]['symbol_info_tick']['data'])
        with self.assertRaisesRegex(InvalidData, 'DISTINCT_TICKS'): self.check(values)

    def test_disconnected_and_identity_account_mismatch(self):
        for name, key, value in (('terminal_info', 'connected', False), ('account_info', 'login', 8)):
            values = batch(self.now); values[0][0][name]['data'][key] = value
            with self.assertRaises(InvalidData): self.check(values)

    def test_expired_policy(self):
        with self.assertRaisesRegex(InvalidData, 'POLICY_EXPIRED'):
            self.check(batch(self.now), self.now.replace(year=2027))

    def test_exact_freshness_boundary_and_bad_policy_revision(self):
        values = batch(self.now, age=30)
        for clock in values[2]: clock['uncertainty_seconds'] = 0
        self.assertEqual(self.check(values)['samples'][0]['age_seconds'], 30)
        values = batch(self.now, age=30.001)
        with self.assertRaises(InvalidData): self.check(values)
        values = batch(self.now); values[1]['revision'] = 'changed'
        with self.assertRaisesRegex(InvalidData, 'POLICY_BINDING'): self.check(values)

    def test_worker_cross_api_reads_use_utc_and_match_exact_tick(self):
        adapter = Mock()
        data = fixture()
        data['symbol_info_tick']['data'].update(last=0., volume=0, flags=2, volume_real=0.)
        adapter.status.return_value = MT5Result(True, 'OK', 'ok')
        for name in ('terminal_info', 'account_info', 'symbol_info', 'symbol_info_tick',
                     'positions_get', 'orders_get'):
            getattr(adapter, name).return_value = MT5Result(**data[name])
        for name in ('history_deals_get', 'history_orders_get'):
            getattr(adapter, name).return_value = MT5Result(True, 'OK', 'ok', {'items': []})
        adapter._module.__version__ = '5.0.6180'
        adapter._module.COPY_TICKS_ALL = 0
        for name in ('copy_ticks_from', 'copy_ticks_range'):
            getattr(adapter._module, name).return_value = [data['symbol_info_tick']['data']]
        result = collect(adapter, 'EURUSD.a', timestamp_reads=True)
        for name in ('copy_ticks_from', 'copy_ticks_range'):
            self.assertTrue(result['tick_crosscheck'][name]['matched'])
            self.assertIs(getattr(adapter._module, name).call_args.args[1].tzinfo, UTC)
        self.assertIs(adapter._module.copy_ticks_range.call_args.args[2].tzinfo, UTC)
        self.assertIs(adapter.history_deals_get.call_args.args[0].tzinfo, UTC)

    def test_adapter_rejects_naive_dates_and_normalizes_aware_inputs(self):
        adapter = MT5Service()
        self.assertFalse(adapter.history_orders_get(datetime(2026, 1, 1), self.now).ok)
        with patch.object(adapter, '_call') as call:
            local = self.now.astimezone(timezone(timedelta(hours=3)))
            adapter.history_deals_get(local, local)
            self.assertIs(call.call_args.args[1].tzinfo, UTC)
            self.assertEqual(call.call_args.args[1], self.now)

    def test_failed_run_keeps_raw_report_and_latches(self):
        values = batch(self.now, offset=7200)
        diagnostics = [dict() for _ in range(3)]
        with self.assertRaises(InvalidData):
            evaluate(*values, self.now, '900001', 'DEMO-SERVER', 'EURUSD.a', diagnostics=diagnostics)
        self.assertIsNotNone(diagnostics[0]['normalized_utc_candidate'])
        self.assertIsNone(diagnostics[0]['normalized_utc'])
        self.assertEqual(diagnostics[0]['freshness'], 'BLOCKED')

    def test_cli_pass_report_and_persistent_latch(self):
        values = batch(self.now)
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp)/'policy.json'; policy.write_text(json.dumps(values[1]))
            report = Path(tmp)/'report.json'
            args = SimpleNamespace(hfm_policy=str(policy), report_file=str(report),
                                   time_samples=3, symbol='EURUSD.a', order_check=False)
            settings = SimpleNamespace(demo_expected_account_login='900001',
                       demo_expected_broker_server='DEMO-SERVER', mt5_terminal_path='private')
            with patch.object(hfm_readonly, 'utcnow', return_value=self.now), \
                 patch.object(hfm_readonly, 'clock_check', side_effect=values[2]), \
                 patch.object(hfm_readonly.time, 'sleep'), \
                 patch.object(hfm_readonly, 'IsolatedMT5Service') as service:
                service.return_value.snapshot.side_effect = values[0]
                self.assertEqual(hfm_readonly.run(args, settings), 0)
            data = report.read_text()
            self.assertNotIn('DEMO-SERVER', data); self.assertNotIn('900001', data)
            state = policy.with_suffix('.json.state.json')
            state.write_text(json.dumps({'blocked': True}))
            with patch.object(hfm_readonly, 'utcnow', return_value=self.now):
                self.assertEqual(hfm_readonly.run(args, settings), 1)


class ClockPacketTests(unittest.TestCase):
    def packet(self):
        before = 1790000000.0
        sent = struct.pack('!II', int(before)+EPOCH, 0)
        packet = bytearray(48); packet[0] = 0x24; packet[1] = 2; packet[24:32] = sent
        packet[32:40] = struct.pack('!II', int(before)+EPOCH, int(.01*2**32))
        packet[40:48] = packet[32:40]
        return packet, sent, before, before+.02, .02

    def test_valid_clock_packet(self):
        offset, error = decode(*self.packet())
        self.assertAlmostEqual(offset, 0, places=5); self.assertLess(error, .02)

    def test_bad_originate_unsynced_and_clock_drift(self):
        for index, value in ((24, 0), (0, 0xE4), (1, 0)):
            args = list(self.packet()); args[0][index] = value
            with self.assertRaises(InvalidData): decode(*args)
        args = list(self.packet()); args[-1] = .4
        with self.assertRaises(InvalidData): decode(*args)
