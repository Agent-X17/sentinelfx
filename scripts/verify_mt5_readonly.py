#!/usr/bin/env python3
"""Redaction-safe Windows MT5 read-only verification. Never submits an order."""
import argparse
import sys
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

from engine.config import Settings
from engine.domain import InvalidData
from engine.isolated_mt5 import IsolatedMT5Service,SnapshotMT5
from engine.mt5_evidence import validate_order_request,validate_snapshot


def blocked(code):
    print("RESULT: BLOCKED / NO_TRADE")
    print("REASON:",code)
    print("No order was sent.")
    return 1


def main():
    parser=argparse.ArgumentParser(description="Verify redacted read-only MT5 demo evidence")
    parser.add_argument("--symbol",required=True,help="Exact MT5 Market Watch symbol")
    parser.add_argument("--order-check",action="store_true",help="Also run one non-submitting minimum-volume order_check")
    args=parser.parse_args()
    service=None
    try:
        settings=Settings.from_env(ROOT)
        if settings.demo_trade_proposals_enabled or not settings.demo_trade_proposal_kill_switch:
            return blocked("SAFE_DEFAULTS_NOT_ACTIVE")
        if settings.live_execution_enabled or settings.mode!="SIMULATION" or settings.mt5_diagnostic_mode!="real":
            return blocked("READ_ONLY_MODE_NOT_CONFIGURED")
        if not settings.demo_expected_account_login or not settings.demo_expected_broker_server:
            return blocked("EXPECTED_DEMO_IDENTITY_NOT_CONFIGURED")
        service=IsolatedMT5Service(True,settings.mt5_terminal_path)
        data=service.snapshot(args.symbol)
        summary=validate_snapshot(data,{"mt5_symbol":args.symbol},settings.demo_expected_account_login,settings.demo_expected_broker_server)
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
