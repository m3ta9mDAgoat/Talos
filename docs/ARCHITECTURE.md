# Talos Architecture

## Stable extension contracts
Talos treats Telegram, news, RSS, exchanges, social feeds, and custom APIs as interchangeable DataSource plugins. Strategies consume normalized events/features and emit Decisions. Backtest and live execution operate on the same Decision contract.

## Research lifecycle
DRAFT -> BACKTESTED -> VALIDATED -> PAPER -> APPROVED -> LIVE

Direct DRAFT -> LIVE transitions are intentionally forbidden.

## Portability
Deployment-specific values are externalized through environment variables. No business logic depends on Render. The same containers can run on a local Docker host or another cloud/VPS.

## Planned V1 milestones
1. Dedicated Telegram account connector
2. Historical importer + message/edit archive
3. Telegram signal parser and normalized schema
4. Exchange market resolver
5. Historical market-data fetcher/cache
6. Event-driven backtest engine
7. Metrics + reproducibility manifest
8. Dashboard views
9. Optimizer plugin framework
10. Paper execution
11. Live spot execution with risk gates and kill switch
