from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.security import delete_secret, get_runtime, get_secret, list_secret_status, save_secret, set_runtime

router = APIRouter(prefix="/settings", tags=["settings"])

PROVIDERS = {
    "telegram": ["api_id", "api_hash", "session_string"],
    "binance": ["api_key", "api_secret"],
    "okx": ["api_key", "api_secret", "password"],
    "bybit": ["api_key", "api_secret"],
    "bitget": ["api_key", "api_secret", "password"],
    "kucoin": ["api_key", "api_secret", "password"],
    "mexc": ["api_key", "api_secret"],
}


class SecretWrite(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


class RuntimeWrite(BaseModel):
    live_trading_enabled: bool | None = None


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
    missing = [x for x in PROVIDERS[provider] if not merged.get(x)]
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
