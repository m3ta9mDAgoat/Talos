from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.enums import SourceStatus, SourceType
from app.db.base import Base
from app.models.analysis import MessageLabelRecord
from app.models.raw_event import RawEventRecord
from app.models.signal import SignalRecord
from app.models.source import SourceRecord
from app.services.stored_signal_service import parse_stored_messages


def test_manual_new_signal_label_is_honored():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        source = SourceRecord(name='Signals', type=SourceType.TELEGRAM, identifier='chan', status=SourceStatus.READY, config={})
        db.add(source); db.flush()
        raw = RawEventRecord(
            dedupe_key='x', source_type='telegram', source_identifier='chan', external_id='1',
            occurred_at=now, ingested_at=now, event_type='message',
            text='RLC/USDT BUY 0.41 - 0.43 TP1 0.44 STOP 0.40', payload={}, metadata_json={}
        )
        db.add(raw); db.flush()
        db.add(MessageLabelRecord(raw_event_id=raw.id, source_id=source.id, predicted_class='unknown', predicted_confidence=.45, manual_class='new_signal', reasons_json=[]))
        db.commit()
        result = parse_stored_messages(db, source, limit=100)
        assert result['parsed'] == 1
        assert db.query(SignalRecord).count() == 1
