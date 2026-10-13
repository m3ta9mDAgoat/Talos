"""Phanes manual-export integration. Synthetic workbook; not a vendor-produced file."""
import base64,io,sys,zipfile
from pathlib import Path
from openpyxl import Workbook
from fastapi.testclient import TestClient
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.phanes_export import workbook_to_archive
from server.api import create_app
from server.storage import Store


def fixture_workbook():
    w=Workbook();c=w.active;c.title='Calls'
    c.append(['Caller','Token','Entry MC','ATH Multiple','Date','CA'])
    c.append(['@alice','SOL-COIN',20000,5.2,'2026-10-12','0xabc'])
    c.append(['@bob','SOL-TWO',80000,1.0,'2026-10-12','0xdef'])
    w.create_sheet('Summary').append(['Total','2'])
    w.create_sheet('Top Callers').append(['@alice','2'])
    b=io.BytesIO();w.save(b);return b.getvalue()


def test_extracts_source_ledger_without_claiming_trading_returns():
    raw=fixture_workbook();x=workbook_to_archive(raw,'Phanes-leaderboard.xlsx')
    assert x['call_count']==2
    assert x['calls'][0]['fields']['Caller']=='@alice'
    assert x['provider_authenticated'] is False
    assert x['price_execution_verified'] is False
    assert x['notifications_sent'] is False
    assert x['live_orders_enabled'] is False
    assert x['sheet_names']==['Calls','Summary','Top Callers']


def test_invalid_or_wrong_format_rejected():
    with pytest.raises(ValueError):workbook_to_archive(b'malformed','calls.xlsx')
    with pytest.raises(ValueError):workbook_to_archive(fixture_workbook(),'calls.xls')
    w=Workbook();w.active.title='Wrong';b=io.BytesIO();w.save(b)
    with pytest.raises(ValueError):workbook_to_archive(b.getvalue(),'calls.xlsx')


def test_authenticated_import_persists_deduplicated_immutable_archive(tmp_path,monkeypatch):
    monkeypatch.setenv('TALOS_LAB_ADMIN_TOKEN','A'*40)
    monkeypatch.setenv('TALOS_LAB_WORKER_TOKEN','W'*40)
    dburl='sqlite:///'+str(tmp_path/'persist.db')
    c=TestClient(create_app(Store(dburl)))
    body={'filename':'leaderboard.xlsx','xlsx_base64':base64.b64encode(fixture_workbook()).decode()}
    endpoint='/api/v2/replay/phanes/import'
    assert c.post(endpoint,json=body).status_code==401
    h={'authorization':'Bearer '+'A'*40}
    a=c.post(endpoint,headers=h,json=body)
    assert a.status_code==201,a.text
    data=a.json();assert data['call_count']==2
    assert data['integration_type']=='manual_official_export'
    assert data['replay_ready'] is False
    assert data['native_engine_execution'] is False
    b=c.post(endpoint,headers=h,json=body)
    assert b.status_code==201
    assert b.json()['archive_id']==data['archive_id']
    fresh=Store(dburl)
    assert len(fresh.get_archive(data['archive_id']))==2
    assert c.post(endpoint,headers=h,json={**body,'xlsx_base64':'%%%'}).status_code==422


def test_defends_against_zip_bomb_length_limit():
    large=fixture_workbook()
    with pytest.raises(ValueError):workbook_to_archive(large*600,'huge.xlsx')
