"""Time Machine: before risking money, replay the trade.

1. Monte Carlo: the rest of today's session is rebuilt N times. Each 5-min bar
   is drawn from the SAME time slot of the stock's own past sessions (its move
   relative to the previous close), so the open's volatility and the lunch lull
   are kept. Stop and target are checked bar by bar (stop first if a bar
   touches both). Outcome: target first / stop first / flat at flat_by.
2. History: the same ORB rule is replayed on every past session; how often it
   fired and how each one ended.

It gives odds, not a prediction. Past bars do not promise future ones.
"""

import datetime
import random

from . import core, strategy


def slot_moves(history_days, interval):
    """slot time -> list of (o, h, l, c) as ratios to the previous bar's close."""
    slots = {}
    for day in history_days:
        for prev, b in zip(day, day[1:]):
            if prev["c"] <= 0:
                continue
            p = prev["c"]
            slots.setdefault(b["t"].time(), []).append((b["o"] / p, b["h"] / p, b["l"] / p, b["c"] / p))
    return slots


def monte_carlo(plan, entry_time, history_days, cfg, runs=10000, seed=None):
    rng = random.Random(seed)
    slots = slot_moves(history_days, cfg["interval"])
    step = datetime.timedelta(minutes=cfg["interval"])
    flat = core.parse_hhmm(cfg["flat_by"])
    times = []
    t = entry_time
    while t.time() < flat:
        if t.time() in slots:
            times.append(t.time())
        t += step
    if not times:
        return None
    res = {"target": 0, "stop": 0, "flat": 0}
    flat_pnl = 0.0
    for _ in range(runs):
        price = plan["entry"]
        out = None
        for tm in times:
            o, h, l, c = rng.choice(slots[tm])
            lo, hi = price * l, price * h
            if lo <= plan["stop"]:
                out = "stop"
                break
            if hi >= plan["target"]:
                out = "target"
                break
            price = price * c
        if out is None:
            out = "flat"
            flat_pnl += price - plan["entry"]
        res[out] += 1
    risk = plan["risk_per_share"]
    reward = plan["target"] - plan["entry"]
    exp_r = (res["target"] * reward - res["stop"] * risk + flat_pnl) / runs / risk
    return {"runs": runs, "seed": seed, "sessions_used": len(history_days),
            "target_pct": round(100.0 * res["target"] / runs, 1),
            "stop_pct": round(100.0 * res["stop"] / runs, 1),
            "flat_pct": round(100.0 * res["flat"] / runs, 1),
            "expected_R": round(exp_r, 3)}


def history(history_days, cfg):
    trades = []
    for day in history_days:
        d = day[0]["t"].date()
        end = datetime.datetime.combine(d, core.CLOSE)
        sig = strategy.check(day, end, cfg)
        if sig["status"] != "triggered":
            continue
        try:
            plan = strategy.exit_plan(sig["price"], sig["range"], cfg)
        except ValueError:
            continue
        et = datetime.datetime.strptime(sig["time"], "%Y-%m-%d %H:%M")
        after = [b for b in day if b["t"] >= et]
        how, px, _ = strategy.simulate_exit(after, plan, cfg)
        r = (px - plan["entry"]) / plan["risk_per_share"] if px is not None else 0
        cost_r = plan["entry"] * cfg["cost_pct_round_trip"] / 100 / plan["risk_per_share"]
        trades.append({"date": d.isoformat(), "entry": plan["entry"], "exit": how,
                       "exit_price": px, "R": round(r, 2), "net_R": round(r - cost_r, 2)})
    n = len(trades)
    summary = {"sessions": len(history_days), "fired": n,
               "target": sum(t["exit"] == "target" for t in trades),
               "stop": sum(t["exit"] == "stop" for t in trades),
               "flat": sum(t["exit"] == "flat" for t in trades),
               "total_R": round(sum(t["R"] for t in trades), 2),
               "avg_R": round(sum(t["R"] for t in trades) / n, 3) if n else None,
               "net_R": round(sum(t["net_R"] for t in trades), 2)}
    return summary, trades


def verdict(mc, hist):
    if not mc:
        return "not enough history to simulate"
    e = mc["expected_R"]
    h = hist["avg_R"]
    if e > 0.15 and (h is None or h > 0.1):
        tone = "edge looks positive on both tests, still not a guarantee"
    elif e < -0.1 or (h is not None and h < -0.1):
        tone = "odds look against this trade; skipping is a fine answer"
    else:
        tone = "close to a coin flip after the stop and target; size small or skip"
    return tone
