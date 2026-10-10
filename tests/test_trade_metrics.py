from datetime import datetime, timezone

from app.core.enums import Side
from app.schemas.backtest import SignalBacktestResult, TargetHit
from app.schemas.signal import EntrySpec, NormalizedSignal, StopSpec
from app.services.trade_metrics import trade_return_pct


def test_equal_target_allocation_then_stop():
    now = datetime.now(timezone.utc)
    signal = NormalizedSignal(
        timestamp=now,
        symbol="ABC/USDT",
        side=Side.BUY,
        entry=EntrySpec(type="price", price=100),
        stop=StopSpec(price=90),
        targets=[110, 120],
    )
    result = SignalBacktestResult(
        symbol="ABC/USDT",
        entry_triggered=True,
        entry_price=100,
        entry_at=now,
        targets=[TargetHit(target=110, hit=True, hit_at=now), TargetHit(target=120, hit=False)],
        stop_hit=True,
        stop_at=now,
        exit_price=90,
    )
    # 50% at +10%, 50% at -10% = 0% before costs.
    ret = trade_return_pct(signal, result, final_price=95, fee_bps=0, slippage_bps=0)
    assert ret == 0.0
