# TradingView diagnostic setup

SentinelFX accepts TradingView alerts at exactly:

```text
/api/webhook/tradingview
```

This procedure proves delivery and safety-gate behavior. It does not submit an order. Keep `SYSTEM_MODE=SIMULATION`, `LIVE_EXECUTION_ENABLED=false`, and `MT5_DIAGNOSTIC_MODE=mock` throughout the test.

TradingView requires webhook destinations to use port 80 or 443, cancels requests that take longer than three seconds, requires two-factor authentication on the TradingView account, and sends `application/json` when the alert message is valid JSON.

## 1. Start SentinelFX locally

Open a terminal in the project folder and run:

```sh
export SYSTEM_MODE=SIMULATION
export LIVE_EXECUTION_ENABLED=false
export MT5_DIAGNOSTIC_MODE=mock
export MT5_ENABLED=false
export TRADINGVIEW_WEBHOOK_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export WEBHOOK_ALLOWED_HOSTS=''
python3 -B server.py --port 8765 --db data/tradingview-diagnostic.sqlite3
```

Keep that terminal open. Do not paste the generated secret into chat, logs, screenshots, or source control.

In another terminal, confirm the local path:

```sh
python3 -B scripts/test_webhook.py \
  --url http://127.0.0.1:8765/api/webhook/tradingview \
  --secret "$TRADINGVIEW_WEBHOOK_SECRET"
```

Expected result: HTTP `200`, decision `NO_TRADE`, reason `REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED`, and `order_sent: false`.

The helper can also exercise intentional failures:

```sh
python3 -B scripts/test_webhook.py --url http://127.0.0.1:8765/api/webhook/tradingview --secret "$TRADINGVIEW_WEBHOOK_SECRET" --case bad-secret
python3 -B scripts/test_webhook.py --url http://127.0.0.1:8765/api/webhook/tradingview --secret "$TRADINGVIEW_WEBHOOK_SECRET" --case stale
python3 -B scripts/test_webhook.py --url http://127.0.0.1:8765/api/webhook/tradingview --secret "$TRADINGVIEW_WEBHOOK_SECRET" --case duplicate
python3 -B scripts/test_webhook.py --url http://127.0.0.1:8765/api/webhook/tradingview --secret "$TRADINGVIEW_WEBHOOK_SECRET" --case missing-stop
python3 -B scripts/test_webhook.py --url http://127.0.0.1:8765/api/webhook/tradingview --secret "$TRADINGVIEW_WEBHOOK_SECRET" --case unmapped
```

## 2. Create a temporary public HTTPS tunnel

TradingView cannot call `127.0.0.1`. Use one of these temporary tunnel commands in a third terminal:

```sh
cloudflared tunnel --url http://127.0.0.1:8765
```

or:

```sh
ngrok http 8765
```

The tunnel product prints a public HTTPS URL. Copy only its hostname. For example, from `https://random-name.trycloudflare.com`, copy `random-name.trycloudflare.com`.

Stop SentinelFX with Control-C and restart it with the exact tunnel hostname:

```sh
export SYSTEM_MODE=SIMULATION
export LIVE_EXECUTION_ENABLED=false
export MT5_DIAGNOSTIC_MODE=mock
export MT5_ENABLED=false
export WEBHOOK_ALLOWED_HOSTS='random-name.trycloudflare.com'
python3 -B server.py --port 8765 --db data/tradingview-diagnostic.sqlite3
```

Use this public webhook URL:

```text
https://random-name.trycloudflare.com/api/webhook/tradingview
```

Only the webhook route accepts the approved external host. The dashboard, `/status`, and `/api/health` remain local-only and return `403` through the public hostname. This limits tunnel exposure.

Check that protection and then test the public webhook:

```sh
curl -i https://random-name.trycloudflare.com/
python3 -B scripts/test_webhook.py \
  --url https://random-name.trycloudflare.com/api/webhook/tradingview \
  --secret "$TRADINGVIEW_WEBHOOK_SECRET"
```

The first command should return `403`. The webhook helper should return the expected `NO_TRADE` result.

## 3. Create the TradingView alert

1. Enable two-factor authentication on the TradingView account.
2. Create a BUY or SELL alert. Use a separate alert for each direction.
3. Paste the public webhook URL into the alert's **Webhook URL** field.
4. Copy [examples/tradingview-alert-message.json](../examples/tradingview-alert-message.json) into the alert message.
5. Replace `PASTE_NEW_WEBHOOK_ONLY_SECRET` with the current webhook secret.
6. Set `side` to the intended diagnostic direction and replace the sample stop-loss and take-profit prices with values appropriate to the alert symbol.
7. Trigger the alert and inspect TradingView's Alert Log.

TradingView's alert form does not provide SentinelFX's custom authentication header, so this diagnostic template carries the dedicated webhook secret in the JSON body. SentinelFX strips that known secret before persistence. Never reuse a broker password, API key, personal password, or funded-account credential as this secret. Rotate it after the test.

The template uses TradingView placeholders for `{{exchange}}`, `{{ticker}}`, `{{interval}}`, `{{timenow}}`, and `{{close}}`. SentinelFX validates freshness, direction, mapped symbol, stop loss, take profit, authentication, and replay identity before the risk engine evaluates the candidate.

## 4. Verify the result locally

Open these local pages:

- Dashboard: `http://127.0.0.1:8765/`
- Operator status: `http://127.0.0.1:8765/status`
- Machine-readable health: `http://127.0.0.1:8765/api/health`

The dashboard's **Webhook diagnostics**, **Signal decisions**, and **Audit log** should show the alert and its exact block source. In mock MT5 mode, the safe expected decision is:

```text
NO_TRADE / REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED
```

That result proves TradingView delivery reached the engine while synthetic MT5 evidence remained unable to authorize a trade.

Common responses:

- `403 Invalid host`: `WEBHOOK_ALLOWED_HOSTS` does not exactly match the public hostname.
- `401 WEBHOOK_AUTH_FAILED`: the secret is missing or does not match.
- `409 DUPLICATE_WEBHOOK`: the alert ID or stable no-ID identity was already accepted.
- `422`: the alert is stale, expired, malformed, or the symbol is unmapped.
- `200` with `NO_TRADE`: intake worked and a later safety gate blocked the candidate.

When testing is complete, stop the tunnel, stop SentinelFX, and rotate or unset `TRADINGVIEW_WEBHOOK_SECRET`.

Official references:

- <https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/>
- <https://www.tradingview.com/support/solutions/43000531021-how-to-use-a-variable-value-in-alert/>
- <https://www.tradingview.com/support/solutions/43000776894-what-do-errors-mean-when-sending-webhooks/>
