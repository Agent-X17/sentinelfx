"""Strict MT5 timestamp verification; never infer or silently apply an offset."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import math
import re

from .domain import InvalidData, date

MAX_TICK_AGE_SECONDS = 30
MAX_CLOCK_STEP_SECONDS = 0.25
MAX_EPOCH_SECONDS = 253402300799
TIMESTAMP_POLICY_SCHEMA = "sentinelfx.mt5.timestamp-policy.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _epoch_integer(value, maximum):
    return type(value) is int and 0 < value <= maximum


def _iso(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(timespec="milliseconds")


def _policy_correction(policy, context, raw_millis):
    """Accept only a complete policy independently binding this exact runtime.

    SentinelFX has no environment, webhook, database, or HTTP loader for these
    policies. A stable observed difference or server-clock webpage is not enough.
    """
    if not isinstance(policy, dict):
        raise InvalidData("MT5_TIMESTAMP_POLICY_MISSING")
    if policy.get("schema") != TIMESTAMP_POLICY_SCHEMA or policy.get("status") != "INDEPENDENTLY_VERIFIED":
        raise InvalidData("MT5_TIMESTAMP_POLICY_UNVERIFIED")
    if type(policy.get("revision")) is not int or policy["revision"] < 1:
        raise InvalidData("MT5_TIMESTAMP_POLICY_INVALID")
    for key in ("policy_id", "reviewed_by", "source_url", "source_published_at",
                "source_retrieved_at", "source_sha256", "broker_server_sha256",
                "package_version", "symbol", "timestamp_semantics"):
        if not isinstance(policy.get(key), str) or not policy[key].strip():
            raise InvalidData("MT5_TIMESTAMP_POLICY_INCOMPLETE")
    if not policy["source_url"].startswith("https://") or not _SHA256.fullmatch(policy["source_sha256"]):
        raise InvalidData("MT5_TIMESTAMP_POLICY_INVALID")
    if not _SHA256.fullmatch(policy["broker_server_sha256"]):
        raise InvalidData("MT5_TIMESTAMP_POLICY_INVALID")
    if policy["timestamp_semantics"] != "MT5_PYTHON_TICK_EPOCH_OFFSET_INDEPENDENTLY_CONFIRMED":
        raise InvalidData("MT5_TIMESTAMP_POLICY_UNVERIFIED")
    for key in ("source_published_at", "source_retrieved_at"):
        date(policy[key])
    dst = policy.get("dst_policy")
    if not isinstance(dst, dict) or dst.get("status") != "DOCUMENTED" or not dst.get("timezone_name"):
        raise InvalidData("MT5_TIMESTAMP_POLICY_DST_AMBIGUOUS")
    if not isinstance(context, dict):
        raise InvalidData("MT5_TIMESTAMP_CONTEXT_MISSING")
    for key in ("broker_server_sha256", "package_version", "terminal_build", "symbol"):
        if context.get(key) != policy.get(key):
            raise InvalidData("MT5_TIMESTAMP_POLICY_BINDING_MISMATCH")
    if type(policy.get("terminal_build")) is not int or policy["terminal_build"] <= 0:
        raise InvalidData("MT5_TIMESTAMP_POLICY_INVALID")
    intervals = policy.get("validity_intervals")
    if not isinstance(intervals, list) or not intervals:
        raise InvalidData("MT5_TIMESTAMP_POLICY_DST_AMBIGUOUS")
    matches = []
    for interval in intervals:
        if not isinstance(interval, dict) or type(interval.get("offset_seconds")) is not int:
            raise InvalidData("MT5_TIMESTAMP_POLICY_INVALID")
        if interval.get("dst_state") not in ("STANDARD", "DAYLIGHT"):
            raise InvalidData("MT5_TIMESTAMP_POLICY_DST_AMBIGUOUS")
        start, end = date(interval.get("utc_start")), date(interval.get("utc_end"))
        if start >= end:
            raise InvalidData("MT5_TIMESTAMP_POLICY_INVALID")
        normalized = datetime.fromtimestamp(raw_millis / 1000 - interval["offset_seconds"], timezone.utc)
        if start <= normalized < end:
            matches.append((interval["offset_seconds"], normalized))
    if len(matches) != 1:
        raise InvalidData("MT5_TIMESTAMP_POLICY_DST_AMBIGUOUS")
    offset, normalized = matches[0]
    prior = context.get("last_verified_offset_seconds")
    if prior is not None and (type(prior) is not int or prior != offset):
        raise InvalidData("MT5_TIMESTAMP_OFFSET_CHANGED")
    return offset, int(normalized.timestamp() * 1000), policy["policy_id"], policy["revision"]


def tick_time_evidence(tick, now, policy=None, context=None, call_observation=None):
    """Return allowlisted diagnostics while keeping raw and normalized time separate."""
    result = {
        "raw_time": None, "raw_time_msc": None,
        "documented_utc_candidate": None, "normalized_utc": None,
        "trusted_offset_status": "UNAVAILABLE_NO_INDEPENDENT_BROKER_EVIDENCE",
        "applied_offset_seconds": 0, "age_seconds": None,
        "raw_minus_call_midpoint_seconds": None,
        "freshness": "BLOCKED", "code": "MT5_TICK_TIMESTAMP_INVALID",
        "policy_id": None, "policy_revision": None,
    }
    if not isinstance(tick, dict):
        return result
    seconds, millis = tick.get("time"), tick.get("time_msc")
    seconds_ok = _epoch_integer(seconds, MAX_EPOCH_SECONDS)
    millis_ok = _epoch_integer(millis, MAX_EPOCH_SECONDS * 1000)
    if seconds_ok:
        result["raw_time"] = seconds
    if millis_ok:
        result["raw_time_msc"] = millis
    if not seconds_ok or not millis_ok:
        return result
    if millis // 1000 != seconds:
        result["code"] = "MT5_TICK_TIMESTAMP_INCONSISTENT"
        return result
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        result["code"] = "MT5_CLOCK_INVALID"
        return result
    result["documented_utc_candidate"] = _iso(millis)
    if call_observation is not None:
        try:
            before, after = validate_call_observation(call_observation)
            result["raw_minus_call_midpoint_seconds"] = float(
                Decimal(millis) / 1000 - Decimal(str((before + after) / 2)))
        except InvalidData as exc:
            result["code"] = str(exc)
            return result
    normalized_millis = millis
    documented_age = Decimal(str(now.timestamp())) - Decimal(millis) / 1000
    if not 0 <= documented_age <= MAX_TICK_AGE_SECONDS:
        if policy is None:
            result["age_seconds"] = float(documented_age)
            result["code"] = "MT5_TICK_STALE_OR_FUTURE"
            return result
        try:
            offset, normalized_millis, policy_id, revision = _policy_correction(policy, context, millis)
        except InvalidData as exc:
            result["age_seconds"] = float(documented_age)
            result["code"] = str(exc)
            return result
        result.update(trusted_offset_status="INDEPENDENTLY_VERIFIED_VERSIONED_POLICY",
                      applied_offset_seconds=offset, policy_id=policy_id,
                      policy_revision=revision)
    age = Decimal(str(now.timestamp())) - Decimal(normalized_millis) / 1000
    result["age_seconds"] = float(age)
    if not 0 <= age <= MAX_TICK_AGE_SECONDS:
        result["code"] = "MT5_TICK_STALE_OR_FUTURE"
        return result
    result.update(normalized_utc=_iso(normalized_millis), freshness="PASS", code="MT5_TICK_TIME_OK")
    return result


def validate_call_observation(observation):
    if not isinstance(observation, dict):
        raise InvalidData("MT5_CALL_CLOCK_EVIDENCE_MISSING")
    values = []
    for field in ("utc_before", "utc_after", "monotonic_elapsed"):
        value = observation.get(field)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise InvalidData("MT5_CALL_CLOCK_EVIDENCE_INVALID")
        values.append(value)
    before, after, elapsed = values
    if not 0 < before <= MAX_EPOCH_SECONDS or not 0 < after <= MAX_EPOCH_SECONDS:
        raise InvalidData("MT5_CALL_CLOCK_EVIDENCE_INVALID")
    if elapsed < 0 or elapsed > MAX_TICK_AGE_SECONDS or after < before:
        raise InvalidData("MT5_CALL_CLOCK_DRIFT")
    if abs((after - before) - elapsed) > MAX_CLOCK_STEP_SECONDS:
        raise InvalidData("MT5_CALL_CLOCK_DRIFT")
    return before, after


def validate_clock_observation(observation, now, captured_at):
    """Detect host clock steps during collection, not absolute UTC accuracy."""
    if not isinstance(observation, dict):
        raise InvalidData("MT5_CLOCK_EVIDENCE_MISSING")
    values = []
    for field in ("wall_start", "wall_end", "monotonic_elapsed"):
        value = observation.get(field)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise InvalidData("MT5_CLOCK_EVIDENCE_INVALID")
        values.append(value)
    start, end, elapsed = values
    if not 0 < start <= MAX_EPOCH_SECONDS or not 0 < end <= MAX_EPOCH_SECONDS:
        raise InvalidData("MT5_CLOCK_EVIDENCE_INVALID")
    if elapsed < 0 or elapsed > MAX_TICK_AGE_SECONDS or end < start:
        raise InvalidData("MT5_CLOCK_DRIFT")
    if abs((end - start) - elapsed) > MAX_CLOCK_STEP_SECONDS:
        raise InvalidData("MT5_CLOCK_DRIFT")
    if abs(captured_at.timestamp() - end) > MAX_CLOCK_STEP_SECONDS:
        raise InvalidData("MT5_CLOCK_SOURCE_INCONSISTENT")
    if not 0 <= now.timestamp() - end <= MAX_TICK_AGE_SECONDS:
        raise InvalidData("MT5_CLOCK_SOURCE_INCONSISTENT")


def server_fingerprint(server):
    """One-way identity binding for diagnostics; never expose the server name."""
    if not isinstance(server, str) or not server:
        return None
    return hashlib.sha256(server.encode("utf-8")).hexdigest()
