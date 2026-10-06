from datetime import datetime, timedelta, timezone
from app.schemas.backtest import Candle
from app.services.strategy_engine import entry_ok, simulate_technical
from app.services.research_service import heuristic_draft


def candles(vals):
    t=datetime(2026,1,1,tzinfo=timezone.utc)
    return [Candle(timestamp=t+timedelta(hours=i),open=v,high=v*1.02,low=v*.98,close=v,volume=100+i) for i,v in enumerate(vals)]


def test_no_code_rsi_condition_can_be_evaluated():
    cs=candles([100,99,98,97,96,95,94,93,92,91,90,89,88,87,86,85,84])
    d={'entry_logic':'all','conditions':[{'feature':'rsi','period':14,'op':'<','value':40}]}
    assert entry_ok(cs,15,d) is True


def test_technical_simulator_returns_real_trade_rows():
    cs=candles([100,99,98,97,96,95,94,93,92,91,90,89,88,87,86,90,94,98,102])
    d={'entry_logic':'all','conditions':[{'feature':'rsi','period':14,'op':'<','value':40}], 'take_profit_pct':2,'stop_loss_pct':3,'max_holding_bars':4}
    rows=simulate_technical(cs,d,10,5)
    assert rows
    assert 'return_pct' in rows[0]


def test_research_heuristic_builds_supported_strategy():
    d=heuristic_draft('A momentum trading strategy based on recent returns','Paper idea')
    assert d['kind']=='technical'
    assert d['definition']['conditions'][0]['feature']=='return_pct'
