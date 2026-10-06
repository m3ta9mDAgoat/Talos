# Talos v3 architecture

## Separation of concerns

1. **Sources** collect immutable/raw events.
2. **Normalization** converts source-specific payloads into reusable events or normalized trade signals.
3. **Strategies** are stored DSL documents; they never own broker credentials.
4. **Backtests** consume stored strategy versions and historical market data.
5. **Optimizer** changes declared strategy parameters and calls the same backtest engine.
6. **Paper execution** uses live prices with simulated capital.
7. **Risk / routing / live execution** are downstream gates independent of research logic.
8. **Portfolio** aggregates paper and connected-broker state.

## Why this structure matters

A new Telegram format, news feed, research paper, technical rule, social API or future ML feature should change only its adapter/block. The persistence, backtesting, optimization and execution layers remain reusable.

## Historical integrity

Raw source events are stored before interpretation. Parsing/classification can be rerun when a parser version changes without losing the original evidence. For sources that cannot expose deleted historical data, Talos cannot reconstruct what was deleted before monitoring began; this limitation should be reflected in research confidence.
