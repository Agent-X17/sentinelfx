# TradingView webhook testing

## Local test

Start SentinelFX, read its printed webhook URL, then run:

```sh
python3 -B scripts/test_webhook.py --url http://127.0.0.1:8765/api/webhook/tradingview
```

The helper generates a unique alert ID and fresh UTC timestamps. In disabled MT5 mode, `MT5_DISABLED` is the expected fail-closed result. In mock diagnostic mode, `REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED` is expected. Both prove intake without authorizing or sending an order. View the stored reason under **Signal decisions**.

## Real TradingView delivery through a tunnel

TradingView cannot call `127.0.0.1`. It requires a public HTTPS URL. Install a trusted tunnel product such as Cloudflare Tunnel or ngrok separately. Copy only the hostname from the generated URL, without `https://` or a path.

Use a new secret of at least 32 characters and launch, replacing the example host:

```sh
export TRADINGVIEW_WEBHOOK_SECRET='replace-with-a-random-secret-at-least-32-characters'
export WEBHOOK_ALLOWED_HOSTS='your-random-host.example-tunnel.app'
export MT5_DIAGNOSTIC_MODE=mock
python3 -B server.py --port 8765 --db data/tunnel-test.sqlite3
```

Point the tunnel at `http://127.0.0.1:8765`. Configure the TradingView alert URL as:

```text
https://your-random-host.example-tunnel.app/api/webhook/tradingview
```

Put the secret in the alert JSON as `"secret":"..."`, because TradingView's alert dialog may not support the custom `X-Webhook-Secret` header. The application strips that field before persistence. Keep credentials out of every other field.

The dashboard and `/status` show whether a secret is configured and which external `Host` values are accepted. Remote GET requests remain refused; the allowed external host applies only to the webhook endpoint. A tunnel exposes an internet-facing endpoint, so stop the tunnel after testing and rotate the secret if it was disclosed.

Common results:

- `403 Invalid host`: `WEBHOOK_ALLOWED_HOSTS` does not exactly match the incoming `Host` header.
- `401 WEBHOOK_AUTH_FAILED`: secret mismatch.
- `409 DUPLICATE_WEBHOOK`: reuse of an alert ID or stable no-ID signal identity.
- `422`: stale, expired, or unmapped alert.
- `200` with `NO_TRADE`: intake worked and a later safety gate blocked the candidate.
