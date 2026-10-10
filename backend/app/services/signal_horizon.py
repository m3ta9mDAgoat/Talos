"""Frozen, explainable horizon selection for Telegram-paper simulations.

A deterministic volatility/time-to-first-target heuristic, never a prediction of
winning probability. Only closed candles observed at decision time can be used.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

POLICY_VERSION = "adaptive_horizon_1"
FEE_BPS_PER_SIDE = 10.0
SLIPPAGE_BPS_PER_SIDE = 8.0


def aware(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _closed_bars(bars: list[dict[str, Any]], decided_at: datetime) -> list[dict[str, Any]]:
    at_ms = int(aware(decided_at).timestamp() * 1000)
    return sorted((b for b in bars if int(b['timestamp']) + 60_000 <= at_ms), key=lambda x: x['timestamp'])[-120:]


def volatility_pct_per_min(bars: list[dict[str, Any]], decided_at: datetime) -> float | None:
    clean = _closed_bars(bars, decided_at)
    # At least 12 completed one-minute bars; never use a running/future bar.
    if len(clean) < 12:
        return None
    moves = []
    for a,b in zip(clean, clean[1:]):
        if int(b['timestamp'])-int(a['timestamp']) != 60_000:
            continue
        if float(a['close']) > 0 and float(b['close']) > 0:
            moves.append(abs(math.log(float(b['close']) / float(a['close'])))*100)
    if len(moves) < 10:
        return None
    # Median robust against one sudden price spike.
    return max(0.02, min(3.0, 1.4826 * median(moves)))


def _entry_reference(entry: dict[str, Any] | None) -> float | None:
    if not entry:
        return None
    if entry.get('type') == 'price':
        return float(entry['price'])
    if entry.get('type') == 'range':
        return (float(entry['min']) + float(entry['max'])) / 2
    return None


def decide_entry_window(entry: dict | None, bars: list[dict], decided_at: datetime) -> dict[str, Any]:
    """Pick max wait for an entry, independently of how long a filled trade runs."""
    vol = volatility_pct_per_min(bars, decided_at)
    ref = _entry_reference(entry)
    if entry and entry.get('type') == 'market':
        minutes = 15
    elif ref and entry and entry.get('type') == 'range' and vol:
        width_pct = max(0.01, abs(float(entry['max']) - float(entry['min'])) / ref * 100)
        minutes = round((width_pct / vol)**2 * 1.5)
    elif vol:
        minutes = round((0.8 / vol)**2)
    else:
        minutes = 180
    minutes = max(15, min(720, minutes))
    return {'policy_version': POLICY_VERSION, 'entry_wait_minutes': minutes,
            'entry_deadline': (aware(decided_at) + timedelta(minutes=minutes)).isoformat(),
            'volatility_pct_per_min': vol, 'selection': 'market_now' if entry and entry.get('type')=='market' else 'entry_zone_volatility' if vol else 'missing_market_history_fallback',
            'decided_at': aware(decided_at).isoformat()}


def decide_horizon(entry_price: float, targets: list[float], stop: dict | None,
                   bars: list[dict], entry_at: datetime) -> dict[str, Any]:
    """Freeze planned exit time and evidence at entry; no future chart selection."""
    entry_at = aware(entry_at)
    vol = volatility_pct_per_min(bars, entry_at)
    valid = sorted(float(t) for t in targets if float(t) > entry_price)
    tp_pct = (valid[0] / entry_price - 1) * 100 if valid else None
    stop_pct = (1 - float(stop['price']) / entry_price)*100 if stop and stop.get('price') else None
    # Diffusion time-to-distance scales quadratically with target/volatility.
    if vol and tp_pct and tp_pct > 0:
        minutes = round((tp_pct / vol)**2)
        basis = 'tp1_distance_over_realized_1m_volatility'
    elif tp_pct:
        # Missing data must be explicitly disclosed; based on intended move.
        minutes = round(60 * tp_pct)
        basis = 'tp1_distance_fallback_missing_volatility'
    elif stop_pct and stop_pct > 0:
        minutes = round(120 * stop_pct)
        basis = 'stop_distance_fallback_no_valid_target'
    else:
        minutes = 180
        basis = 'insufficient_signal_levels_fallback'
    # Allow an entire 4h candle to close before calling a candle-close stop.
    if stop and str(stop.get('trigger')) == 'candle_close':
        try:
            tf = str(stop.get('timeframe') or '4h')
            hours = int(tf[:-1]) * {'m':1/60,'h':1,'d':24}[tf[-1]]
            minutes = max(minutes, math.ceil(hours * 60) + 1)
        except (ValueError, KeyError, IndexError):
            minutes = max(minutes, 241)
    minutes = max(15, min(4320, minutes))
    return {'policy_version': POLICY_VERSION, 'holding_minutes': minutes,
            'deadline': (entry_at + timedelta(minutes=minutes)).isoformat(),
            'decided_at': entry_at.isoformat(), 'basis': basis,
            'tp1_distance_pct': tp_pct, 'stop_distance_pct': stop_pct,
            'volatility_pct_per_min': vol,
            'notes': 'Rule-based planning, not a win-probability estimate. Exit earlier on stop/all targets.'}


def settlement(entry: float, exit_price: float, *, fee_bps: float = FEE_BPS_PER_SIDE,
               slippage_bps: float = SLIPPAGE_BPS_PER_SIDE) -> dict[str, Any]:
    """Long-only net paper mark-to-market; fixed fees and slippage assumptions."""
    gross = (exit_price / entry - 1)*100
    costs = 2 * (fee_bps + slippage_bps) / 100
    net = gross - costs
    return {'gross_return_pct': round(gross, 6), 'net_return_pct': round(net, 6),
            'costs_pct': round(costs, 6), 'exit_price': float(exit_price),
            'outcome': 'win' if net > 0 else 'loss' if net < 0 else 'breakeven',
            'assumptions': '1m OHLCV mark-to-market, modeled fees/slippage; not an executable fill'}
