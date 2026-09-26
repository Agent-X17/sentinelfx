"""Versioned HFM broker-time normalization for local DEMO validation only."""
import copy
import hashlib
import json
from datetime import datetime, timezone

from .domain import InvalidData, date
from .hfm_readonly_time import seasonal_offset
from .mt5_evidence import validate_snapshot
from .mt5_time import server_fingerprint, validate_call_observation

SCHEMA = "sentinelfx.hfm-demo-policy.v2"
POLICY_REVISION = "hfm-demo-2026-v2"
REPORT_SCHEMA = "sentinelfx.hfm-demo-level1-report.v2"


def _reject(code):
    raise InvalidData("HFM_DEMO_" + code)


def account_fingerprint(login, server):
    if not login or not server:
        _reject("IDENTITY_MISSING")
    return hashlib.sha256((str(login) + "|" + str(server)).encode()).hexdigest()


def validate_policy(policy, now, expected_login, expected_server, symbol):
    if not isinstance(policy, dict):
        _reject("POLICY_MISSING")
    expected = {
        "schema": SCHEMA,
        "revision": POLICY_REVISION,
        "scope": "DEMO_ONLY_VALIDATION",
        "year": 2026,
        "server_fingerprint": server_fingerprint(expected_server),
        "account_fingerprint": account_fingerprint(expected_login, expected_server),
        "symbol": symbol,
        "summer_offset_seconds": 10800,
        "winter_offset_seconds": 7200,
        "dst_start": "LAST_SUNDAY_OF_MARCH",
        "dst_end": "LAST_SUNDAY_OF_OCTOBER",
    }
    if any(policy.get(key) != value for key, value in expected.items()):
        _reject("POLICY_BINDING_MISMATCH")
    for key in ("package_version", "python_version", "hfm_evidence_date", "mql5_evidence_url"):
        if not isinstance(policy.get(key), str) or not policy[key].strip():
            _reject("POLICY_INCOMPLETE")
    if type(policy.get("terminal_build")) is not int or policy["terminal_build"] <= 0:
        _reject("POLICY_INCOMPLETE")
    seasonal_offset(now)
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evaluate(samples, policy, now, expected_login, expected_server, symbol,
             previous=None, require_autotrading=False, external_clock_status="EXTERNAL_CLOCK_NOT_CERTIFIED"):
    """Normalize only after every DEMO identity, policy, sample and age gate passes."""
    digest = validate_policy(policy, now, expected_login, expected_server, symbol)
    offset = seasonal_offset(now)
    if previous is not None:
        if (not isinstance(previous, dict) or previous.get("blocked") is not False
                or previous.get("policy_digest") != digest
                or previous.get("offset_seconds") != offset):
            _reject("PREVIOUS_STATE_CHANGED_OR_BLOCKED")
    if not isinstance(samples, list) or not 3 <= len(samples) <= 5:
        _reject("SAMPLES_MISSING")

    rows, raws, normalized_samples = [], [], []
    for sample in samples:
        runtime = sample.get("runtime_info") or {}
        for key in ("server_fingerprint", "package_version", "python_version", "terminal_build", "symbol"):
            if runtime.get(key) != policy.get(key):
                _reject("RUNTIME_IDENTITY_MISMATCH")
        tick = ((sample.get("symbol_info_tick") or {}).get("data") or {})
        raw, millis = tick.get("time"), tick.get("time_msc")
        if type(raw) is not int or type(millis) is not int or raw <= 0 or millis // 1000 != raw:
            _reject("TICK_FIELDS_INCONSISTENT")
        before, after = validate_call_observation((sample.get("call_observations") or {}).get("symbol_info_tick"))
        try:
            normalized_millis = millis - offset * 1000
            normalized_epoch = normalized_millis / 1000
            normalized_dt = datetime.fromtimestamp(normalized_epoch, timezone.utc)
        except (ValueError, OverflowError, OSError):
            _reject("TICK_INVALID")
        if seasonal_offset(normalized_dt) != offset:
            _reject("OFFSET_CHANGED")
        age_at_call_low = before - normalized_epoch
        age_at_call_high = after - normalized_epoch
        age = now.timestamp() - normalized_epoch
        if age_at_call_low < 0 or age_at_call_high > 30 or age < 0 or age > 30:
            _reject("TICK_STALE_OR_FUTURE")
        for method in ("copy_ticks_from", "copy_ticks_range"):
            evidence = (sample.get("tick_crosscheck") or {}).get(method) or {}
            if evidence.get("matched") is not True:
                _reject("CROSS_API_TICK_UNPROVEN")
            try:
                start, end = date(evidence.get("utc_from")), date(evidence.get("utc_to"))
            except InvalidData:
                _reject("CROSS_API_WINDOW_INVALID")
            if not start <= normalized_dt <= end:
                _reject("CROSS_API_WINDOW_MISMATCH")
            validate_call_observation((sample.get("call_observations") or {}).get(method))

        normalized = copy.deepcopy(sample)
        normalized["symbol_info_tick"]["data"].update(
            time=normalized_millis // 1000, time_msc=normalized_millis)
        summary = validate_snapshot(
            normalized, {"mt5_symbol": symbol}, expected_login, expected_server, now,
            expected_terminal_trade_allowed=require_autotrading,
            maximum_balance_equity="500",
        )
        rows.append({
            "raw_time": raw,
            "raw_time_msc": millis,
            "normalized_utc": normalized_dt.isoformat(),
            "applied_offset_seconds": offset,
            "measured_raw_minus_call_midpoint_seconds": millis / 1000 - (before + after) / 2,
            "policy_revision": POLICY_REVISION,
            "expected_server_fingerprint": policy["server_fingerprint"],
            "runtime_server_fingerprint": runtime["server_fingerprint"],
            "account_identity_fingerprint": policy["account_fingerprint"],
            "package_version": runtime["package_version"],
            "terminal_build": runtime["terminal_build"],
            "symbol": symbol,
            "age_seconds": age,
            "freshness": "PASS",
            "final_allow_block_reason": "DEMO_LEVEL1_SAMPLE_PASSED",
        })
        raws.append(millis)
        normalized_samples.append(normalized)
    if len(set(raws)) < 3 or any(right <= left for left, right in zip(raws, raws[1:])):
        _reject("MARKET_CLOSED_OR_NO_ADVANCING_TICK")
    return {
        "schema": REPORT_SCHEMA,
        "result": "PASS_DEMO_LEVEL1_ONLY",
        "final_reason": "ALL_DEMO_LEVEL1_GATES_PASSED",
        "blocked": False,
        "offset_seconds": offset,
        "policy_revision": POLICY_REVISION,
        "policy_digest": digest,
        "external_clock_status": external_clock_status,
        "externally_clock_certified": external_clock_status == "CERTIFIED",
        "samples": rows,
        "verified_snapshot_summary": summary,
        "normalized_snapshot": normalized_samples[-1],
    }


def report_safe(result):
    value = copy.deepcopy(result)
    value.pop("normalized_snapshot", None)
    return value
