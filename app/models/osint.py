from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Float, Text, UniqueConstraint
from app.db.base import Base

class OsintItem(Base):
    __tablename__ = 'osint_items'
    id = Column(Integer, primary_key=True)
    source = Column(String(100), nullable=False)
    category = Column(String(40), nullable=False)
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=False)
    fingerprint = Column(String(64), nullable=False, unique=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    importance = Column(Float, default=0)
    summary = Column(Text, nullable=True)
