#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
printf '\nSentinelFX — local simulation dashboard\n\nOpen the address printed after startup below.\nKeep this Terminal window open while using the app.\nPress Control-C to stop it.\n\n'
SYSTEM_MODE=SIMULATION MT5_DIAGNOSTIC_MODE=disabled LIVE_EXECUTION_ENABLED=false python3 -B server.py --demo --port 8765 --port-fallback
printf '\nThe server has stopped. Press Return to close this window.\n'
read -r reply
