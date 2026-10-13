# v2.3 deployment status

Code tested locally; Render deployment not yet performed for v2.3.

- Hosted reference worker in web-service process, authenticated claims and PostgreSQL durable queue.
- Native LEAN/NautilusTrader Stage3 Windows fixture remains Windows-only.
- Telegram Bot v2.1 remains an external daemon; remote connection requires configuring its local Admin token and bot_config.
- Frontend API-backed Data Library, Jobs, Paper Portfolio and Service Status; other mockup items remain illustrative.
- No broker integration, no live trading.
- External feed connectors optional / unverified.
- Render free Postgres scheduled expiry 2026-11-12.
