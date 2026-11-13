"""Offline tests on synthetic Dhan-format bars. Run: python3 -m unittest discover -s tests -v"""

import datetime
import io
import json
import os
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from idt import core, data, strategy, timemachine  # noqa: E402
import trader  # noqa: E402

CFG = dict(core.DEFAULTS)


def ist_epoch(dt):
    return (dt - datetime.timedelta(hours=5, minutes=30) - datetime.datetime(1970, 1, 1)).total_seconds()


def make_days(n=70, seed=1, last=datetime.date(2026, 11, 11)):
    rng = random.Random(seed)
    days, d = [], last
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= datetime.timedelta(days=1)
    days.reverse()
    out = {"open": [], "high": [], "low": [], "close": [], "volume": [], "timestamp": []}
    price = 1000.0
    for day in days:
        t = datetime.datetime.combine(day, core.OPEN)
        for i in range(75):                       # 9:15 .. 15:25
            o = price
            c = o * (1 + rng.gauss(0.0002, 0.003))
            h = max(o, c) * (1 + abs(rng.gauss(0, 0.001)))
            l = min(o, c) * (1 - abs(rng.gauss(0, 0.001)))
            for k, v in zip(("open", "high", "low", "close"), (o, h, l, c)):
                out[k].append(round(v, 2))
            out["volume"].append(rng.randint(10000, 50000))
            out["timestamp"].append(ist_epoch(t))
            price = c
            t += datetime.timedelta(minutes=5)
    return out


def day_from(rows):
    """rows: list of (hh:mm, o, h, l, c) on 2026-11-11 -> bars"""
    out = []
    for hm, o, h, l, c in rows:
        t = datetime.datetime.strptime("2026-11-11 " + hm, "%Y-%m-%d %H:%M")
        out.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": 1000})
    return out


ORB_DAY = day_from([
    ("09:15", 100, 102, 99, 101), ("09:20", 101, 101.5, 100, 100.5), ("09:25", 100.5, 101.8, 100.2, 101),
    ("09:30", 101, 101.9, 100.8, 101.5),            # closes below 102: no trigger
    ("09:35", 101.5, 102.6, 101.4, 102.4),          # first close above 102 -> trigger at 09:40
    ("09:40", 102.4, 103, 102.2, 102.8),
])


class TestParse(unittest.TestCase):
    def test_dhan_arrays_to_ist(self):
        j = {"open": [1], "high": [2], "low": [0.5], "close": [1.5], "volume": [9],
             "timestamp": [ist_epoch(datetime.datetime(2026, 11, 11, 9, 15))]}
        b = data.parse_dhan_bars(j)[0]
        self.assertEqual(b["t"], datetime.datetime(2026, 11, 11, 9, 15))
        self.assertEqual(data.parse_dhan_bars({"data": j})[0]["c"], 1.5)


class TestORB(unittest.TestCase):
    def test_waiting_while_range_forms(self):
        s = strategy.check(ORB_DAY, datetime.datetime(2026, 11, 11, 9, 25), CFG)
        self.assertEqual(s["status"], "waiting")

    def test_unfinished_bar_does_not_trigger(self):
        s = strategy.check(ORB_DAY, datetime.datetime(2026, 11, 11, 9, 38), CFG)
        self.assertEqual(s["status"], "waiting")

    def test_trigger_on_first_close_above(self):
        s = strategy.check(ORB_DAY, datetime.datetime(2026, 11, 11, 9, 41), CFG)
        self.assertEqual(s["status"], "triggered")
        self.assertEqual(s["time"], "2026-11-11 09:40")
        self.assertEqual(s["price"], 102.4)
        self.assertEqual(s["range"], {"high": 102, "low": 99, "mid": 100.5})

    def test_breakout_size_filter(self):
        t = datetime.datetime(2026, 11, 11, 9, 41)
        self.assertEqual(strategy.check(ORB_DAY, t, dict(CFG, min_over_pct=0.2))["status"], "triggered")  # +0.39%
        self.assertEqual(strategy.check(ORB_DAY, t, dict(CFG, min_over_pct=0.5))["status"], "waiting")

    def test_expired(self):
        cfg = dict(CFG, no_entries_after="09:35")
        s = strategy.check(ORB_DAY, datetime.datetime(2026, 11, 11, 10, 0), cfg)
        self.assertEqual(s["status"], "expired")


class TestPlanSizeRisk(unittest.TestCase):
    def setUp(self):
        self.plan = strategy.exit_plan(102.4, {"high": 102, "low": 99, "mid": 100.5}, CFG)

    def test_plan(self):
        self.assertEqual(self.plan["stop"], 100.5)
        self.assertEqual(self.plan["risk_per_share"], 1.9)
        self.assertEqual(self.plan["target"], 105.25)   # 102.4 + 1.5*1.9 = 105.25

    def test_size_by_risk(self):
        sz = strategy.size(self.plan, dict(CFG, max_loss_per_trade=1000, capital=1000000))
        self.assertEqual(sz["qty"], 526)                 # floor(1000/1.9)
        self.assertEqual(sz["limited_by"], "risk")

    def test_size_by_capital(self):
        sz = strategy.size(self.plan, dict(CFG, capital=10000, max_position_pct=50))
        self.assertEqual(sz["qty"], 48)                  # floor(5000/102.4)
        self.assertEqual(sz["limited_by"], "capital")

    def test_risk_blocks(self):
        o = dict(self.plan, **strategy.size(self.plan, CFG))
        now = datetime.datetime(2026, 11, 11, 10, 0)
        self.assertEqual(strategy.risk_check(o, CFG, 0, 0, now)[0], "PASS")
        self.assertEqual(strategy.risk_check(o, CFG, 3, 0, now)[0], "BLOCK")       # trades/day
        self.assertEqual(strategy.risk_check(o, CFG, 0, -2500, now)[0], "BLOCK")   # daily loss
        self.assertEqual(strategy.risk_check(o, CFG, 0, 0, now.replace(hour=15))[0], "BLOCK")
        sat = datetime.datetime(2026, 11, 14, 10, 0)
        self.assertEqual(strategy.risk_check(o, CFG, 0, 0, sat)[0], "BLOCK")

    def test_stop_first_when_bar_hits_both(self):
        bar = {"t": datetime.datetime(2026, 11, 11, 9, 45), "o": 102, "h": 106, "l": 100, "c": 103, "v": 1}
        self.assertEqual(strategy.simulate_exit([bar], self.plan, CFG)[0], "stop")


class TestTimeMachine(unittest.TestCase):
    def test_runs_and_is_repeatable(self):
        bars = data.parse_dhan_bars(make_days())
        days = core.sessions(bars)
        past = [days[d] for d in sorted(days)][:-1]
        plan = strategy.exit_plan(1000, {"high": 999, "low": 990, "mid": 994.5}, CFG)
        et = datetime.datetime(2026, 11, 11, 9, 45)
        a = timemachine.monte_carlo(plan, et, past, CFG, 2000, seed=7)
        b = timemachine.monte_carlo(plan, et, past, CFG, 2000, seed=7)
        self.assertEqual(a, b)
        self.assertAlmostEqual(a["target_pct"] + a["stop_pct"] + a["flat_pct"], 100, delta=0.2)
        hist, trades = timemachine.history(past, CFG)
        self.assertEqual(hist["fired"], len(trades))
        self.assertEqual(hist["target"] + hist["stop"] + hist["flat"], hist["fired"])


class TestDhanRequest(unittest.TestCase):
    def test_super_order_body(self):
        seen = {}

        def fake(method, url, headers=None, body=None, timeout=30):
            seen.update(method=method, url=url, headers=headers, body=body)
            return '{"orderId": "1", "orderStatus": "PENDING"}'
        with mock.patch.dict(os.environ, {"DHAN_CLIENT_ID": "1000", "DHAN_ACCESS_TOKEN": "tok"}), \
                mock.patch.object(data, "_http", fake):
            data.Dhan().place_super_order("2885", 5, 1400.5, 1420.0, 1390.0, tag="idt-RELIANCE")
        b = seen["body"]
        self.assertEqual(seen["url"], "https://api.dhan.co/v2/super/orders")
        self.assertEqual(seen["headers"]["access-token"], "tok")
        self.assertEqual((b["transactionType"], b["exchangeSegment"], b["productType"], b["orderType"]),
                         ("BUY", "NSE_EQ", "INTRADAY", "LIMIT"))
        self.assertEqual((b["securityId"], b["quantity"], b["price"], b["targetPrice"], b["stopLossPrice"]),
                         ("2885", 5, 1400.5, 1420.0, 1390.0))
        self.assertEqual(b["dhanClientId"], "1000")


class TestCLI(unittest.TestCase):
    """End to end on a saved file: scan -> plan -> backtest -> paper place -> status."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fix = os.path.join(self.tmp, "DEMO.json")
        cfg = dict(CFG)
        for seed in range(1, 200):                 # pick a seed whose last day breaks out
            j = make_days(seed=seed)
            day = core.sessions(data.parse_dhan_bars(j))[datetime.date(2026, 11, 11)]
            if strategy.check(day, datetime.datetime(2026, 11, 11, 14, 35), cfg)["status"] == "triggered":
                break
        with open(self.fix, "w") as f:
            json.dump(j, f)
        self.p = mock.patch.object(core, "STATE", os.path.join(self.tmp, "trader"))
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def run_cli(self, *args, typed=None):
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch("builtins.input", return_value=typed or ""):
            code = trader.main(list(args))
        return code, buf.getvalue()

    def test_flow(self):
        code, out = self.run_cli("check", "DEMO", "--now", "2026-11-11 14:35", "--offline", self.fix)
        self.assertEqual(code, 0, out)
        sig = core.read_state("signal-DEMO.json")
        when = sig["time"]
        code, out = self.run_cli("plan", "DEMO", "--now", when, "--offline", self.fix)
        self.assertIn("RISK:", out)
        code, out = self.run_cli("backtest", "DEMO", "--now", when, "--offline", self.fix, "--runs", "500", "--seed", "1")
        self.assertIn("Monte Carlo 500 runs", out)
        order = core.read_state("order-DEMO.json")
        self.assertEqual(order["risk"], "PASS", out)
        if True:
            code, out = self.run_cli("place", "DEMO", "--now", when, "--offline", self.fix, typed="wrong")
            self.assertIn("Nothing placed", out)
            code, out = self.run_cli("place", "DEMO", "--now", when, "--offline", self.fix, "--live",
                                     typed="YES BUY %d DEMO" % order["qty"])
            self.assertIn("Paper trade recorded", out)       # --live ignored in paper mode
            code, out = self.run_cli("place", "DEMO", "--now", when, "--offline", self.fix,
                                     "--confirm", "YES BUY 1 DEMO")
            self.assertIn("Nothing placed", out)             # wrong qty in --confirm is refused
            code, out = self.run_cli("place", "DEMO", "--now", when, "--offline", self.fix,
                                     "--confirm", "YES BUY %d DEMO" % order["qty"])
            self.assertIn("Paper trade recorded", out)
            code, out = self.run_cli("status", "--now", "2026-11-11 15:29", "--offline", self.fix)
            self.assertIn("closed P&L today", out)


if __name__ == "__main__":
    unittest.main()
