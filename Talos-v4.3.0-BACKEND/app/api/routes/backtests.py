from fastapi import APIRouter

from app.schemas.backtest import SignalBacktestRequest, SignalBacktestResult
from app.services.backtest import signal_backtester

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.post("/signal", response_model=SignalBacktestResult)
async def backtest_signal(request: SignalBacktestRequest) -> SignalBacktestResult:
    return signal_backtester.run(
        request.signal,
        request.candles,
        candle_timeframe=request.candle_timeframe,
        conservative_intrabar=request.conservative_intrabar,
    )
