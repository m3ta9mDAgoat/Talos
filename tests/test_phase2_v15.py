"""Stage 1.5 regression — point in time, auth, independent baselines, approved UI."""
import sys
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.market_data import sample_bars
from core.native_replay_plan import NativePlanRequest,build_plan
from core.trend_sentiment import TrendSentimentRequest,evaluate
from core.sentiment_models import TextDocument,vader_batch
from server.api import create_app
from server.storage import Store


@pytest.fixture
def secured(tmp_path,monkeypatch):
    monkeypatch.setenv('TALOS_LAB_ADMIN_TOKEN','A'*40)
    monkeypatch.setenv('TALOS_LAB_WORKER_TOKEN','W'*40)
    client=TestClient(create_app(Store('sqlite:///'+str(tmp_path/'v15.db'))))
    header={'authorization':'Bearer '+'A'*40}
    bars=sample_bars('AAPL','2020-01-01','2022-06-30')
    up=client.post('/api/v2/research/datasets',headers=header,json={'symbol':'AAPL','bars':bars})
    assert up.status_code==201,up.text
    return client,header,up.json()['dataset_id'],bars


def replay_req(ref):
    return {'source_kind':'telegram','source_label':'test-author','symbol':'AAPL','dataset_id':ref,
            'horizon_sessions':5,'events':[{'post_id':'1','author':'a','text':'BUY AAPL',
               'first_observed_at':'2020-01-06T21:00:00Z'},
                {'post_id':'1','author':'a','text':'SELL AAPL','event_type':'edit',
               'first_observed_at':'2020-01-07T21:00:00Z'},
                {'post_id':'2','author':'b','text':'SELL AAPL',
               'first_observed_at':'2020-01-15T21:00:00Z'}]}


def sentiment_req(ref):
    return {'symbol':'AAPL','dataset_id':ref,'slow_ema':40,'fast_ema':10,
            'minimum_articles':1,'sentiment_threshold':0.1,
            'observations':[{'source':'rss','post_id':'news1','first_observed_at':'2020-08-04T22:00:00Z',
                             'symbol':'AAPL','score':0.65,'model':'vader','model_version':'1'}]}


def test_native_plan_same_event_rules_for_both_engines(secured):
    client,h,ref,bars=secured
    base=replay_req(ref)
    docs=[]
    for engine in ('lean','nautilus'):
        r=client.post('/api/v2/replay/native-plan',headers=h,json={**base,'engine':engine})
        assert r.status_code==200,r.text
        doc=r.json();docs.append(doc)
        assert doc['execution_status']=='PLAN_ONLY_NOT_EXECUTED'
        assert doc['native_execution_verified'] is False
        assert doc['live_trading_enabled'] is False
        assert len(doc['signal_events'])==2
        assert len(doc['excluded_or_revised'])==1
        assert doc['signal_events'][0]['entry_session']=='2020-01-07'
        assert doc['signal_events'][0]['entry_session']>doc['signal_events'][0]['observed_utc'][:10]
    assert docs[0]['ohlcv_sha256']==docs[1]['ohlcv_sha256']
    assert [(r['post_id'],r['entry_session'],r['exit_session']) for r in docs[0]['signal_events']]==[(r['post_id'],r['entry_session'],r['exit_session']) for r in docs[1]['signal_events']]


def test_native_plan_auth_and_data_rejection(secured):
    client,h,ref,bars=secured
    body={**replay_req(ref),'engine':'lean'}
    assert client.post('/api/v2/replay/native-plan',json=body).status_code==401
    assert client.post('/api/v2/replay/native-plan',headers=h,json={**body,'symbol':'MSFT'}).status_code==422
    assert client.post('/api/v2/replay/native-plan',headers=h,json={**body,'dataset_id':'0'*64}).status_code==404
    assert client.post('/api/v2/replay/native-plan',headers=h,json={**body,'engine':'live'}).status_code==422


def test_trend_sentiment_baseline_does_not_use_future_news(secured):
    client,h,ref,bars=secured
    r=client.post('/api/v2/research/trend-sentiment/evaluate',headers=h,json=sentiment_req(ref))
    assert r.status_code==200,r.text
    doc=r.json()
    assert doc['status']=='COMPLETED_REFERENCE_ONLY'
    assert doc['native_engine_execution'] is False
    assert doc['trend_only_baseline']['ending_equity']>0
    assert doc['sentiment_confirmed']['ending_equity']>0
    assert doc['trend_only_baseline']['ending_equity']!=doc['sentiment_confirmed']['ending_equity']
    # One article is not guaranteed to coincide with a positive trend: flat is valid.
    assert not doc['sentiment_confirmed']['fills'] or doc['sentiment_confirmed']['fills'][0]['date'] > '2020-08-04'
    assert client.post('/api/v2/research/trend-sentiment/evaluate',json=sentiment_req(ref)).status_code==401
    assert client.post('/api/v2/research/trend-sentiment/evaluate',headers=h,json={**sentiment_req(ref),'fast_ema':100}).status_code==422


def test_vader_optional_injected_scoring(secured):
    client,h,ref,bars=secured
    docs=[TextDocument.model_validate({'source':'rss','post_id':'post1','first_observed_at':'2020-01-06T12:00:00Z',
                'text':'Profits rose','symbol':'AAPL','language':'en'})]
    class Fake:
        def polarity_scores(self,t):return {'compound':0.42}
    scored=vader_batch(docs,scorer=Fake())
    assert scored[0]['score']==0.42
    assert scored[0]['text_sha256']
    assert not client.post('/api/v2/research/sentiment/vader',json=[docs[0].model_dump(mode='json')]).status_code==200


def test_approved_helmet_splash_and_navigation_preserved():
    orig=(ROOT/'web/APPROVED_ORIGINAL.html').read_text()
    integrated=(ROOT/'web/index.html').read_text()
    orig_helmet=orig[orig.index('<div class="intro-overlay"'):orig.index('<div class="shell">')]
    upgraded_helmet=integrated[integrated.index('<div class="intro-overlay"'):integrated.index('<div class="shell">')]
    assert orig_helmet==upgraded_helmet
    assert '<h1>Turn ideas into <span class="accent-text">evidence.</span></h1>' in integrated
    assert 'telegram_signal' in integrated
    assert 'Replay & Verification' in integrated
    assert 'prepareTalosNativeReplay()' in integrated
    assert 'runTalosTrendSentiment()' in integrated
