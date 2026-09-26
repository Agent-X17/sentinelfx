"""Local CLI-only HFM Level 1 DEMO timestamp and account verifier."""
import argparse
import json
from pathlib import Path
import time

from engine.config import Settings
from engine.domain import InvalidData, utcnow
from engine.hfm_demo_time import evaluate, report_safe
from engine.isolated_mt5 import IsolatedMT5Service
from scripts.hfm_readonly import atomic_json, clock_check

ROOT = Path(__file__).resolve().parent.parent


def run(args, settings):
    report = {"schema": "sentinelfx.hfm-demo-level1-report.v2",
              "result": "BLOCKED_NO_TRADE", "final_reason": None,
              "order_sent": False, "level": "DEMO_LEVEL1_ONLY",
              "externally_clock_certified": False,
              "external_clock_status": "EXTERNAL_CLOCK_NOT_CERTIFIED"}
    service = None
    try:
        policy_path = Path(args.hfm_policy).resolve()
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        state_path = policy_path.with_suffix(policy_path.suffix + ".demo-state.json")
        previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
        external_status = "EXTERNAL_CLOCK_NOT_CERTIFIED"
        try:
            first = clock_check()
        except InvalidData:
            first = None
        service = IsolatedMT5Service(True, settings.mt5_terminal_path)
        snapshots = []
        for index in range(args.time_samples):
            if index:
                time.sleep(1)
            snapshots.append(service.snapshot(args.symbol, timestamp_reads=True))
            print("DEMO Level 1 sample:", index + 1, "of", args.time_samples)
        try:
            second = clock_check()
            if first and second:
                external_status = "CERTIFIED"
        except InvalidData:
            pass
        result = evaluate(snapshots, policy, utcnow(), settings.demo_expected_account_login,
                          settings.demo_expected_broker_server, args.symbol, previous,
                          require_autotrading=args.require_autotrading,
                          external_clock_status=external_status)
        report = report_safe(result)
        report.update(order_sent=False, level="DEMO_LEVEL1_ONLY")
        atomic_json(state_path, {key: result[key] for key in
                    ("blocked", "policy_digest", "offset_seconds", "policy_revision")})
    except Exception as exc:
        report["final_reason"] = str(exc) if isinstance(exc, InvalidData) else "HFM_DEMO_EVIDENCE_INVALID"
    finally:
        if service is not None:
            service.shutdown()
    report["generated_at"] = utcnow().isoformat()
    atomic_json(Path(args.report_file).resolve(), report)
    for row in report.get("samples", []):
        print("Raw MT5 time / time_msc:", row["raw_time"], "/", row["raw_time_msc"])
        print("Applied seasonal offset seconds:", row["applied_offset_seconds"])
        print("Measured raw-minus-call-midpoint seconds:", round(row["measured_raw_minus_call_midpoint_seconds"], 3))
        print("Normalized UTC:", row["normalized_utc"])
        print("Calculated age seconds:", round(row["age_seconds"], 3))
        print("Freshness (30-second limit):", row["freshness"])
    passed = report["result"] == "PASS_DEMO_LEVEL1_ONLY"
    print("External clock:", report.get("external_clock_status"))
    print("RESULT:", "PASS — DEMO LEVEL 1 ONLY" if passed else "BLOCKED / NO_TRADE")
    if not passed:
        print("REASON:", report["final_reason"])
    print("No order was sent.")
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--hfm-policy", required=True)
    parser.add_argument("--report-file", required=True)
    parser.add_argument("--time-samples", type=int, choices=(3, 4, 5), default=5)
    parser.add_argument("--require-autotrading", action="store_true")
    args = parser.parse_args()
    return run(args, Settings.from_env(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
