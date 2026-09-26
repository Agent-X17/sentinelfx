"""Explicit, one-shot, local CLI execution of one approved DEMO proposal."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from engine.config import Settings
from engine.domain import InvalidData, date, stamp, utcnow
from engine.hfm_demo_time import validate_policy
from engine.storage import Store, dumps, loads

ROOT = Path(__file__).resolve().parent.parent


def _safe_result(status, reason, order_sent=False):
    return {"status": status, "reason": reason, "order_sent": order_sent}


def execute(args, settings):
    attempt_id = str(uuid4())
    if not settings.demo_execution_gated:
        raise InvalidData("DEMO_EXECUTION_GATED_DISABLED")
    if args.confirm != "EXECUTE DEMO " + args.proposal_id:
        raise InvalidData("EXACT_DEMO_CONFIRMATION_REQUIRED")
    if Path(settings.demo_emergency_stop_path).exists():
        raise InvalidData("DEMO_EMERGENCY_STOP_ACTIVE")
    policy_path = Path(settings.hfm_demo_policy_path).resolve()
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    state_path = policy_path.with_suffix(policy_path.suffix + ".demo-state.json")
    if not state_path.is_file():
        raise InvalidData("HFM_DEMO_LEVEL1_STATE_MISSING")
    previous_state = json.loads(state_path.read_text(encoding="utf-8"))
    store = Store(settings.database_path)
    with store.transaction() as db:
        if not Store.verify_audit(db):
            raise InvalidData("AUDIT_INTEGRITY_FAILED")
        if db.execute("SELECT 1 FROM demo_execution_attempts WHERE status IN ('PREPARED','UNRESOLVED_RECONCILIATION_REQUIRED')").fetchone():
            raise InvalidData("DEMO_EXECUTION_UNRESOLVED_ATTEMPT_EXISTS")
        row = db.execute("SELECT * FROM demo_trade_proposals WHERE id=?", (args.proposal_id,)).fetchone()
        if not row or row["status"] != "APPROVED_FOR_FUTURE_DEMO_EXECUTION":
            raise InvalidData("DEMO_PROPOSAL_NOT_APPROVED")
        if date(row["expires_at"]) <= utcnow():
            raise InvalidData("DEMO_PROPOSAL_EXPIRED")
        proposal = loads(row["payload"])
        symbol = proposal.get("symbol")
        validate_policy(policy, utcnow(), settings.demo_expected_account_login,
                        settings.demo_expected_broker_server, symbol)
        required = ("direction", "exact_volume", "stop_loss", "take_profit")
        if any(proposal.get(key) in (None, "") for key in required):
            raise InvalidData("DEMO_PROPOSAL_INCOMPLETE")
        today = utcnow().date().isoformat()
        count = db.execute("SELECT COUNT(*) FROM demo_execution_attempts WHERE status='CONFIRMED_DEMO_EXECUTED' AND substr(created_at,1,10)=?", (today,)).fetchone()[0]
        if count >= settings.demo_max_trades_per_day:
            raise InvalidData("DEMO_MAX_TRADES_PER_DAY_REACHED")
        request = {"proposal_id": args.proposal_id, "symbol": symbol,
                   "direction": proposal["direction"], "volume": proposal["exact_volume"],
                   "sl": proposal["stop_loss"], "tp": proposal["take_profit"]}
        digest = hashlib.sha256(dumps(request).encode()).hexdigest()
        prepared = {"attempt_id": attempt_id, "proposal_id": args.proposal_id,
                    "status": "PREPARED", "request_digest": digest,
                    "order_sent": False, "created_at": stamp()}
        db.execute("INSERT INTO demo_execution_attempts VALUES(?,?,?,?,?,?,?)",
                   (attempt_id, args.proposal_id, "PREPARED", digest, dumps(prepared),
                    prepared["created_at"], prepared["created_at"]))
        db.execute("INSERT INTO demo_execution_history VALUES(?,?,?,?,?)",
                   (str(uuid4()), attempt_id, "PREPARED", dumps(prepared), prepared["created_at"]))
        Store.audit(db, "DEMO_EXECUTION_PREPARED", prepared)

    worker_input = {
        **request,
        "attempt_id": attempt_id,
        "terminal_path": settings.mt5_terminal_path,
        "expected_login": settings.demo_expected_account_login,
        "expected_server": settings.demo_expected_broker_server,
        "account_tag": settings.demo_account_tag,
        "policy": policy,
        "previous_state": previous_state,
        "external_clock_status": "EXTERNAL_CLOCK_NOT_CERTIFIED",
    }
    try:
        process = subprocess.run([sys.executable, "-B", "-m", "engine.demo_execution_worker"],
                                 input=json.dumps(worker_input), cwd=ROOT, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=25, check=True)
        result = json.loads(process.stdout)
        if result.get("status") not in ("BLOCKED_NO_TRADE", "CONFIRMED_DEMO_EXECUTED",
                                         "UNRESOLVED_RECONCILIATION_REQUIRED"):
            raise ValueError()
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        result = _safe_result("UNRESOLVED_RECONCILIATION_REQUIRED",
                              "DEMO_WORKER_OUTCOME_UNKNOWN", True)
    result.update(attempt_id=attempt_id, proposal_id=args.proposal_id,
                  request_digest=digest, updated_at=stamp())
    with store.transaction() as db:
        db.execute("UPDATE demo_execution_attempts SET status=?,payload=?,updated_at=? WHERE id=? AND status='PREPARED'",
                   (result["status"], dumps(result), result["updated_at"], attempt_id))
        db.execute("INSERT INTO demo_execution_history VALUES(?,?,?,?,?)",
                   (str(uuid4()), attempt_id, result["status"], dumps(result), result["updated_at"]))
        Store.audit(db, "DEMO_EXECUTION_" + result["status"], result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposal-id", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--report-file", required=True)
    args = parser.parse_args()
    try:
        result = execute(args, Settings.from_env(ROOT))
    except Exception as exc:
        result = _safe_result("BLOCKED_NO_TRADE",
                              str(exc) if isinstance(exc, InvalidData) else "DEMO_EXECUTION_SETUP_INVALID")
    Path(args.report_file).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("RESULT:", result["status"])
    print("REASON:", result["reason"])
    print("Order sent:", "YES — DEMO ONLY" if result.get("order_sent") else "NO")
    if result["status"] == "UNRESOLVED_RECONCILIATION_REQUIRED":
        print("STOP: outcome requires manual reconciliation; this proposal cannot be retried.")
    return 0 if result["status"] == "CONFIRMED_DEMO_EXECUTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
