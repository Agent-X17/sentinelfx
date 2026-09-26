"""Create a versioned DEMO-only policy from the already reviewed v1 policy."""
import argparse
import json
from pathlib import Path

from engine.config import Settings
from engine.domain import utcnow
from engine.hfm_demo_time import (POLICY_REVISION, SCHEMA, account_fingerprint,
                                  validate_policy)
from engine.hfm_readonly_time import validate_policy as validate_v1
from engine.mt5_time import server_fingerprint

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-readonly-policy", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--hfm-evidence-date", required=True)
    args = parser.parse_args()
    settings = Settings.from_env(ROOT)
    source = json.loads(Path(args.source_readonly_policy).read_text(encoding="utf-8"))
    validate_v1(source, utcnow(), settings.demo_expected_broker_server, args.symbol)
    policy = {
        "schema": SCHEMA,
        "revision": POLICY_REVISION,
        "scope": "DEMO_ONLY_VALIDATION",
        "year": 2026,
        "server_fingerprint": server_fingerprint(settings.demo_expected_broker_server),
        "account_fingerprint": account_fingerprint(settings.demo_expected_account_login,
                                                    settings.demo_expected_broker_server),
        "symbol": args.symbol,
        "package_version": source["package_version"],
        "terminal_build": source["terminal_build"],
        "python_version": source["python_version"],
        "summer_offset_seconds": 10800,
        "winter_offset_seconds": 7200,
        "dst_start": "LAST_SUNDAY_OF_MARCH",
        "dst_end": "LAST_SUNDAY_OF_OCTOBER",
        "hfm_evidence_date": args.hfm_evidence_date,
        "mql5_evidence_url": "https://www.mql5.com/en/forum/516531#comment_60465455",
    }
    validate_policy(policy, utcnow(), settings.demo_expected_account_login,
                    settings.demo_expected_broker_server, args.symbol)
    target = Path(args.output).resolve()
    with target.open("x", encoding="utf-8") as stream:
        json.dump(policy, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print("DEMO-only policy created: YES")
    print("Policy revision:", POLICY_REVISION)
    print("No order was sent.")


if __name__ == "__main__":
    main()
