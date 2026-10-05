from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.raw_event import RawEventRecord
from app.plugins.contracts import Event
from app.services.ingestion import persist_events


def test_bulk_ingestion_dedupes_existing_and_batch_duplicates():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    events = [
        Event(source='telegram', external_id='1', timestamp=now, payload={'text': 'a'}, metadata={'event_type': 'message'}),
        Event(source='telegram', external_id='2', timestamp=now, payload={'text': 'b'}, metadata={'event_type': 'message'}),
    ]
    with Session(engine) as db:
        inserted, skipped = persist_events(db, 'chan', events + [events[0]])
        assert inserted == 2
        assert skipped == 1
        assert db.query(RawEventRecord).count() == 2
        inserted2, skipped2 = persist_events(db, 'chan', events)
        assert inserted2 == 0
        assert skipped2 == 2
