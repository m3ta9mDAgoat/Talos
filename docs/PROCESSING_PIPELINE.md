# Talos Processing Pipeline

Talos separates research, control, and execution so a new strategy can be added without rebuilding the platform.

## 1. Source adapters
Raw inputs enter through `DataSource` plugins. Telegram is the first concrete connector; news/RSS/market/on-chain/custom APIs use the same contract later.

## 2. Raw event archive
Incoming source data is saved before interpretation. Re-parsing old data with a newer parser remains possible. Telegram revisions are deduplicated by message/revision metadata rather than overwriting the raw record.

## 3. Normalization
Source-specific text is converted to a stable schema: symbol, market type, side, entry, stop, targets, timestamp, confidence, metadata.

## 4. Strategy layer
Strategies consume normalized events/features and produce a `Decision`. They do not select exchange accounts or position size and they cannot directly move money.

## 5. Market data
Historical/live price providers are independent plugins. The planned V1 provider is CCXT; large historical datasets are cached outside PostgreSQL as Parquet.

## 6. Research
The same normalized decision is evaluated by the backtester, optimizer, walk-forward validator, and Monte Carlo tools.

## 7. Control
The risk and portfolio layers apply capital constraints independent of the strategy. They can reject a valid signal.

## 8. Execution
The exchange resolver ranks eligible venues. The execution broker sends the order only after all gates pass. Live trading is disabled by configuration by default.

## Lifecycle
`DRAFT -> BACKTESTED -> VALIDATED -> PAPER -> APPROVED -> LIVE`

Direct `DRAFT -> LIVE` transitions are forbidden.
