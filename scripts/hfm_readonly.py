"""CLI-only HFM normalization. No bridge, proposal or approval integration."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from engine.domain import InvalidData, utcnow
from engine.hfm_readonly_time import evaluate, validate_policy, POLICY_REVISION
from engine.isolated_mt5 import IsolatedMT5Service

ROOT = Path(__file__).resolve().parent.parent


def clock_check():
    try:
        process = subprocess.run([sys.executable, '-B', '-m', 'engine.readonly_clock'],
                                 cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
        result = json.loads(process.stdout)
        if result.get('status') != 'MEASURED':
            raise InvalidData(result.get('error', 'HFM_CLOCK_UNCERTAIN'))
        return result
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError) as exc:
        raise InvalidData('HFM_CLOCK_SOURCE_UNAVAILABLE') from exc


def atomic_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def run(args, settings):
    report = {'schema': 'sentinelfx.hfm-readonly-report.v1', 'policy_revision': POLICY_REVISION,
              'result': 'BLOCKED_NO_TRADE', 'reason': None, 'samples': [],
              'order_sent': False, 'read_only': True, 'offset_applied': False}
    policy_path = Path(args.hfm_policy).resolve()
    state_path = policy_path.with_suffix(policy_path.suffix + '.state.json')
    lock_path = policy_path.with_suffix(policy_path.suffix + '.lock')
    service = None
    lock = None
    try:
        if args.order_check or args.time_samples < 3 or not args.report_file:
            raise InvalidData('HFM_REQUIRES_3_TO_5_SAMPLES_AND_REPORT_ONLY')
        output_path = Path(args.report_file).resolve()
        if output_path in (policy_path, state_path, lock_path):
            raise InvalidData('HFM_REPORT_PATH_COLLISION')
        policy = json.loads(policy_path.read_text(encoding='utf-8'))
        validate_policy(policy, utcnow(), settings.demo_expected_broker_server, args.symbol)
        lock = lock_path.open('x')
        previous = json.loads(state_path.read_text()) if state_path.exists() else None
        if previous is not None and (not isinstance(previous, dict) or previous.get('blocked') is not False):
            raise InvalidData('HFM_PREVIOUS_STATE_CHANGED_OR_BLOCKED')
        atomic_json(state_path, {'blocked': True, 'reason': 'HFM_RUN_INCOMPLETE'})
        checks = [clock_check()]
        service = IsolatedMT5Service(True, settings.mt5_terminal_path)
        snapshots = []
        for index in range(args.time_samples):
            if index:
                time.sleep(1)
            snapshot = service.snapshot(args.symbol, timestamp_reads=True)
            snapshots.append(snapshot)
            tick = snapshot.get('symbol_info_tick', {}).get('data') or {}
            report['samples'].append({
                'raw_time': tick.get('time') if type(tick.get('time')) is int else None,
                'raw_time_msc': tick.get('time_msc') if type(tick.get('time_msc')) is int else None,
                'normalized_utc': None, 'freshness': 'BLOCKED'})
            print('HFM read-only sample:', index + 1, 'of', args.time_samples)
        checks.append(clock_check())
        report['clock_evidence'] = checks
        result = evaluate(snapshots, policy, checks, utcnow(), settings.demo_expected_account_login,
                          settings.demo_expected_broker_server, args.symbol, previous, report['samples'])
        # Persistence must succeed before reporting a pass. No state is consumed
        # by the application/bridge; this is only a diagnostic change latch.
        atomic_json(state_path, {key: result[key] for key in
                    ('blocked', 'policy_digest', 'offset_seconds', 'policy_revision')})
        report.update(result, offset_applied=True)
    except Exception as exc:
        report['reason'] = str(exc) if isinstance(exc, InvalidData) else 'HFM_EVIDENCE_OR_FILE_INVALID'
        for row in report['samples']:
            row.update(normalized_utc=None, freshness='BLOCKED')
        if lock is not None:
            try:
                atomic_json(state_path, {'blocked': True, 'reason': report['reason']})
            except OSError:
                report['reason'] = 'HFM_STATE_SAVE_FAILED'
    finally:
        if service is not None:
            service.shutdown()
        if lock is not None:
            lock.close(); lock_path.unlink(missing_ok=True)
    report['generated_at'] = utcnow().isoformat()
    try:
        output_path = Path(args.report_file).resolve() if args.report_file else None
        if output_path is None or output_path in (policy_path, state_path, lock_path):
            raise OSError('invalid report path')
        atomic_json(output_path, report)
    except OSError:
        print('RESULT: BLOCKED / NO_TRADE — HFM_REPORT_SAVE_FAILED')
        return 1
    for row in report['samples']:
        print('Raw MT5 seconds / milliseconds:', row['raw_time'], '/', row['raw_time_msc'])
        print('Offset evidence:', row.get('offset_evidence', 'UNVERIFIED'))
        print('Seasonal offset seconds:', row.get('expected_offset_seconds', 'UNAVAILABLE'))
        print('Measured raw-minus-host seconds:', row.get('measured_raw_minus_host_seconds', 'UNAVAILABLE'))
        print('Normalized UTC:', row.get('normalized_utc') or 'UNAVAILABLE')
        print('UTC normalization candidate:', row.get('normalized_utc_candidate', 'UNAVAILABLE'))
        print('Age seconds:', row.get('age_seconds', 'UNAVAILABLE'))
        print('Freshness (30-second limit):', row['freshness'])
    passed = report['result'] == 'PASS_READ_ONLY_ONLY'
    print('RESULT:', 'PASS — READ-ONLY TIMESTAMP VERIFICATION ONLY' if passed else 'BLOCKED / NO_TRADE')
    if not passed:
        print('REASON:', report['reason'])
    print('No order was sent.')
    return 0 if passed else 1
