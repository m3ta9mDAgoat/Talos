"""Research-only paper broker with fail-closed live adapter boundaries."""
from __future__ import annotations
from dataclasses import dataclass,field
from decimal import Decimal,ROUND_DOWN
import os

@dataclass
class PaperBook:
    cash:Decimal=Decimal('10000')
    positions:dict[str,int]=field(default_factory=dict)
    fills:list[dict]=field(default_factory=list)
    killed:bool=False
    max_notional:Decimal=Decimal('5000')
    max_allocation:Decimal=Decimal('0.9')
    fee_bps:Decimal=Decimal('10')
    def order(self,symbol,side,qty,price):
        if self.killed:raise PermissionError('Paper kill switch enabled')
        if side not in ('BUY','SELL'):raise ValueError('Invalid side')
        if not symbol or len(symbol)>30:raise ValueError('Invalid symbol')
        qty=int(qty);price=Decimal(str(price))
        if qty<=0 or price<=0 or not price.is_finite():raise ValueError('Invalid quantity/price')
        amount=price*qty;fee=amount*self.fee_bps/10000
        if amount>self.max_notional:raise ValueError('Per-order risk notional exceeded')
        if side=='BUY':
            if amount+fee>self.cash or amount>self.cash*self.max_allocation:raise ValueError('Paper cash/allocation risk limit')
            self.cash-=amount+fee;self.positions[symbol]=self.positions.get(symbol,0)+qty
        else:
            if self.positions.get(symbol,0)<qty:raise ValueError('No short selling or naked selling in paper broker')
            self.positions[symbol]-=qty;self.cash+=amount-fee
        fill={'symbol':symbol,'side':side,'qty':qty,'price':str(price),'fee':str(fee),
              'execution_type':'PAPER_CLIENT_QUOTE_NOT_MARKET_VERIFIED'}
        self.fills.append(fill)
        return fill
    def equity(self,marks):
        return self.cash+sum(Decimal(str(marks[s]))*q for s,q in self.positions.items() if q)

class LiveBroker:
    """Deliberately unavailable until credentialed sandbox, broker reconciliation,
    explicit approvals and risk-engine production checks have been tested.
    """
    @property
    def capability(self):return {'connected':False,'orders_enabled':False,'reason':'LIVE_DISABLED_PENDING_BROKER_CERTIFICATION'}
    def place_order(self,*args,**kwargs):
        raise PermissionError('Talos Research Plane never places live orders; use separately audited broker service')
