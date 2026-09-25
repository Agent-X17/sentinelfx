"""Strict documented-UTC tick interpretation; never infer a broker offset.

The terminal's tick and the host clock are not independent evidence of a
broker-to-UTC offset. A future tick remains blocked even if the difference is
stable or exactly a timezone interval. See docs/MT5_TIMESTAMP_INVESTIGATION.md.
"""
from datetime import datetime, timezone
from decimal import Decimal
import math

from .domain import InvalidData

MAX_TICK_AGE_SECONDS = 30
MAX_CLOCK_STEP_SECONDS = 0.25
# Bound all values before formatting diagnostics. Invalid strings are never echoed.
MAX_EPOCH_SECONDS = 253402300799


def _epoch_integer(value, maximum):
    return type(value) is int and 0 < value <= maximum


def _iso(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(timespec="milliseconds")


def tick_time_evidence(tick, now):
    """Return only allowlisted diagnostics, without mutating the raw tick.

    `documented_utc_candidate` is an interpretation, not a trusted correction.
    A normalized value is published only after consistency and freshness pass.
    There is deliberately no input for an offset, timezone or 'trusted' flag.
    """
    result = {
        "raw_time": None, "raw_time_msc": None,
        "documented_utc_candidate": None, "normalized_utc": None,
        "trusted_offset_status": "UNAVAILABLE_NO_INDEPENDENT_BROKER_EVIDENCE",
        "applied_offset_seconds": 0, "age_seconds": None,
        "freshness": "BLOCKED", "code": "MT5_TICK_TIMESTAMP_INVALID",
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
    # Both native fields are mandatory. Never fall back from malformed millis.
    if not seconds_ok or not millis_ok:
        return result
    if millis // 1000 != seconds:
        result["code"] = "MT5_TICK_TIMESTAMP_INCONSISTENT"
        return result
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        result["code"] = "MT5_CLOCK_INVALID"
        return result
    age = Decimal(str(now.timestamp())) - Decimal(millis) / 1000
    result["documented_utc_candidate"] = _iso(millis)
    result["age_seconds"] = float(age)
    if not 0 <= age <= MAX_TICK_AGE_SECONDS:
        result["code"] = "MT5_TICK_STALE_OR_FUTURE"
        return result
    result.update(normalized_utc=result["documented_utc_candidate"],
                  freshness="PASS", code="MT5_TICK_TIME_OK")
    return result


def validate_clock_observation(observation, now, captured_at):
    """Detect host clock steps during collection, not absolute UTC accuracy.

    Monotonic time is an independent *elapsed-time* measurement only. Neither
    these observations nor a Windows NTP source name authorize a broker offset.
    """
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
