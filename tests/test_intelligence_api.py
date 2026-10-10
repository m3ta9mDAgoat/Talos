"""Route-level regression without requiring broker, Telethon or internet connections."""
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.api.routes.intelligence import router
from app.db.base import Base
from app.db.session import get_db
from app.models import *  # noqa: F403
from app.services.intelligence import Source, store_rows


def test_intelligence_endpoints_filter_and_provenance():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    def dependency():
        with Session(engine) as db:
            yield db
    app=FastAPI()
    app.include_router(router,prefix='/api/v1')
    app.dependency_overrides[get_db]=dependency
    ts=datetime.now(timezone.utc)
    with Session(engine) as db:
        store_rows(db,Source('offline','Offline test','crypto','rss','https://example.org',.9),
                   [{'external_id':'1','url':'https://example.org/article1','headline':'Bitcoin exploit detected','published_at':ts}],seen_at=ts)
        db.commit()
    client=TestClient(app)
    for path in ['/api/v1/intelligence/dashboard','/api/v1/intelligence/sources','/api/v1/intelligence/summary',
                 '/api/v1/intelligence/feed','/api/v1/intelligence/feed?symbol=BTC',
                 '/api/v1/intelligence/features?symbol=BTC']:
        assert client.get(path).status_code==200
    assert len(client.get('/api/v1/intelligence/feed?symbol=BTC').json()['items'])==1
    assert len(client.get('/api/v1/intelligence/feed?symbol=ETH').json()['items'])==0
    event=client.get('/api/v1/intelligence/feed').json()['items'][0]
    detail=client.get('/api/v1/intelligence/events/'+str(event['id'])).json()
    assert detail['revisions'][0]['revision']==1
    assert detail['event']['first_seen_at']
    assert client.get('/api/v1/intelligence/features?symbol=BTC&as_of=2030-01-01T00:00:00Z').status_code==422
