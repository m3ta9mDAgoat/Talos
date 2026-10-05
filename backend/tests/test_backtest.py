from datetime import datetime, timedelta, timezone

from app.core.enums import Side, StopTrigger
from app.schemas.backtest import Candle
from app.schemas.signal import EntrySpec, NormalizedSignal, StopSpec
from app.services.backtest import signal_backtester


def test_signal_backtest_hits_targets_then_candle_close_stop():
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    signal = NormalizedSignal(
        timestamp=t0,
        symbol="RLC/USDT",
        side=Side.BUY,
        entry=EntrySpec(type="range", min=0.41, max=0.43),
        targets=[0.44, 0.45, 0.47],
        stop=StopSpec(price=0.40, trigger=StopTrigger.CANDLE_CLOSE, timeframe="4h"),
    )
    candles = [
        Candle(timestamp=t0, open=0.435, high=0.436, low=0.425, close=0.429),
        Candle(timestamp=t0 + timedelta(hours=1), open=0.429, high=0.445, low=0.425, close=0.44),
        Candle(timestamp=t0 + timedelta(hours=2), open=0.44, high=0.455, low=0.438, close=0.452),
        Candle(timestamp=t0 + timedelta(hours=3), open=0.452, high=0.475, low=0.395, close=0.399),
    ]
    result = signal_backtester.run(signal, candles, candle_timeframe="1h")
    assert result.entry_triggered is True
    assert result.entry_price == 0.43
    assert [t.hit for t in result.targets] == [True, True, True]
    assert result.stop_hit is True
    assert result.stop_at == candles[-1].timestamp


def test_price_touch_stop_is_conservative_same_candle():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    signal = NormalizedSignal(
        timestamp=t0,
        symbol="ABC/USDT",
        side=Side.BUY,
        entry=EntrySpec(type="price", price=1.0),
        targets=[1.1],
        stop=StopSpec(price=0.9),
    )
    candles = [Candle(timestamp=t0, open=1.0, high=1.2, low=0.8, close=1.05)]
    result = signal_backtester.run(signal, candles, conservative_intrabar=True)
    assert result.stop_hit is True
    assert result.targets[0].hit is False
