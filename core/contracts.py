from __future__ import annotations
import hashlib,json,math,re
from datetime import date
from pydantic import BaseModel,Field,field_validator,model_validator
from typing import Literal

SYM=re.compile(r'^[A-Z0-9][A-Z0-9_\-./:=^]{0,30}$')
class StrategySpec(BaseModel):
    plugin:Literal['sma_cross','ema_cross','rsi_reversion','buy_hold','telegram_signal','trusted_python','ten_month_sma_fixture']='sma_cross'
    short:int=Field(10,ge=2,le=250)
    long:int=Field(40,ge=3,le=500)
    rsi_buy:float=Field(28,ge=1,le=50)
    rsi_sell:float=Field(65,ge=50,le=99)
    parameters:dict[str,float|int|str|bool]=Field(default_factory=dict,max_length=20)
    trusted_name:str|None=Field(default=None,max_length=60,pattern=r'^[A-Za-z_][A-Za-z_0-9]*$')
    @model_validator(mode='after')
    def valid(self):
        if self.plugin in ('sma_cross','ema_cross') and self.short>=self.long:raise ValueError('short must be < long')
        if self.plugin=='trusted_python' and not self.trusted_name:raise ValueError('Locally pinned trusted_name required')
        return self

class DataSpec(BaseModel):
    source:Literal['sample','dataset','yahoo','ccxt','native_fixture']='sample'
    dataset_id:str|None=None
    symbol:str='AAPL'
    interval:Literal['1d']='1d'
    start:date=date(2020,1,1)
    end:date=date(2023,12,31)
    exchange:Literal['binance','kraken','coinbase','okx','bybit']='binance'
    @field_validator('symbol')
    @classmethod
    def symbol_ok(cls,s):
        if not SYM.fullmatch(s):raise ValueError('Invalid symbol')
        return s
    @model_validator(mode='after')
    def valid(self):
        if self.end<=self.start:raise ValueError('end must be after start')
        if (self.end-self.start).days>365*30:raise ValueError('Range > 30 years')
        if self.source=='dataset' and not self.dataset_id:raise ValueError('dataset_id required')
        if self.source=='ccxt' and '/' not in self.symbol:raise ValueError('CCXT symbols should resemble BTC/USDT')
        return self

class RiskSpec(BaseModel):
    initial_cash:float=Field(10000,ge=100,le=1000000000)
    max_allocation:float=Field(0.9,gt=0,le=1)
    fee_bps:float=Field(10,ge=0,le=500)
    slippage_bps:float=Field(2,ge=0,le=300)
    max_drawdown_kill:float=Field(0.5,gt=0,le=1)
    max_trade_notional:float=Field(500000,gt=0,le=1000000000)

class ResearchRequest(BaseModel):
    engine:Literal['reference','lean_parity','nautilus_parity']='reference'
    data:DataSpec=Field(default_factory=DataSpec)
    strategy:StrategySpec=Field(default_factory=StrategySpec)
    risk:RiskSpec=Field(default_factory=RiskSpec)
    walk_forward:bool=True
    monte_carlo:int=Field(200,ge=0,le=1000)
    robustness:bool=False
    robustness_simulations:int=Field(25,ge=5,le=200)
    random_seed:int=Field(42,ge=0,le=2147483647)
    @model_validator(mode='after')
    def engine_rules(self):
        if self.engine!='reference':
            if self.strategy.plugin!='ten_month_sma_fixture' or self.data.source!='native_fixture' or self.data.symbol not in ('AAPL','MSFT'):
                raise ValueError('Native Stage3 is only the approved 10-month SMA AAPL/MSFT fixture')
            if self.data.start!=date(2012,1,1) or self.data.end!=date(2022,5,31):
                raise ValueError('Native Stage3 has fixed fixture period 2012-01 through 2022-05')
            if self.monte_carlo!=0 or self.walk_forward:
                raise ValueError('Native Stage3 does not run Monte Carlo or walk-forward; disable both')
            if self.strategy.parameters or self.strategy.trusted_name:
                raise ValueError('Native Stage3 fixture does not use arbitrary plugin parameters')
            if self.risk.max_allocation!=0.998 or self.risk.slippage_bps!=0:
                raise ValueError('Native Stage3 requires allocation=0.998 and slippage=0')
            if self.risk.initial_cash!=10000 or self.risk.fee_bps!=10:
                raise ValueError('Native fixture uses fixed 10000 USD initial cash and 10bps fee')
        if self.engine=='reference' and self.data.source=='native_fixture':
            raise ValueError('Native fixture is not daily market data')
        if self.robustness:
            if self.engine!='reference' or self.strategy.plugin not in ('ema_cross','sma_cross','rsi_reversion'):
                raise ValueError('Robustness grid supports reference EMA/SMA/RSI only')
            if self.data.source=='native_fixture':raise ValueError('Robustness needs daily OHLCV')
        if self.strategy.plugin=='telegram_signal':
            if not self.strategy.parameters.get('archive_id'):
                raise ValueError('Telegram strategy requires immutable archive_id')
            if self.walk_forward or self.monte_carlo:
                raise ValueError('Telegram event-time benchmark does not support optimizer / return-bootstrap; disable them')
        if self.engine=='reference' and self.strategy.plugin=='ten_month_sma_fixture':
            raise ValueError('10M monthly fixture plugin runs only in verified native harness')
        return self

class JobSubmit(BaseModel):
    kind:Literal['research','native_parity','telegram_replay']='research'
    request:ResearchRequest=Field(default_factory=ResearchRequest)
    idempotency_key:str|None=Field(default=None,max_length=120,pattern=r'^[a-zA-Z0-9._:-]+$')
    @model_validator(mode='after')
    def rules(self):
        if self.kind=='native_parity' and self.request.engine=='reference':raise ValueError('Native parity requires native engine')
        if self.kind=='research' and self.request.engine!='reference':raise ValueError('Use native_parity for native execution')
        return self

def stable_hash(v:dict)->str:
    return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()).hexdigest()
