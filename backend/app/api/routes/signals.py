from fastapi import APIRouter

from app.schemas.signal import ParseSignalRequest, ParseSignalResponse
from app.services.signal_parser import telegram_signal_parser

router = APIRouter(prefix="/signals", tags=["signals"])


@router.post("/parse", response_model=ParseSignalResponse)
async def parse_signal(request: ParseSignalRequest) -> ParseSignalResponse:
    return telegram_signal_parser.parse(
        request.text,
        timestamp=request.timestamp,
        source_id=request.source_id,
        source_message_id=request.source_message_id,
        default_quote=request.default_quote,
    )

@router.get("/live-simulations")
def live_simulations(limit: int = 300):
    from app.db.session import SessionLocal
    from app.models.trading import LiveSignalSimulationRecord
    with SessionLocal() as db:
        rows = db.query(LiveSignalSimulationRecord).order_by(LiveSignalSimulationRecord.id.desc()).limit(max(1, min(limit, 2000))).all()
        return [
            {
                "id": r.id,
                "signal_id": r.signal_id,
                "source_id": r.source_id,
                "symbol": r.symbol,
                "side": r.side,
                "status": r.status,
                "broker_id": r.broker_id,
                "market_symbol": r.market_symbol,
                "entry_price": r.entry_price,
                "entry_at": r.entry_at,
                "current_price": r.current_price,
                "targets": r.target_state_json or [],
                "stop_hit": r.stop_hit,
                "stop_at": r.stop_at,
                "mfe_pct": r.mfe_pct,
                "mae_pct": r.mae_pct,
                "started_at": r.started_at,
                "last_checked_at": r.last_checked_at,
                "completed_at": r.completed_at,
                "state": r.state_json or {},
            }
            for r in rows
        ]
