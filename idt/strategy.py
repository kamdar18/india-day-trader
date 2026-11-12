"""Opening-range breakout (long only), exit plan, position size and risk checks.

Rule: the opening range is the high/low of the first `or_minutes` (9:15-9:30 by
default). The entry triggers on the first COMPLETED 5-min bar after the range
that CLOSES above the range high, between no_entries_before and
no_entries_after. A wick above the high is not a trigger.
"""

import datetime
import math

from . import core


def opening_range(day_bars, cfg):
    n = max(1, cfg["or_minutes"] // cfg["interval"])
    first = day_bars[:n]
    if len(first) < n or first[0]["t"].time() != core.OPEN:
        return None
    return {"high": max(b["h"] for b in first), "low": min(b["l"] for b in first),
            "end": first[-1]["t"] + datetime.timedelta(minutes=cfg["interval"]),
            "vol": sum(b["v"] for b in first) / n}


def check(day_bars, now, cfg):
    """status: waiting | triggered | expired | no-data, plus details."""
    step = datetime.timedelta(minutes=cfg["interval"])
    done = core.closed_bars(day_bars, now, cfg["interval"])
    if not day_bars:
        return {"status": "no-data", "reason": "no bars for this day (holiday, pre-open or data delay)"}
    rng = opening_range(done, cfg)
    if rng is None:
        return {"status": "waiting", "reason": "opening range still forming (till %s)" %
                (datetime.datetime.combine(now.date(), core.OPEN) + datetime.timedelta(minutes=cfg["or_minutes"])).strftime("%H:%M")}
    start = core.parse_hhmm(cfg["no_entries_before"])
    stop_t = core.parse_hhmm(cfg["no_entries_after"])
    for b in done:
        close_t = b["t"] + step
        if b["t"] < rng["end"] or close_t.time() < start:
            continue
        if close_t.time() > stop_t:
            break
        if b["c"] > rng["high"]:
            return {"status": "triggered", "time": close_t.strftime("%Y-%m-%d %H:%M"),
                    "price": round(b["c"], 2), "range": rng_out(rng),
                    "over_pct": round((b["c"] / rng["high"] - 1) * 100, 2),
                    "vol_x": round(b["v"] / rng["vol"], 2) if rng["vol"] else None}
    if now.time() > stop_t:
        return {"status": "expired", "reason": "no close above %.2f by %s" % (rng["high"], cfg["no_entries_after"]),
                "range": rng_out(rng)}
    last = done[-1]["c"] if done else None
    return {"status": "waiting", "reason": "no 5-min close above %.2f yet" % rng["high"],
            "last": last, "range": rng_out(rng)}


def rng_out(r):
    return {"high": round(r["high"], 2), "low": round(r["low"], 2), "mid": round((r["high"] + r["low"]) / 2, 2)}


def exit_plan(entry, rng, cfg, tick=0.05):
    s = cfg["stop"]
    if s == "or-mid":
        stop = rng["mid"]
    elif s == "or-low":
        stop = rng["low"]
    elif s.startswith("pct:"):
        stop = entry * (1 - float(s[4:]) / 100)
    else:
        raise ValueError("unknown stop rule %r" % s)
    stop = core.round_tick(stop, tick, "down")
    if stop >= entry:
        raise ValueError("stop %.2f is not below entry %.2f" % (stop, entry))
    risk = round(entry - stop, 2)
    target = core.round_tick(entry + cfg["target_r"] * risk, tick, "up")
    return {"entry": round(entry, 2), "stop": stop, "target": target, "risk_per_share": risk,
            "reward_risk": round((target - entry) / risk, 2), "flat_by": cfg["flat_by"]}


def size(plan, cfg, cash=None):
    """Shares so a stop-out loses at most max_loss_per_trade, capped by capital."""
    risk = plan["risk_per_share"]
    by_risk = math.floor(cfg["max_loss_per_trade"] / risk)
    buying_power = (cash if cash is not None else cfg["capital"]) * cfg.get("intraday_leverage", 1)
    by_cap = math.floor(buying_power * cfg["max_position_pct"] / 100 / plan["entry"])
    qty = max(0, min(by_risk, by_cap))
    value = round(qty * plan["entry"], 2)
    cost = round(value * cfg["cost_pct_round_trip"] / 100, 2)   # buy + sell charges, rough
    return {"qty": qty, "value": value, "loss_at_stop": round(qty * risk + cost, 2),
            "gain_at_target": round(qty * (plan["target"] - plan["entry"]) - cost, 2),
            "est_charges": cost, "limited_by": "risk" if by_risk <= by_cap else "capital"}


def risk_check(order, cfg, today_trades, today_pnl, now):
    """Returns (PASS|BLOCK, [reasons])."""
    why = []
    if order["qty"] <= 0:
        why.append("quantity is 0 (risk per share too big for max_loss_per_trade or capital)")
    if order["loss_at_stop"] > cfg["max_loss_per_trade"] * 1.05:
        why.append("loss at stop Rs %.0f > max_loss_per_trade Rs %.0f" % (order["loss_at_stop"], cfg["max_loss_per_trade"]))
    if -today_pnl + order["loss_at_stop"] > cfg["daily_loss_limit"]:
        why.append("would break daily_loss_limit Rs %.0f (today's P&L Rs %.0f)" % (cfg["daily_loss_limit"], today_pnl))
    if today_trades >= cfg["max_trades_per_day"]:
        why.append("max_trades_per_day %d reached" % cfg["max_trades_per_day"])
    if order["reward_risk"] < cfg["min_reward_risk"]:
        why.append("reward:risk %.2f < %.2f" % (order["reward_risk"], cfg["min_reward_risk"]))
    t = now.time()
    if not (core.parse_hhmm(cfg["no_entries_before"]) <= t <= core.parse_hhmm(cfg["no_entries_after"])):
        why.append("outside entry window %s-%s IST (now %s)" % (cfg["no_entries_before"], cfg["no_entries_after"], t.strftime("%H:%M")))
    if not core.is_trading_day(now.date()):
        why.append("%s is not an NSE trading day" % now.date())
    return ("PASS" if not why else "BLOCK"), why


def simulate_exit(bars_after, plan, cfg):
    """Walk bars after entry: stop first if a bar touches both (conservative)."""
    flat = core.parse_hhmm(cfg["flat_by"])
    step = datetime.timedelta(minutes=cfg["interval"])
    for b in bars_after:
        if b["l"] <= plan["stop"]:
            return "stop", plan["stop"], b["t"]
        if b["h"] >= plan["target"]:
            return "target", plan["target"], b["t"]
        if (b["t"] + step).time() >= flat:
            return "flat", b["c"], b["t"] + step
    if bars_after:
        return "flat", bars_after[-1]["c"], bars_after[-1]["t"] + step
    return "open", None, None
