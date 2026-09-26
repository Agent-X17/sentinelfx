"""Explicit HFM diagnostic policy. Never used by proposal or execution paths."""
import calendar
import copy
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

from .domain import InvalidData, date
from .mt5_evidence import validate_snapshot
from .mt5_time import server_fingerprint, validate_call_observation

POLICY_REVISION = 'hfm-readonly-2026-v1'
SCHEMA = 'sentinelfx.hfm-readonly-policy.v1'


def reject(code):
    raise InvalidData('HFM_' + code)


def seasonal_offset(instant):
    """No guessed transition hour: exclude Saturday through Monday UTC."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        reject('CLOCK_INVALID')
    instant = instant.astimezone(timezone.utc)
    if instant.year != 2026:
        reject('POLICY_EXPIRED')
    boundaries = []
    for month in (3, 10):
        day = calendar.monthrange(instant.year, month)[1]
        end = datetime(instant.year, month, day, tzinfo=timezone.utc)
        sunday = end - timedelta(days=(end.weekday() + 1) % 7)
        if sunday - timedelta(days=1) <= instant < sunday + timedelta(days=2):
            reject('DST_UNCERTAIN')
        boundaries.append(sunday)
    return 10800 if boundaries[0] < instant < boundaries[1] else 7200


def validate_policy(policy, now, expected_server, symbol):
    if not isinstance(policy, dict):
        reject('POLICY_MISSING')
    expected = {'schema': SCHEMA, 'revision': POLICY_REVISION,
                'scope': 'READ_ONLY_DIAGNOSTIC', 'year': 2026,
                'server_fingerprint': server_fingerprint(expected_server),
                'symbol': symbol}
    if not expected_server or any(policy.get(k) != v for k, v in expected.items()):
        reject('POLICY_BINDING_MISMATCH')
    for key in ('package_version', 'python_version'):
        if not isinstance(policy.get(key), str) or not policy[key].strip():
            reject('POLICY_INCOMPLETE')
    if type(policy.get('terminal_build')) is not int or policy['terminal_build'] <= 0:
        reject('POLICY_INCOMPLETE')
    if type(policy.get('year')) is not int:
        reject('POLICY_INCOMPLETE')
    seasonal_offset(now)
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def evaluate(samples, policy, clock_checks, now, expected_login, expected_server,
             symbol, previous=None, diagnostics=None):
    """Validate a bounded batch; measured offset corroborates, never fits a correction.

    Only this CLI-specific boundary validates a private normalized snapshot copy.
    The original snapshot, shared validator and proposal path remain unmodified.
    """
    digest = validate_policy(policy, now, expected_server, symbol)
    offset = seasonal_offset(now)
    if previous is not None:
        if (not isinstance(previous, dict) or previous.get('blocked') is not False
                or previous.get('policy_digest') != digest
                or previous.get('offset_seconds') != offset):
            reject('PREVIOUS_STATE_CHANGED_OR_BLOCKED')
    if not isinstance(samples, list) or not 3 <= len(samples) <= 5:
        reject('SAMPLES_MISSING')
    if not isinstance(clock_checks, list) or len(clock_checks) != 2:
        reject('CLOCK_EVIDENCE_MISSING')
    uncertainty = 0.0
    for check in clock_checks:
        if not isinstance(check, dict) or check.get('status') != 'MEASURED':
            reject('CLOCK_UNCERTAIN')
        value = check.get('uncertainty_seconds')
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2:
            reject('CLOCK_UNCERTAIN')
        uncertainty = max(uncertainty, value)
        allowed_sources = (['time.windows.com', 'time.cloudflare.com'],
                           ['www.cloudflare.com', 'www.microsoft.com'])
        if check.get('sources') not in allowed_sources:
            reject('CLOCK_SOURCE_MISMATCH')
        expected_auth = ('UNAUTHENTICATED_NTP_TWO_SOURCES' if check['sources'] == allowed_sources[0]
                         else 'TLS_HTTPS_DATE_TWO_SOURCES')
        if check.get('authentication') not in (None, expected_auth):
            reject('CLOCK_SOURCE_MISMATCH')
        stamp = check.get('utc')
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 <= now.timestamp() - stamp <= 30:
            reject('CLOCK_EVIDENCE_STALE')
    first_call = samples[0].get('call_observations', {}).get('symbol_info_tick')
    last_call = samples[-1].get('call_observations', {}).get('symbol_info_tick')
    first_before, _ = validate_call_observation(first_call)
    _, last_after = validate_call_observation(last_call)
    if not clock_checks[0]['utc'] <= first_before <= last_after <= clock_checks[1]['utc']:
        reject('CLOCK_NOT_BRACKETED')
    wall = clock_checks[1]['utc'] - clock_checks[0]['utc']
    try:
        elapsed = clock_checks[1]['monotonic'] - clock_checks[0]['monotonic']
    except (KeyError, TypeError):
        reject('CLOCK_UNCERTAIN')
    if not math.isfinite(elapsed) or elapsed < 0 or abs(wall - elapsed) > .25:
        reject('HOST_CLOCK_DRIFT')
    rows = []
    raws = []
    for index, sample in enumerate(samples):
        runtime = sample.get('runtime_info', {})
        for key in ('server_fingerprint', 'package_version', 'python_version', 'terminal_build', 'symbol'):
            if runtime.get(key) != policy.get(key):
                reject('RUNTIME_IDENTITY_MISMATCH')
        tick = sample.get('symbol_info_tick', {}).get('data') or {}
        raw, millis = tick.get('time'), tick.get('time_msc')
        if type(raw) is not int or type(millis) is not int or raw <= 0 or millis // 1000 != raw:
            reject('TICK_FIELDS_INCONSISTENT')
        before, after = validate_call_observation(sample['call_observations']['symbol_info_tick'])
        if seasonal_offset(datetime.fromtimestamp(before - uncertainty, timezone.utc)) != offset or seasonal_offset(datetime.fromtimestamp(after + uncertainty, timezone.utc)) != offset:
            reject('OFFSET_CHANGED')
        normalized = millis - offset * 1000
        try:
            normalized_dt = datetime.fromtimestamp(normalized / 1000, timezone.utc)
        except (ValueError, OverflowError, OSError):
            reject('TICK_INVALID')
        if seasonal_offset(normalized_dt) != offset:
            reject('OFFSET_CHANGED')
        # A measured raw-minus-host range contains tick delivery age. It must
        # agree with the policy without rounding or inferring a timezone.
        low_age = before - uncertainty - normalized / 1000
        high_age = after + uncertainty - normalized / 1000
        final_age = now.timestamp() - normalized / 1000
        row = {'raw_time': raw, 'raw_time_msc': millis,
               'host_utc_before': before, 'host_utc_after': after,
               'expected_offset_seconds': offset,
               'measured_raw_minus_host_seconds': millis / 1000 - (before + after) / 2,
               'offset_evidence': 'SEASONAL_POLICY_CHECK_PENDING',
               'normalized_utc_candidate': normalized_dt.isoformat(),
               'normalized_utc': None, 'age_seconds': final_age,
               'age_uncertainty_seconds': uncertainty, 'freshness': 'BLOCKED',
               'policy_revision': POLICY_REVISION}
        if diagnostics is not None:
            diagnostics[index] = row
        if low_age < 0 or high_age > 30 or final_age - uncertainty < 0 or final_age + uncertainty > 30:
            reject('OFFSET_MISMATCH_OR_TICK_STALE_OR_FUTURE')
        for method in ('copy_ticks_from', 'copy_ticks_range'):
            evidence = sample.get('tick_crosscheck', {}).get(method, {})
            if evidence.get('matched') is not True:
                reject('CROSS_API_TICK_UNPROVEN')
            start, end = date(evidence.get('utc_from')), date(evidence.get('utc_to'))
            if not start <= normalized_dt <= end:
                reject('CROSS_API_WINDOW_MISMATCH')
            validate_call_observation(sample['call_observations'].get(method))
        snapshot = copy.deepcopy(sample)
        snapshot['symbol_info_tick']['data'].update(time=normalized // 1000, time_msc=normalized)
        validate_snapshot(snapshot, {'mt5_symbol': symbol}, expected_login, expected_server, now)
        raws.append(millis)
        rows.append(row)
    if len(set(raws)) < 3 or any(b < a for a, b in zip(raws, raws[1:])) or last_after - first_before < 2:
        reject('DISTINCT_TICKS_INSUFFICIENT')
    for row in rows:
        row.update(normalized_utc=row['normalized_utc_candidate'], freshness='PASS',
                   offset_evidence='SEASONAL_POLICY_AND_DISTINCT_TICKS')
    return {'samples': rows, 'result': 'PASS_READ_ONLY_ONLY', 'offset_seconds': offset,
            'policy_digest': digest, 'blocked': False, 'policy_revision': POLICY_REVISION}
