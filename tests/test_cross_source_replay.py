from datetime import date, timedelta
from fastapi.testclient import TestClient
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.api import create_app
from server.storage import Store
from core.replay_verify import VerificationRequest,verify
from core.market_data import sample_bars
import pytest


def payload(id, symbol='AAPL'):
    return {'source_kind':'telegram','source_label':'A channel / user observation',
            'dataset_id':id,'symbol':symbol,'horizon_sessions':2,
            'events':[
                {'post_id':'a','author':'author1','first_observed_at':'2020-01-06T18:00:00Z',
                 'claimed_published_at':'2019-01-01T10:00:00Z','text':'BUY AAPL','event_type':'new'},
                {'post_id':'a','author':'author1','first_observed_at':'2020-01-08T15:00:00Z',
                 'text':'SELL AAPL','event_type':'edit'},
                {'post_id':'a','author':'author1','first_observed_at':'2020-01-09T15:00:00Z',
                 'event_type':'delete','text':''},
                {'post_id':'b','author':'author2','first_observed_at':'2020-01-10T18:00:00Z',
                 'text':'SELL AAPL','event_type':'new'}]}


def test_replay_no_backdate_revision_does_not_retroactively_trade():
    bars=sample_bars('AAPL','2020-01-01','2020-03-01')
    data={'symbol':'AAPL','bars':bars}
    request=VerificationRequest.model_validate(payload('f'*64))
    r=verify(request,data)
    assert r['schema']=='talos.replay.cross_source_validation.v1'
    assert r['summary']['recommendations_scored']==2
    assert r['summary']['revisions_retained']==2
    assert r['recommendations'][0]['entry_date']=='2020-01-07'
    assert r['recommendations'][0]['first_observed_at']=='2020-01-06T18:00:00+00:00'
    assert r['source_authenticated'] is False
    assert r['notification_count']==0


def test_replay_missing_market_data_excludes_without_fake_results():
    bars=sample_bars('AAPL','2020-01-01','2020-01-13')
    req=VerificationRequest.model_validate({**payload('f'*64),'events':[{'post_id':'x','author':'u',
      'first_observed_at':'2020-01-13T20:00:00Z','text':'BUY AAPL'}]})
    result=verify(req,{'symbol':'AAPL','bars':bars})
    assert result['summary']['recommendations_scored']==0
    assert result['excluded'][0]['reason']=='insufficient_future_market_data'
    assert result['summary']['accuracy_pct_of_scored'] is None


def test_replay_cross_source_compatible():
    bars=sample_bars('AAPL','2020-01-01','2020-02-01')
    for k in ('telegram','x','rss','website','other'):
        r=verify(VerificationRequest.model_validate({**payload('f'*64),'source_kind':k}),{'symbol':'AAPL','bars':bars})
        assert r['summary']['source_kind']==k
        assert r['summary']['recommendations_scored']==2


def test_replay_admin_auth_market_dataset_and_validation(tmp_path,monkeypatch):
    monkeypatch.setenv('TALOS_LAB_ADMIN_TOKEN','A'*40)
    monkeypatch.setenv('TALOS_LAB_WORKER_TOKEN','W'*40)
    client=TestClient(create_app(Store('sqlite:///'+str(tmp_path/'test.db'))))
    headers={'authorization':'Bearer '+'A'*40}
    rows=sample_bars('AAPL','2020-01-01','2020-03-01')
    dataset=client.post('/api/v2/research/datasets',headers=headers,json={'symbol':'AAPL','bars':rows})
    assert dataset.status_code==201,dataset.text
    p=payload(dataset.json()['dataset_id'])
    assert client.post('/api/v2/replay/verify',json=p).status_code==401
    assert client.post('/api/v2/replay/verify',headers={'authorization':'Bearer '+'W'*40},json=p).status_code==401
    yes=client.post('/api/v2/replay/verify',headers=headers,json=p)
    assert yes.status_code==200,yes.text
    assert yes.json()['summary']['wins']+yes.json()['summary']['losses']+yes.json()['summary']['flat']==2
    assert client.post('/api/v2/replay/verify',headers=headers,json={**p,'symbol':'MSFT'}).status_code==422
    assert client.post('/api/v2/replay/verify',headers=headers,json={**p,'dataset_id':'f'*64}).status_code==404


def test_replay_bad_timestamp_and_duplicate_original():
    bars=sample_bars('AAPL','2020-01-01','2020-03-01')
    p=payload('f'*64)
    p['events'].append({'post_id':'a','author':'u','first_observed_at':'2020-01-10T18:00:00Z',
                        'text':'BUY AAPL'})
    r=verify(VerificationRequest.model_validate(p),{'symbol':'AAPL','bars':bars})
    assert any(x['reason']=='duplicate_original_post_id' for x in r['excluded'])
    p['events'][0]['first_observed_at']='2020-01-06T18:00:00'
    with pytest.raises(ValueError):VerificationRequest.model_validate(p)
