from app.db.base import Base
from app.db.session import engine
from app.models import (  # noqa: F401
    AnalysisRunRecord,
    MessageLabelRecord,
    RawEventRecord,
    SignalBacktestRecord,
    SignalRecord,
    SourceRecord,
)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
