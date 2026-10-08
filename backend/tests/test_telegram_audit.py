from datetime import datetime, timezone

from app.models.telegram_audit import TelegramSignalVersionRecord
from app.schemas.analysis import AnalyzeSourceRequest
from app.services.processing import _diff


def test_replay_defaults_to_edit_time_policy():
    cfg = AnalyzeSourceRequest()
    assert cfg.edit_policy == "from_edit"


def test_revision_diff_flags_trading_rule_changes():
    prev = TelegramSignalVersionRecord(
        raw_event_id=1,
        source_id=1,
        source_identifier="chan",
        source_message_id="10",
        revision_type="message",
        revision_at=datetime.now(timezone.utc),
        original_message_at=datetime.now(timezone.utc),
        symbol="BTC/USDT",
        side="buy",
        market_type="spot",
        confidence=0.95,
        status="parsed",
        entry_json={"type":"price","price":100.0},
        stop_json={"price":95.0,"trigger":"price_touch"},
        targets_json=[105.0],
        raw_text="BTC buy 100 TP 105 SL 95",
    )
    changes, flags = _diff(prev, {
        "symbol":"BTC/USDT","side":"buy",
        "entry":{"type":"price","price":101.0},
        "stop":{"price":96.0,"trigger":"price_touch"},
        "targets":[106.0],"raw_text":"BTC buy 101 TP 106 SL 96",
    })
    assert "entry_changed" in flags
    assert "stop_changed" in flags
    assert "targets_changed" in flags
    assert changes["entry"]["after"]["price"] == 101.0
