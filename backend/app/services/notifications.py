from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func

from app.db.session import SessionLocal
from app.models.analysis import AnalysisRunRecord
from app.models.signal import SignalRecord
from app.models.trading import LiveSignalSimulationRecord, PaperAccountRecord, PaperPositionRecord
from app.services.security import get_runtime, get_secret, set_runtime

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "new_signal": True,
    "signal_edited": True,
    "entry": True,
    "targets": True,
    "stop": True,
    "simulation_complete": True,
    "daily_summary": True,
    "daily_summary_hour_utc": 20,
    "simulation_horizon_days": 14,
}


def notification_config() -> dict[str, Any]:
    return {**DEFAULTS, **get_runtime("notifications", DEFAULTS)}


def update_notification_config(values: dict[str, Any]) -> dict[str, Any]:
    cfg = notification_config()
    allowed = set(DEFAULTS)
    for key, value in values.items():
        if key in allowed:
            cfg[key] = value
    cfg["daily_summary_hour_utc"] = max(0, min(23, int(cfg.get("daily_summary_hour_utc", 20))))
    cfg["simulation_horizon_days"] = max(1, min(365, int(cfg.get("simulation_horizon_days", 14))))
    set_runtime("notifications", cfg)
    return cfg


def _credentials() -> tuple[str | None, str | None]:
    row = get_secret("telegram_notify")
    return row.get("bot_token"), row.get("chat_id")


async def send_message(text: str, *, force: bool = False) -> dict[str, Any]:
    cfg = notification_config()
    if not force and not cfg.get("enabled", True):
        return {"ok": False, "skipped": "notifications_disabled"}
    token, chat_id = _credentials()
    if not token or not chat_id:
        return {"ok": False, "skipped": "notification_bot_not_configured"}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": str(chat_id),
        "text": text[:4096],
        "disable_web_page_preview": True,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        body = response.json()
    if not body.get("ok"):
        raise RuntimeError(str(body))
    return {"ok": True, "message_id": (body.get("result") or {}).get("message_id")}


async def discover_chats() -> list[dict[str, Any]]:
    token, _ = _credentials()
    if not token:
        raise RuntimeError("Save the notification bot token first")
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, params={"limit": 100, "timeout": 0})
        response.raise_for_status()
        body = response.json()
    chats: dict[str, dict[str, Any]] = {}
    for update in body.get("result") or []:
        message = update.get("message") or update.get("channel_post") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("id") is None:
            continue
        cid = str(chat["id"])
        chats[cid] = {
            "chat_id": cid,
            "type": chat.get("type"),
            "title": chat.get("title") or " ".join(x for x in [chat.get("first_name"), chat.get("last_name")] if x),
            "username": chat.get("username"),
        }
    return list(chats.values())


def _entry_text(entry: dict[str, Any] | None) -> str:
    if not entry:
        return "market"
    if entry.get("type") == "price":
        return str(entry.get("price"))
    if entry.get("type") == "range":
        return f"{entry.get('min')}–{entry.get('max')}"
    return "market"


async def notify_signal(signal_id: int, event: str = "new", *, raw_event_id: int | None = None) -> dict[str, Any]:
    cfg = notification_config()
    if event == "new" and not cfg.get("new_signal", True):
        return {"ok": False, "skipped": "new_signal_disabled"}
    if event == "edit" and not cfg.get("signal_edited", True):
        return {"ok": False, "skipped": "signal_edited_disabled"}
    with SessionLocal() as db:
        row = db.get(SignalRecord, signal_id)
        if not row:
            return {"ok": False, "skipped": "signal_missing"}
        targets = ", ".join(str(x) for x in (row.targets or [])) or "—"
        stop = (row.stop or {}).get("price") if row.stop else None
        prefix = "✏️ SIGNAL EDITED" if event == "edit" else "🛰 NEW SIGNAL"
        detail = ""
        if event == "edit" and raw_event_id is not None:
            try:
                from app.models.telegram_audit import TelegramSignalVersionRecord
                version = db.query(TelegramSignalVersionRecord).filter(TelegramSignalVersionRecord.raw_event_id == raw_event_id).first()
                if version:
                    flags = ", ".join(version.integrity_flags_json or []) or "text-only/no trading-rule change"
                    detail = f"\nChanged: {flags}"
            except Exception:
                detail = ""
        text = (
            f"{prefix}\n"
            f"{row.symbol} · {row.side.upper()}\n"
            f"Entry: {_entry_text(row.entry)}\n"
            f"Targets: {targets}\n"
            f"Stop: {stop if stop is not None else '—'}\n"
            f"Confidence: {row.confidence * 100:.2f}%\n"
            f"Status: simulation monitoring"
            f"{detail}"
        )
    return await send_message(text)


async def notify_simulation_event(sim_id: int, event: str, detail: str | None = None) -> dict[str, Any]:
    cfg = notification_config()
    gate = {
        "entry": "entry",
        "target": "targets",
        "stop": "stop",
        "complete": "simulation_complete",
        "expired": "simulation_complete",
    }.get(event)
    if gate and not cfg.get(gate, True):
        return {"ok": False, "skipped": f"{gate}_disabled"}
    with SessionLocal() as db:
        sim = db.get(LiveSignalSimulationRecord, sim_id)
        if not sim:
            return {"ok": False, "skipped": "simulation_missing"}
        signal = db.get(SignalRecord, sim.signal_id)
        hits = sum(1 for t in (sim.target_state_json or []) if t.get("hit"))
        total = len(sim.target_state_json or [])
        label = {
            "entry": "🟢 ENTRY ACTIVATED",
            "target": "🎯 TARGET HIT",
            "stop": "🔴 STOP HIT",
            "complete": "✅ SIMULATION COMPLETE",
            "expired": "⌛ SIMULATION EXPIRED",
        }.get(event, "TALOS UPDATE")
        text = (
            f"{label}\n"
            f"{sim.symbol}\n"
            f"Status: {sim.status}\n"
            f"Entry: {sim.entry_price if sim.entry_price is not None else 'waiting'}\n"
            f"TPs: {hits}/{total}\n"
            f"Current: {sim.current_price if sim.current_price is not None else '—'}\n"
        )
        if sim.mfe_pct is not None:
            text += f"MFE: {sim.mfe_pct:.4f}%\n"
        if sim.mae_pct is not None:
            text += f"MAE: {sim.mae_pct:.4f}%\n"
        if detail:
            text += detail
        if signal and signal.source_message_id:
            text += f"\nMessage ID: {signal.source_message_id}"
    return await send_message(text)


def _summary_payload(now: datetime) -> dict[str, Any]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        new_signals = db.query(SignalRecord).filter(SignalRecord.created_at >= start, SignalRecord.source == "telegram").count()
        sims = db.query(LiveSignalSimulationRecord).filter(LiveSignalSimulationRecord.started_at >= start).all()
        activated = [x for x in sims if x.entry_at is not None]
        tp1 = [x for x in activated if (x.target_state_json or []) and (x.target_state_json or [])[0].get("hit")]
        stopped = [x for x in sims if x.status == "stopped"]
        success = [x for x in sims if x.status == "success"]
        open_ = [x for x in sims if x.status == "open"]
        waiting = [x for x in sims if x.status == "waiting_entry"]
        paper = db.query(PaperAccountRecord).filter(PaperAccountRecord.name == "Default Paper").first()
        open_positions = db.query(PaperPositionRecord).filter(PaperPositionRecord.status == "open").count()
        latest_replay = db.query(AnalysisRunRecord).filter(AnalysisRunRecord.status == "complete").order_by(AnalysisRunRecord.id.desc()).first()
        replay = dict(latest_replay.summary_json or {}) if latest_replay else {}
    return {
        "new_signals": new_signals,
        "simulations": len(sims),
        "activated": len(activated),
        "tp1": len(tp1),
        "tp1_rate": (len(tp1) / len(activated) * 100) if activated else None,
        "stopped": len(stopped),
        "success": len(success),
        "open": len(open_),
        "waiting": len(waiting),
        "paper_equity": paper.equity if paper else None,
        "paper_realized": paper.realized_pnl if paper else None,
        "paper_open_positions": open_positions,
        "latest_replay_entry_rate": replay.get("entry_trigger_rate_pct"),
        "latest_replay_profit_factor": replay.get("profit_factor"),
    }


async def send_daily_summary(*, force: bool = False) -> dict[str, Any]:
    cfg = notification_config()
    now = datetime.now(timezone.utc)
    if not force:
        if not cfg.get("enabled", True) or not cfg.get("daily_summary", True):
            return {"ok": False, "skipped": "daily_summary_disabled"}
        if now.hour < int(cfg.get("daily_summary_hour_utc", 20)):
            return {"ok": False, "skipped": "not_due"}
        state = get_runtime("notification_state", {})
        if state.get("last_daily_summary_date") == now.date().isoformat():
            return {"ok": False, "skipped": "already_sent"}
    p = _summary_payload(now)
    rate = "—" if p["tp1_rate"] is None else f"{p['tp1_rate']:.2f}%"
    text = (
        f"📊 TALOS DAILY · {now.date().isoformat()} UTC\n"
        f"New recommendations: {p['new_signals']}\n"
        f"Live simulations: {p['simulations']}\n"
        f"Activated: {p['activated']} · Waiting: {p['waiting']} · Open: {p['open']}\n"
        f"TP1 reached: {p['tp1']}/{p['activated']} ({rate})\n"
        f"Stopped: {p['stopped']} · Completed all targets: {p['success']}\n"
        f"Paper equity: {p['paper_equity'] if p['paper_equity'] is not None else '—'}\n"
        f"Paper realized P&L: {p['paper_realized'] if p['paper_realized'] is not None else '—'}\n"
        f"Open paper positions: {p['paper_open_positions']}"
    )
    result = await send_message(text, force=force)
    if result.get("ok"):
        state = get_runtime("notification_state", {})
        state["last_daily_summary_date"] = now.date().isoformat()
        set_runtime("notification_state", state)
    return {**result, "summary": p}


async def notify_system_error(text: str) -> dict[str, Any]:
    return await send_message(f"⚠️ TALOS ERROR\n{text[:3500]}")


async def notify_signal_deleted(source_id: int, message_id: str) -> dict[str, Any]:
    cfg = notification_config()
    if not cfg.get("signal_edited", True):
        return {"ok": False, "skipped": "signal_edited_disabled"}
    text = (
        "🗑 SIGNAL MESSAGE DELETED\n"
        f"Source ID: {source_id}\n"
        f"Telegram message: {message_id}\n"
        "Talos preserved the previously observed versions for audit."
    )
    return await send_message(text)
