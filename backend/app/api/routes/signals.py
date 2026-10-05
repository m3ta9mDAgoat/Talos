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
