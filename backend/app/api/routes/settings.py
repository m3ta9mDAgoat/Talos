from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.notifications import discover_chats, notification_config, send_daily_summary, send_message, update_notification_config
from app.services.security import delete_secret, get_runtime, get_secret, list_secret_status, save_secret, set_runtime

router = APIRouter(prefix="/settings", tags=["settings"])

PROVIDERS = {
    "telegram": ["api_id", "api_hash", "session_string"],
    "telegram_notify": ["bot_token", "chat_id"],
    "binance": ["api_key", "api_secret"],
    "okx": ["api_key", "api_secret", "password"],
    "bybit": ["api_key", "api_secret"],
    "bitget": ["api_key", "api_secret", "password"],
    "kucoin": ["api_key", "api_secret", "password"],
    "mexc": ["api_key", "api_secret"],
    "ai": ["base_url", "model", "api_key"],
}


class SecretWrite(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


class RuntimeWrite(BaseModel):
    live_trading_enabled: bool | None = None


class NotificationWrite(BaseModel):
    enabled: bool | None = None
    new_signal: bool | None = None
    signal_edited: bool | None = None
    entry: bool | None = None
    targets: bool | None = None
    stop: bool | None = None
    simulation_complete: bool | None = None
    daily_summary: bool | None = None
    daily_summary_hour_utc: int | None = Field(default=None, ge=0, le=23)
    simulation_horizon_days: int | None = Field(default=None, ge=1, le=365)


@router.get("/vault")
def vault_status():
    existing = {x["provider"]: x for x in list_secret_status()}
    return [
        {
            "provider": provider,
            "required_fields": fields,
            "configured": provider in existing,
            "configured_fields": existing.get(provider, {}).get("fields", []),
            "updated_at": existing.get(provider, {}).get("updated_at"),
        }
        for provider, fields in PROVIDERS.items()
    ]


@router.put("/vault/{provider}")
def put_secret(provider: str, req: SecretWrite):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    allowed = set(PROVIDERS[provider])
    bad = sorted(set(req.values) - allowed)
    if bad:
        raise HTTPException(400, f"Unsupported fields: {', '.join(bad)}")
    current = get_secret(provider)
    merged = {**current, **{k: v for k, v in req.values.items() if v}}
    required = [x for x in PROVIDERS[provider] if not (provider == "ai" and x == "api_key")]
    if provider == "telegram_notify":
        required = ["bot_token"]
    missing = [x for x in required if not merged.get(x)]
    if missing:
        raise HTTPException(400, f"Missing required fields: {', '.join(missing)}")
    save_secret(provider, merged)
    return {"ok": True, "provider": provider, "configured_fields": sorted(merged.keys())}


@router.delete("/vault/{provider}")
def remove_secret(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    return {"ok": delete_secret(provider), "provider": provider}


@router.get("/runtime")
def runtime():
    cfg = get_runtime("trading", {"live_trading_enabled": False})
    return {"live_trading_enabled": bool(cfg.get("live_trading_enabled", False))}


@router.put("/runtime")
def update_runtime(req: RuntimeWrite):
    cfg = get_runtime("trading", {"live_trading_enabled": False})
    if req.live_trading_enabled is not None:
        cfg["live_trading_enabled"] = bool(req.live_trading_enabled)
    set_runtime("trading", cfg)
    return cfg


@router.get("/notifications")
def notification_settings():
    return notification_config()


@router.put("/notifications")
def update_notifications(req: NotificationWrite):
    values: dict[str, Any] = {k: v for k, v in req.model_dump().items() if v is not None}
    return update_notification_config(values)


@router.post("/notifications/test")
async def test_notification():
    try:
        result = await send_message("✅ TALOS notification bot connected successfully.", force=True)
        if not result.get("ok"):
            raise RuntimeError(result.get("skipped") or "Notification test failed")
        return result
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/notifications/discover-chats")
async def notification_chats():
    try:
        return await discover_chats()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/notifications/daily-summary")
async def notification_daily_summary():
    try:
        return await send_daily_summary(force=True)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
