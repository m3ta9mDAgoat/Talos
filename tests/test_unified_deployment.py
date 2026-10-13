import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from server.api import create_app
from server.storage import Store
from server.deployment import validate_hosted
from talos_bot.runner import load_config
class UnifiedDeploymentTests(unittest.TestCase):
    def test_hosted_refuses_ephemeral_sqlite(self):
        with patch.dict(os.environ,{'TALOS_MODE':'hosted','TALOS_LAB_DATABASE_URL':'sqlite:///bad.db'}):
            with self.assertRaisesRegex(RuntimeError,'PostgreSQL'):validate_hosted()
    def test_hosted_rejects_shared_credentials(self):
        with patch.dict(os.environ,{'TALOS_MODE':'hosted','TALOS_LAB_DATABASE_URL':'postgresql://example',
                                    'TALOS_LAB_ADMIN_TOKEN':'x'*50,'TALOS_LAB_WORKER_TOKEN':'x'*50}):
            with self.assertRaisesRegex(RuntimeError,'distinct'):validate_hosted()
    def test_hosted_accepts_separate_tokens(self):
        with patch.dict(os.environ,{'TALOS_MODE':'hosted','TALOS_LAB_DATABASE_URL':'postgresql://example',
                                    'TALOS_LAB_ADMIN_TOKEN':'x'*50,'TALOS_LAB_WORKER_TOKEN':'y'*50}):
            self.assertEqual(validate_hosted()['mode'],'hosted')
    def test_authenticated_integration_endpoint(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store('sqlite:///'+str(Path(td)/'test.sqlite3'))
            with patch.dict(os.environ,{'TALOS_LAB_ADMIN_TOKEN':'admin_'+'x'*40,'TALOS_LAB_WORKER_TOKEN':'worker_'+'y'*40}):
                with TestClient(create_app(store)) as client:
                    self.assertEqual(client.get('/api/v2/integration/status').status_code,401)
                    good=client.get('/api/v2/integration/status',headers={'authorization':'Bearer '+'admin_'+'x'*40})
                    self.assertEqual(good.status_code,200)
                    self.assertEqual(client.get('/').status_code,200)
                    self.assertIn('TALOS',client.get('/lab').text)
                    self.assertFalse(good.json()['live_orders_enabled'])
    def test_verified_wins_and_losses_deduplicated(self):
        from talos_bot.db import BotDB
        with tempfile.TemporaryDirectory() as td:
            db=BotDB(Path(td)/'bot.sqlite3')
            observations=[{'post_id':'channel:1','author':'A','outcome':'WIN','net_return_pct':7,
                           'entry_date':'2026-01-01','policy':'next daily open'},
                          {'post_id':'channel:2','author':'B','outcome':'LOSS','net_return_pct':-3,
                           'entry_date':'2026-01-02','policy':'next daily open'}]
            self.assertEqual(db.record_verification(observations,'AAPL',5),2)
            self.assertEqual(db.record_verification(observations,'AAPL',5),0)
            self.assertEqual(db.outcome_summary()['counts'],{'WIN':1,'LOSS':1})

    def test_bot_external_configuration_independent(self):
        path=Path(__file__).parents[1]/'bot_config.example.json'
        cfg=load_config(path)
        self.assertEqual(cfg['talos_url'],'http://127.0.0.1:8018')
