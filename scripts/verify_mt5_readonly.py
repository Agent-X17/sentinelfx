#!/usr/bin/env python3
"""Redaction-safe Windows MT5 read-only verification. Never submits an order."""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

from engine.config import Settings
from engine.domain import InvalidData, utcnow, date
from engine.isolated_mt5 import IsolatedMT5Service,SnapshotMT5
from engine.mt5_evidence import validate_order_request,validate_snapshot
from engine.mt5_time import tick_time_evidence, validate_call_observation, validate_clock_observation


def blocked(code):
    print("RESULT: BLOCKED / NO_TRADE")
    print("REASON:",code)
    print("No order was sent.")
    return 1


def print_tick_time(data):
    """Print only numeric timestamps and fixed labels, never the raw envelope."""
    envelope = data.get("symbol_info_tick") if isinstance(data, dict) else None
    tick = envelope.get("data") if isinstance(envelope, dict) and envelope.get("ok") is True else None
    now = utcnow()
    calls = data.get("call_observations") if isinstance(data, dict) else None
    tick_call = calls.get("symbol_info_tick") if isinstance(calls, dict) else None
    timing = tick_time_evidence(tick, now, call_observation=tick_call)
    try:
        validate_clock_observation(data.get("clock_observation"), now, date(data.get("captured_at")))
        clock_status = "CONSISTENT_ELAPSED_TIME_ONLY"
    except (InvalidData, AttributeError):
        clock_status = "BLOCKED"
        timing.update(normalized_utc=None, freshness="BLOCKED")
    print("Collection clock consistency:", clock_status)
    try:
        before, after = validate_call_observation(tick_call)
        before_text = datetime.fromtimestamp(before, timezone.utc).isoformat(timespec="milliseconds")
        after_text = datetime.fromtimestamp(after, timezone.utc).isoformat(timespec="milliseconds")
    except InvalidData:
        before_text = after_text = "UNAVAILABLE"
    print("Windows UTC immediately before MT5 tick call:", before_text)
    print("Windows UTC immediately after MT5 tick call:", after_text)
    print("Raw tick time (seconds):", timing["raw_time"] if timing["raw_time"] is not None else "UNAVAILABLE")
    print("Raw tick time_msc (milliseconds):", timing["raw_time_msc"] if timing["raw_time_msc"] is not None else "UNAVAILABLE")
    print("Trusted offset status:", timing["trusted_offset_status"])
    print("Applied offset seconds:", timing["applied_offset_seconds"])
    print("Documented UTC candidate:", timing["documented_utc_candidate"] or "UNAVAILABLE")
    print("Normalized UTC tick timestamp:", timing["normalized_utc"] or "UNAVAILABLE — TIMESTAMP NOT VERIFIED")
    print("Tick age seconds:", timing["age_seconds"] if timing["age_seconds"] is not None else "UNAVAILABLE")
    print("Raw tick minus call midpoint seconds:", timing["raw_minus_call_midpoint_seconds"]
          if timing["raw_minus_call_midpoint_seconds"] is not None else "UNAVAILABLE")
    print("Timestamp freshness (30-second limit):", timing["freshness"])
    print("Timestamp reason:", timing["code"])
    print("Clock note: host clock is not independent proof of broker timestamp semantics.")
    runtime = data.get("runtime_info") if isinstance(data, dict) else None
    runtime = runtime if isinstance(runtime, dict) else {}
    print("MT5 Python package version:", runtime.get("package_version") or "UNAVAILABLE")
    print("MT5 terminal build:", runtime.get("terminal_build") or "UNAVAILABLE")
    fingerprint = runtime.get("server_fingerprint")
    print("Broker server fingerprint:", fingerprint[:16] if isinstance(fingerprint, str) else "UNAVAILABLE")
    print("Exact symbol:", runtime.get("symbol") or "UNAVAILABLE")
    print("Independent broker-visible time reference: UNAVAILABLE")
    return {
        "host_utc_before": before_text, "host_utc_after": after_text,
        "raw_time": timing["raw_time"], "raw_time_msc": timing["raw_time_msc"],
        "raw_minus_call_midpoint_seconds": timing["raw_minus_call_midpoint_seconds"],
        "documented_utc_candidate": timing["documented_utc_candidate"],
        "normalized_utc": timing["normalized_utc"], "freshness": timing["freshness"],
        "reason": timing["code"], "trusted_offset_status": timing["trusted_offset_status"],
        "runtime": {"package_version": runtime.get("package_version"),
                    "terminal_build": runtime.get("terminal_build"),
                    "server_fingerprint": fingerprint,
                    "symbol": runtime.get("symbol")},
    }


def summarize_samples(samples):
    raw = [item.get("raw_time_msc") for item in samples]
    offsets = [item.get("raw_minus_call_midpoint_seconds") for item in samples]
    valid_raw = len(raw) == len(samples) and all(type(value) is int for value in raw)
    nondecreasing = valid_raw and all(right >= left for left, right in zip(raw, raw[1:]))
    if len(raw) == 1 and valid_raw:
        progression = "SINGLE_SAMPLE"
    elif nondecreasing and any(right > left for left, right in zip(raw, raw[1:])):
        progression = "ADVANCING"
    else:
        progression = "BLOCKED_NOT_ADVANCING"
    usable = [value for value in offsets if type(value) in (int, float)]
    spread = max(usable) - min(usable) if len(usable) == len(samples) and usable else None
    # Re-reading an identical last tick measures elapsed polling time, not a
    # change in broker offset. Even distinct ticks have unknown delivery age.
    status = "UNVERIFIED_TICK_DELIVERY_AGE_UNKNOWN"
    if not valid_raw or spread is None:
        status = "UNAVAILABLE_INVALID_EVIDENCE"
    elif len(raw) < 2:
        status = "UNVERIFIED_SINGLE_SAMPLE"
    elif len(set(raw)) == 1:
        status = "UNVERIFIED_SAME_TICK_REPEATED"
    elif not nondecreasing:
        status = "UNVERIFIED_TICK_TIME_REGRESSED"
    print("Repeated tick progression:", progression)
    print("Apparent offset stability:", status)
    print("Apparent offset spread seconds:", round(spread, 6) if spread is not None else "UNAVAILABLE")
    print("Offset use: NONE — observation is not independent proof.")
    if status == "UNVERIFIED_SAME_TICK_REPEATED":
        print("Sampling note: the same tick was read repeatedly; spread reflects elapsed polling time, not proven offset drift.")
    return {"tick_progression": progression,
            "apparent_offset_status": status, "apparent_offset_spread_seconds": spread,
            "trusted_for_normalization": False}


def main():
    parser=argparse.ArgumentParser(description="Verify redacted read-only MT5 demo evidence")
    parser.add_argument("--symbol",required=True,help="Exact MT5 Market Watch symbol")
    parser.add_argument("--order-check",action="store_true",help="Also run one non-submitting minimum-volume order_check")
    parser.add_argument("--time-samples", type=int, choices=range(1, 6), default=1,
                        help="Collect 1–5 read-only snapshots; no offset is learned")
    parser.add_argument("--report-file", help="Write a redacted JSON support report")
    parser.add_argument("--hfm-policy", help="Explicit local HFM read-only identity/version policy JSON")
    args=parser.parse_args()
    if args.time_samples != 1 and args.order_check:
        parser.error("--time-samples cannot be combined with --order-check")
    service=None
    try:
        settings=Settings.from_env(ROOT)
        if settings.demo_trade_proposals_enabled or not settings.demo_trade_proposal_kill_switch:
            return blocked("SAFE_DEFAULTS_NOT_ACTIVE")
        if settings.live_execution_enabled or settings.mode!="SIMULATION" or settings.mt5_diagnostic_mode!="real":
            return blocked("READ_ONLY_MODE_NOT_CONFIGURED")
        if not settings.demo_expected_account_login or not settings.demo_expected_broker_server:
            return blocked("EXPECTED_DEMO_IDENTITY_NOT_CONFIGURED")
        if args.hfm_policy:
            from scripts.hfm_readonly import run
            return run(args, settings)
        service=IsolatedMT5Service(True,settings.mt5_terminal_path)
        first_failure = None
        diagnostic_samples = []
        for index in range(args.time_samples):
            if index:
                time.sleep(1)
            print("Time sample:", index + 1, "of", args.time_samples)
            data=service.snapshot(args.symbol)
            diagnostic_samples.append(print_tick_time(data))
            try:
                summary=validate_snapshot(data,{"mt5_symbol":args.symbol},settings.demo_expected_account_login,settings.demo_expected_broker_server)
            except InvalidData as exc:
                # A later passing sample cannot erase an earlier failure.
                first_failure = first_failure or str(exc)
                print("Sample result: BLOCKED / NO_TRADE")
        repeated = summarize_samples(diagnostic_samples)
        if args.time_samples > 1 and repeated["tick_progression"] != "ADVANCING":
            first_failure = first_failure or "MT5_TICK_NOT_PROGRESSING"
        if args.report_file:
            report = {"schema":"sentinelfx.mt5.timestamp-diagnostic.v1",
                      "generated_at":utcnow().isoformat(), "read_only":True,
                      "order_sent":False, "offset_applied":False,
                      "broker_policy_reference":"UNAVAILABLE_NOT_RUNTIME_BOUND",
                      "samples":diagnostic_samples, "repeated_analysis":repeated,
                      "result":"BLOCKED_NO_TRADE" if first_failure else "PASS_READ_ONLY_ONLY",
                      "reason":first_failure}
            try:
                Path(args.report_file).write_text(json.dumps(report, indent=2, sort_keys=True)+"\n", encoding="utf-8")
            except OSError:
                print("Support report: SAVE_FAILED — choose an existing writable folder.")
                return blocked(first_failure or "MT5_REPORT_SAVE_FAILED")
            print("Redacted support report saved:", args.report_file)
        if first_failure:
            return blocked(first_failure)
        print("SNAPSHOT: PASS")
        print("Demo identity: MATCHED (value hidden)")
        print("Terminal: CONNECTED; Algo Trading: OFF")
        print("Symbol:",summary["symbol"])
        print("Fresh quote: YES")
        print("Open positions: 0")
        print("Pending orders: 0")
        print("Recent external exposure: 0")
        if args.order_check:
            frozen=SnapshotMT5(data); symbol=frozen.symbol_info(args.symbol).data; tick=frozen.symbol_info_tick(args.symbol).data
            point=Decimal(str(symbol["point"])); digits=int(symbol["digits"]); quantum=Decimal(10) ** -digits
            price=Decimal(str(tick["ask"])).quantize(quantum)
            distance=max(Decimal(int(symbol["trade_stops_level"])+10)*point,Decimal(100)*point)
            request={"symbol":args.symbol,"type":"BUY","volume":str(symbol["volume_min"]),"price":str(price),
                     "sl":str((price-distance).quantize(quantum)),"tp":str((price+distance*2).quantize(quantum)),
                     "filling_mode":symbol["filling_mode"]}
            validate_order_request(data,request)
            checked=service.read_only_order_check(request)
            if not checked.ok: return blocked(checked.code)
            after=service.snapshot(args.symbol)
            validate_snapshot(after,{"mt5_symbol":args.symbol},settings.demo_expected_account_login,settings.demo_expected_broker_server)
            print("ORDER_CHECK: PASS (non-submitting)")
            print("After order_check — open positions: 0; pending orders: 0")
        print("RESULT: PASS — READ-ONLY EVIDENCE ONLY")
        print("DEMO ORDER NOT SENT — EXECUTION IS NOT IMPLEMENTED.")
        return 0
    except InvalidData as exc:
        return blocked(str(exc))
    except Exception:
        return blocked("MT5_VERIFICATION_FAILED")
    finally:
        if service is not None: service.shutdown()


if __name__=="__main__": raise SystemExit(main())
