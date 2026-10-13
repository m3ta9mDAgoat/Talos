from pathlib import Path
from core.contracts import JobSubmit

def test_native_fixture_payload_valid_and_not_generic():
 payload={'kind':'native_parity','request':{'engine':'lean_parity',
    'data':{'source':'native_fixture','symbol':'AAPL','start':'2012-01-01','end':'2022-05-31'},
    'strategy':{'plugin':'ten_month_sma_fixture'},
    'risk':{'initial_cash':10000,'max_allocation':0.998,'fee_bps':10,'slippage_bps':0},
    'walk_forward':False,'monte_carlo':0}}
 req=JobSubmit.model_validate(payload)
 assert req.kind=='native_parity'
 assert req.request.strategy.plugin=='ten_month_sma_fixture'

def test_native_ui_shows_verified_fixture_scope():
 html=(Path(__file__).resolve().parents[1]/'web'/'index.html').read_text()
 assert 'function runNativeStage3()' in html
 assert "windows_worker?.status!=='online'" in html
 assert "stage3_harness_detected" in html
 assert "kind:'native_parity'" in html
 assert 'not a generic' in html.lower()
