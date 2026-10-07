from datetime import datetime, timezone

from app.core.enums import SignalStatus, StopTrigger
from app.services.signal_parser import telegram_signal_parser


def test_parses_arabic_english_signal():
    text = """
🟩 RLC|USDT
⚜ BUY : 0.41 - 0.43
🎯:
1 : 0.440
2 : 0.450
3 : 0.470
4 : 0.500
5 : 0.540
🔴 STOP اغلاق 4 ساعات اقل من 0.40
"""
    result = telegram_signal_parser.parse(text, timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert result.signal is not None
    assert result.signal.symbol == "RLC/USDT"
    assert result.signal.entry.min == 0.41
    assert result.signal.entry.max == 0.43
    assert result.signal.targets == [0.44, 0.45, 0.47, 0.5, 0.54]
    assert result.signal.stop.price == 0.40
    assert result.signal.stop.trigger == StopTrigger.CANDLE_CLOSE
    assert result.signal.stop.timeframe == "4h"
    assert result.signal.status == SignalStatus.PARSED
    assert result.confidence == 1.0


def test_unclear_signal_is_not_guessed():
    result = telegram_signal_parser.parse("RLC looks nice today")
    assert result.signal is None
    assert result.confidence < 0.8
