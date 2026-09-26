"""One-shot local HFM DEMO worker. This is the sole native submission boundary."""
import json
import sys
import time
from datetime import datetime, timezone

from .domain import InvalidData
from .hfm_demo_time import evaluate, report_safe
from .mt5 import MT5Service
from .mt5_evidence import validate_order_request
from .mt5_worker import collect

MAGIC = 26092601
DONE = 10009


def blocked(code, evidence=None):
    return {"status": "BLOCKED_NO_TRADE", "reason": code, "order_sent": False,
            "evidence": evidence or {}}


def run(payload, adapter=None, sleeper=time.sleep):
    send_attempted = False
    if not isinstance(payload, dict) or payload.get("account_tag") != "DEMO_ONLY":
        return blocked("DEMO_ACCOUNT_TAG_MISSING")
    required = ("symbol", "direction", "volume", "sl", "tp", "attempt_id", "expected_login",
                "expected_server", "policy")
    if any(payload.get(key) in (None, "") for key in required):
        return blocked("DEMO_EXECUTION_REQUEST_INCOMPLETE")
    if payload["direction"] not in ("BUY", "SELL"):
        return blocked("DEMO_EXECUTION_DIRECTION_INVALID")
    owned = adapter is None
    adapter = adapter or MT5Service(True, payload.get("terminal_path", ""))
    try:
        if owned:
            initial = adapter.initialize()
            if not initial.ok:
                return blocked(initial.code)
        samples = []
        for index in range(3):
            if index:
                sleeper(1)
            samples.append(collect(adapter, payload["symbol"], timestamp_reads=True))
        evidence = evaluate(samples, payload["policy"], datetime.now(timezone.utc),
                            payload["expected_login"], payload["expected_server"],
                            payload["symbol"], payload.get("previous_state"),
                            require_autotrading=True,
                            external_clock_status=payload.get("external_clock_status",
                                                              "EXTERNAL_CLOCK_NOT_CERTIFIED"))
        snapshot = evidence["normalized_snapshot"]
        tick = snapshot["symbol_info_tick"]["data"]
        price = tick["ask"] if payload["direction"] == "BUY" else tick["bid"]
        request = {"symbol": payload["symbol"], "type": payload["direction"],
                   "volume": payload["volume"], "price": price,
                   "sl": payload["sl"], "tp": payload["tp"],
                   "filling_mode": snapshot["symbol_info"]["data"]["filling_mode"]}
        validate_order_request(snapshot, request)
        module = adapter._module
        native = {
            "action": module.TRADE_ACTION_DEAL,
            "symbol": request["symbol"],
            "volume": float(request["volume"]),
            "price": float(request["price"]),
            "sl": float(request["sl"]),
            "tp": float(request["tp"]),
            "deviation": 10,
            "magic": MAGIC,
            "comment": "SFXDEMO-" + payload["attempt_id"][:8],
            "type": module.ORDER_TYPE_BUY if request["type"] == "BUY" else module.ORDER_TYPE_SELL,
            "type_time": module.ORDER_TIME_GTC,
            "type_filling": (module.ORDER_FILLING_FOK if int(request["filling_mode"]) & 1 else
                             module.ORDER_FILLING_IOC if int(request["filling_mode"]) & 2 else
                             module.ORDER_FILLING_RETURN),
        }
        checked = adapter.order_check(native)
        if not checked.ok:
            return blocked("MT5_ORDER_CHECK_FAILED", report_safe(evidence))
        before = adapter.account_info()
        if not before.ok:
            return blocked("MT5_ACCOUNT_IDENTITY_UNVERIFIED", report_safe(evidence))
        send_attempted = True
        result = module.order_send(native)
        sent = True
        record = adapter._record(result) if result is not None else {}
        safe_result = {key: record.get(key) for key in ("retcode", "deal", "order", "volume", "price", "request_id")}
        after = adapter.account_info()
        positions = adapter.positions_get(symbol=payload["symbol"])
        identity_keys = ("login", "server", "currency", "trade_mode")
        identity_stable = before.ok and after.ok and all(
            (before.data or {}).get(key) == (after.data or {}).get(key) for key in identity_keys)
        matching = []
        if positions.ok:
            matching = [item for item in (positions.data or {}).get("items", [])
                        if item.get("magic") == MAGIC and item.get("comment") == native["comment"]]
        confirmed = (sent and record.get("retcode") == DONE and type(record.get("deal")) is int
                     and record["deal"] > 0 and identity_stable and len(matching) == 1)
        status = "CONFIRMED_DEMO_EXECUTED" if confirmed else "UNRESOLVED_RECONCILIATION_REQUIRED"
        reason = "DEMO_ORDER_CONFIRMED" if confirmed else "DEMO_ORDER_RESULT_NOT_UNIQUELY_RECONCILED"
        return {"status": status, "reason": reason, "order_sent": sent,
                "broker_result": safe_result, "matching_position_count": len(matching),
                "evidence": report_safe(evidence)}
    except InvalidData as exc:
        return blocked(str(exc))
    except Exception:
        if send_attempted:
            return {"status": "UNRESOLVED_RECONCILIATION_REQUIRED",
                    "reason": "DEMO_ORDER_OUTCOME_UNKNOWN", "order_sent": True,
                    "evidence": {}}
        return blocked("DEMO_EXECUTION_WORKER_FAILURE")
    finally:
        if owned:
            adapter.shutdown()


if __name__ == "__main__":
    print(json.dumps(run(json.loads(sys.stdin.read())), sort_keys=True, allow_nan=False))
