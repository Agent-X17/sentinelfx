# SentinelFX quick start

## Fastest Mac start

1. Open the project folder in Finder.
2. Double-click **Start SentinelFX.command**.
3. Keep the Terminal window open.
4. Open the **Dashboard** address printed in Terminal.

The launcher creates a fresh, timestamped SQLite database and adds three clearly labelled synthetic decisions. It starts in `SIMULATION`, keeps MT5 disabled, and cannot submit a live order. If port 8765 is occupied, the printed address will show a different port.

From Terminal, the same workflow is:

```sh
cd /path/to/forex-engine
python3 -B server.py --demo --port-fallback
```

Startup prints the dashboard URL, status-page URL, exact database path, system mode, MT5 diagnostic mode, webhook URL, and live-execution state. Open `/status` when you want a plain operator summary. `/api/health` provides the same kind of information as JSON.

## Safe choices

```sh
# Simulator only
MT5_DIAGNOSTIC_MODE=disabled python3 -B server.py --demo --port-fallback

# Synthetic MT5 diagnostic pipeline; still always NO_TRADE at the evidence gate
MT5_DIAGNOSTIC_MODE=mock python3 -B server.py --demo --port-fallback
```

Stop the app with **Control-C** in its Terminal window. A “connection refused” browser page means the server is stopped or the browser is using an old port; return to Terminal and use the currently printed Dashboard address.

## Verify it

```sh
python3 -B -m unittest discover -s tests -v
node --check static/app.js
python3 -B -m py_compile server.py manage.py engine/*.py tests/*.py scripts/*.py
```

The real `MetaTrader5` package is generally unavailable on native macOS. Use mock diagnostics on Mac. Real diagnostics require a supported host, the official package, a running terminal, and a demo login. They still cannot send an order in this release.

## Manual browser checklist

Confirm that the header says `LIVE SEND OFF`, the Overview shows the three example decisions, and the Trading workflow card shows the selected diagnostic mode. Open each sidebar page, run **Test standard lot rejection**, and confirm its reason is visible under Signal decisions. Open `/status` and confirm its database path matches the startup output. Browser developer tools should show no JavaScript errors. This checklist is the fallback when automated Chromium launch is restricted by the host.
