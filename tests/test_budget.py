import concurrent.futures
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from jetson_bot.budget import Application, Ledger, Rejected, TICKS, billed_ticks, validate

CONFIG=dict(model="grok-4.7",upstream="https://api.x.ai/v1", max_output_tokens=8192,
            max_input_tokens=131072,input_ticks_per_token=20000,output_ticks_per_token=60000)


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"budget.db"
        self.time=datetime(2026,10,30,16,tzinfo=timezone.utc)
        self.ledger=Ledger(self.path,cap_ticks=1000,now=lambda:self.time)

    def tearDown(self):
        self.temp.cleanup()

    def test_status_advertises_conservative_resume_headroom_without_secrets(self):
        app=Application(CONFIG,self.ledger,'PRIVATE-LOCAL-TOKEN','PRIVATE-PROVIDER-KEY')
        status=app.snapshot()
        self.assertEqual(status['max_request_reservation_usd'],0.311296)
        self.assertNotIn('PRIVATE',json.dumps(status))

    def test_reservation_refund_and_idempotent_settlement(self):
        ticket=self.ledger.reserve(800)
        with self.assertRaises(Rejected):
            self.ledger.reserve(201)
        self.ledger.settle(ticket,300,"billed")
        self.ledger.settle(ticket,0,"billed")
        self.assertEqual(self.ledger.snapshot()["committed_usd"],300/TICKS)
        self.ledger.reserve(700)

    def test_restart_and_unknown_outcome_keeps_reservation(self):
        ticket=self.ledger.reserve(800)
        self.ledger.settle(ticket)
        reopened=Ledger(self.path,cap_ticks=1000,now=lambda:self.time)
        self.assertEqual(reopened.snapshot()["outstanding_reservations"],1)
        with self.assertRaises(Rejected): reopened.reserve(201)

    def test_simultaneous_admission_is_atomic(self):
        def reserve(_):
            try: return self.ledger.reserve(600)
            except Rejected: return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(reserve,range(8)))
        self.assertEqual(sum(x is not None for x in results),1)

    def test_toronto_midnight_and_operator_pause(self):
        self.time=datetime(2026,10,31,3,59,tzinfo=timezone.utc)
        self.ledger.reserve(1000)
        self.time=datetime(2026,10,31,4,0,tzinfo=timezone.utc)
        self.assertEqual(self.ledger.snapshot()["day"],"2026-10-31")
        self.ledger.reserve(900)
        self.ledger.pause(True)
        self.time=datetime(2026,11,1,4,1,tzinfo=timezone.utc)
        with self.assertRaises(Rejected): self.ledger.reserve(1)
        self.ledger.pause(False)
        self.ledger.reserve(1)

    def test_dst_fall_back_has_one_budget_day(self):
        self.time=datetime(2026,11,1,5,30,tzinfo=timezone.utc)
        self.ledger.reserve(1000)
        self.time=datetime(2026,11,1,6,30,tzinfo=timezone.utc)
        with self.assertRaises(Rejected): self.ledger.reserve(1)

    def test_unexpected_pricing_pauses_paid_work(self):
        ticket=self.ledger.reserve(100)
        self.ledger.settle(ticket,101,"billed")
        self.assertTrue(self.ledger.snapshot()["paused"])

    def test_input_output_and_paid_tool_bounds(self):
        base=dict(model="grok-4.7",messages=[dict(role="user",content="Hello")])
        forwarded,reserve=validate(base,CONFIG)
        self.assertFalse(forwarded["stream"])
        self.assertGreater(reserve,8192*60000)
        for change in (dict(model="unpriced"),dict(stream=True),dict(n=2),
                       dict(max_tokens=0),dict(search_parameters={"mode":"auto"}),
                       dict(tools=[dict(type="web_search")]),
                       dict(messages=[dict(role="user",content="a"*140000)]),
                       dict(messages=[dict(role="user",content=[dict(type="image_url")])])):
            with self.assertRaises(Rejected): validate(dict(base,**change),CONFIG)
        bounded,_=validate(dict(base,max_tokens=32768),CONFIG)
        self.assertEqual(bounded["max_completion_tokens"],8192)

    def test_priced_images_and_token_settlement(self):
        base=dict(model="grok-4.7",messages=[dict(role="user",content="Hello")])
        _,text_reserve=validate(base,CONFIG)
        url="data:image/jpeg;base64,"+("A"*80)
        image=dict(type="image_url",image_url={"url":url})
        payload=dict(model="grok-4.7",messages=[dict(role="user",content=[
            dict(type="text",text="Describe this"),image])])
        forwarded,reserve=validate(payload,CONFIG)
        self.assertEqual(forwarded["messages"][0]["content"][1]["image_url"]["url"],url)
        self.assertGreaterEqual(reserve,text_reserve+2000*20000)
        with self.assertRaises(Rejected):
            validate(dict(model="grok-4.7",messages=[dict(role="user",content=[image]*5)]),CONFIG)
        huge=dict(type="image_url",image_url={"url":"data:image/png;base64,"+("A"*1_500_001)})
        with self.assertRaises(Rejected):
            validate(dict(model="grok-4.7",messages=[dict(role="user",content=[huge])]),CONFIG)
        self.assertEqual(billed_ticks(dict(prompt_tokens=10,completion_tokens=2),CONFIG),10*20000+2*60000)
        self.assertEqual(billed_ticks(dict(cost_in_usd_ticks=15,prompt_tokens=10,completion_tokens=2),CONFIG),15)
        self.assertIsNone(billed_ticks(dict(prompt_tokens=1.5),CONFIG))
        ledger=Ledger(self.path.with_name("tokens.db"),cap_ticks=TICKS,now=lambda:self.time)
        app=Application(CONFIG,ledger,"local-token","fake-test-key")
        class TokenBill:
            def __enter__(self): return self
            def __exit__(self,*_): pass
            def read(self,*_):
                return json.dumps(dict(usage=dict(prompt_tokens=10,completion_tokens=2),choices=[])).encode()
        with patch("jetson_bot.budget.urlopen",return_value=TokenBill()):
            app.complete(dict(model="grok-4.7",messages=[dict(role="user",content="Hello")]))
        self.assertEqual(ledger.snapshot()["billed_usd"],(10*20000+2*60000)/TICKS)
        self.assertEqual(ledger.snapshot()["outstanding_reservations"],0)

    def test_exact_provider_bill_cache_refund_and_retries(self):
        ledger=Ledger(self.path.with_name("api.db"),cap_ticks=TICKS,now=lambda:self.time)
        app=Application(CONFIG,ledger,"local-token","fake-test-key")
        class Response:
            def __enter__(self): return self
            def __exit__(self,*_): pass
            def read(self,*_): return json.dumps(dict(usage=dict(cost_in_usd_ticks=12345),choices=[])).encode()
        payload=dict(model="grok-4.7",messages=[dict(role="user",content="Hello")])
        with patch("jetson_bot.budget.urlopen",return_value=Response()):
            app.complete(payload); app.complete(payload)
        self.assertEqual(ledger.snapshot()["billed_usd"],24690/TICKS)
        with patch("jetson_bot.budget.urlopen",side_effect=URLError("timeout")):
            with self.assertRaises(Rejected): app.complete(payload)
        self.assertEqual(ledger.snapshot()["outstanding_reservations"],1)

    def test_missing_usage_retains_reservation_and_429_releases(self):
        ledger=Ledger(self.path.with_name("api.db"),cap_ticks=TICKS,now=lambda:self.time)
        app=Application(CONFIG,ledger,"local-token","fake-test-key")
        class Response:
            def __enter__(self): return self
            def __exit__(self,*_): pass
            def read(self,*_): return b'{"choices":[]}'
        payload=dict(model="grok-4.7",messages=[dict(role="user",content="Hello")])
        with patch("jetson_bot.budget.urlopen",return_value=Response()): app.complete(payload)
        committed=ledger.snapshot()["committed_usd"]
        with patch("jetson_bot.budget.urlopen",side_effect=HTTPError("https://test",429,"",{},None)):
            with self.assertRaises(Rejected): app.complete(payload)
        self.assertEqual(ledger.snapshot()["committed_usd"],committed)


if __name__=="__main__": unittest.main()
