# Talos v1 research flow

```
Telegram / News / RSS / Market / Custom
              ↓
        immutable raw archive
              ↓
        message classification
              ↓
          normalization
              ↓
        strategy / decision
              ↓
       market-data resolver
              ↓
           backtester
              ↓
      validation / optimizer
              ↓
         paper execution
              ↓
            risk gate
              ↓
    future execution adapters
```

## Why classification is separate from parsing
A channel contains signals, results, advertising, news and ordinary commentary. Classification decides what kind of message Talos is looking at. Only a `new_signal` proceeds to the generic signal parser. This sharply reduces false positives and lets result/update messages be linked to existing trades later instead of being misread as fresh entries.

## Future strategy addition
A new strategy should implement the existing `Strategy` contract and emit a standard `Decision`. Nothing downstream should care whether that decision came from Telegram, a news event, a technical model or an ML model.
