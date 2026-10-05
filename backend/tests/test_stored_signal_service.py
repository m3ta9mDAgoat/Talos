from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.enums import SourceStatus, SourceType
from app.db.base import Base
from app.models.raw_event import RawEventRecord
from app.models.signal import SignalRecord
from app.models.source import SourceRecord
from app.services.stored_signal_service import parse_stored_messages


def test_stored_parser_uses_latest_revision_and_upserts():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with Session(engine) as db:
        source = SourceRecord(
            name="Signals",
            type=SourceType.TELEGRAM,
            identifier="test_channel",
            status=SourceStatus.READY,
            config={"default_quote": "USDT"},
        )
        db.add(source)
        db.flush()
        original = RawEventRecord(
            dedupe_key="original",
            source_type="telegram",
            source_identifier=source.identifier,
            external_id="10",
            occurred_at=t0,
            ingested_at=t0,
            event_type="message",
            text="RLC/USDT BUY 0.41 - 0.43 TP1: 0.44 STOP 0.40",
            payload={},
            metadata_json={},
        )
        edited = RawEventRecord(
            dedupe_key="edited",
            source_type="telegram",
            source_identifier=source.identifier,
            external_id="10",
            occurred_at=t0,
            ingested_at=t0 + timedelta(minutes=5),
            event_type="edit",
            text="RLC/USDT BUY 0.42 - 0.43 TP1: 0.45 STOP 0.40",
            payload={},
            metadata_json={"edit_date": (t0 + timedelta(minutes=5)).isoformat()},
        )
        db.add_all([original, edited])
        db.commit()

        result = parse_stored_messages(db, source, limit=100)
        assert result["created"] == 1
        row = db.query(SignalRecord).one()
        assert row.entry["min"] == 0.42
        assert row.targets == [0.45]
        assert row.metadata_json["historical_integrity"] == "edited"

        result2 = parse_stored_messages(db, source, limit=100)
        assert result2["updated"] == 1
        assert db.query(SignalRecord).count() == 1
