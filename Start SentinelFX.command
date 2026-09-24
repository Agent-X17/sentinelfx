#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
printf '\nSentinelFX — local simulation dashboard\n\nOpen http://127.0.0.1:8765 in your browser.\nKeep this Terminal window open while using the app.\nPress Control-C to stop it.\n\n'
SYSTEM_MODE=SIMULATION MT5_ENABLED=false LIVE_EXECUTION_ENABLED=false python3 -B server.py --port 8765
printf '\nThe server has stopped. Press Return to close this window.\n'
read -r reply
