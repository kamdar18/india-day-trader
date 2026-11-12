#!/usr/bin/env python3
"""India Day Trader: NSE opening-range breakout toolkit on Dhan API.

  python3 trader.py doctor                      # check .env, Dhan connection, funds
  python3 trader.py instruments --refresh       # download Dhan's NSE symbol list
  python3 trader.py scan                        # every watchlist stock: waiting / triggered / expired
  python3 trader.py check RELIANCE              # one stock
  python3 trader.py watch RELIANCE              # bar by bar until it triggers
  python3 trader.py plan RELIANCE               # stop, target, quantity, PASS/BLOCK
  python3 trader.py backtest RELIANCE           # Time Machine: 10,000 replays + past sessions
  python3 trader.py ticket                      # the order in plain words
  python3 trader.py place RELIANCE              # paper by default; live only with --live + typed YES
  python3 trader.py status                      # paper trades / live positions, today's P&L

Nothing is sent to the exchange unless config.json has "mode": "live", you add
--live, AND you type the exact confirmation line. Paper trading is the default.
Common flags: --now "2026-11-11 10:05" (replay a time), --offline PATH (saved bars).
"""

import argparse
import datetime
import json
import sys
import time

from idt import core, data, strategy, timemachine


def get_now(a):
    return datetime.datetime.strptime(a.now, "%Y-%m-%d %H:%M") if a.now else core.now_ist()


def day_bars(sym, now, cfg, offline):
    days = core.sessions(data.bars(sym, 5, cfg, offline, end=now.date()))
    return days.get(now.date(), [])


def fmt_sig(sym, s):
    if s["status"] == "triggered":
        r = s["range"]
        return "%-12s TRIGGERED at %s  close %.2f  (range %.2f-%.2f, +%.2f%% over, vol x%s)" % (
            sym, s["time"][11:], s["price"], r["low"], r["high"], s["over_pct"], s["vol_x"])
    return "%-12s %-9s %s" % (sym, s["status"], s.get("reason", ""))


# ---------------- commands ----------------

def cmd_doctor(a, cfg):
    core.load_env()
    print("mode:", cfg["mode"], "| data:", cfg["data_source"], "| capital Rs", cfg["capital"])
    try:
        d = data.Dhan()
        print("credentials: found (client id ending ...%s)" % d.client_id[-3:])
        f = d.funds()
        f = f.get("data", f)
        print("Dhan funds OK: available Rs", f.get("availabelBalance", f.get("availableBalance")))
    except data.DataError as e:
        print("Dhan:", e)
    try:
        n = len(data.instruments())
        print("instruments: %d NSE equities cached" % n)
    except data.DataError as e:
        print("instruments:", e)
    try:
        import urllib.request
        ip = urllib.request.urlopen("https://api.ipify.org", timeout=10).read().decode()
        print("this server's public IP: %s  -> whitelist it in Dhan (Profile > DhanHQ APIs > Static IP)" % ip)
    except Exception:
        print("could not read public IP (check DigitalOcean dashboard)")


def cmd_instruments(a, cfg):
    inst = data.instruments(refresh=a.refresh)
    print("%d NSE equities. Example: RELIANCE ->" % len(inst), inst.get("RELIANCE"))


def cmd_check(a, cfg):
    now = get_now(a)
    s = strategy.check(day_bars(a.symbol, now, cfg, a.offline), now, cfg)
    print(fmt_sig(a.symbol.upper(), s))
    if s["status"] == "triggered":
        core.write_state("signal-%s.json" % a.symbol.upper(), dict(s, symbol=a.symbol.upper()))
    return 0 if s["status"] == "triggered" else 1


def cmd_scan(a, cfg):
    now = get_now(a)
    hits, rows = [], []
    for sym in core.watchlist():
        try:
            s = strategy.check(day_bars(sym, now, cfg, a.offline), now, cfg)
        except Exception as e:
            s = {"status": "error", "reason": str(e)[:120]}
        rows.append(dict(s, symbol=sym))
        print(fmt_sig(sym, s))
        if s["status"] == "triggered":
            hits.append(sym)
            core.write_state("signal-%s.json" % sym, dict(s, symbol=sym))
        time.sleep(0 if a.offline else 0.3)
    core.write_state("scan.json", {"at": now.isoformat(), "rows": rows})
    print("\n%d of %d triggered%s" % (len(hits), len(rows), (": " + ", ".join(hits)) if hits else ""))


def cmd_watch(a, cfg):
    sym = a.symbol.upper()
    while True:
        now = core.now_ist()
        s = strategy.check(day_bars(sym, now, cfg, None), now, cfg)
        print(now.strftime("%H:%M:%S"), fmt_sig(sym, s), flush=True)
        if s["status"] == "triggered":
            core.write_state("signal-%s.json" % sym, dict(s, symbol=sym))
            return 0
        if s["status"] == "expired" or (s["status"] == "no-data" and now.time() > datetime.time(9, 45)):
            return 1
        time.sleep(a.every)


def today_journal(now):
    j = core.read_state("journal.json", [])
    today = [t for t in j if t.get("date") == now.date().isoformat()]
    pnl = sum(t.get("pnl", 0) or 0 for t in today if t.get("closed"))
    return today, pnl


def cmd_plan(a, cfg):
    sym = a.symbol.upper()
    now = get_now(a)
    if a.entry:
        sig = {"price": a.entry, "time": now.strftime("%Y-%m-%d %H:%M"),
               "range": {"low": a.range_low, "high": a.range_high, "mid": round((a.range_low + a.range_high) / 2, 2)}}
    else:
        sig = core.read_state("signal-%s.json" % sym)
        if not sig or not sig["time"].startswith(now.date().isoformat()):
            s = strategy.check(day_bars(sym, now, cfg, a.offline), now, cfg)
            if s["status"] != "triggered":
                print(fmt_sig(sym, s))
                print("No trigger today, so no plan. (Use --entry/--range-low/--range-high to plan by hand.)")
                return 1
            sig = s
    plan = strategy.exit_plan(sig["price"], sig["range"], cfg, data.tick_of(sym, a.offline))
    cash = None
    if cfg["mode"] == "live" and not a.offline:
        try:
            f = data.Dhan().funds()
            f = f.get("data", f)
            cash = float(f.get("availabelBalance", f.get("availableBalance", cfg["capital"])))
        except data.DataError as e:
            print("(could not read Dhan funds, using capital from config:", e, ")")
    sz = strategy.size(plan, cfg, cash)
    order = dict(plan, **sz)
    order.update(symbol=sym, signal_time=sig["time"], planned_at=now.isoformat(timespec="minutes"))
    trades, pnl = today_journal(now)
    verdict, why = strategy.risk_check(order, cfg, len(trades), pnl, now)
    order.update(risk=verdict, risk_reasons=why)
    core.write_state("order-%s.json" % sym, order)
    print_ticket(order)
    return 0 if verdict == "PASS" else 1


def print_ticket(o):
    print("\n  %s  BUY %d @ %.2f  (LIMIT, intraday)" % (o["symbol"], o["qty"], o["entry"]))
    print("  stop-loss %.2f  (Rs %.2f/share)   target %.2f   reward:risk %.2f" %
          (o["stop"], o["risk_per_share"], o["target"], o["reward_risk"]))
    print("  value Rs %.0f | loss at stop ~Rs %.0f | gain at target ~Rs %.0f (after ~Rs %.0f charges)" %
          (o["value"], o["loss_at_stop"], o["gain_at_target"], o["est_charges"]))
    print("  exit everything by %s IST" % o["flat_by"])
    print("  RISK: %s%s\n" % (o["risk"], ("  - " + "; ".join(o["risk_reasons"])) if o["risk_reasons"] else ""))


def cmd_backtest(a, cfg):
    sym = a.symbol.upper()
    now = get_now(a)
    bars = data.bars(sym, a.days, cfg, a.offline, end=now.date())
    days = core.sessions(bars)
    past = [days[d] for d in sorted(days) if d < now.date() and len(days[d]) >= 60]
    if len(past) < 10:
        print("only %d full past sessions: not enough for a fair test" % len(past))
        return 1
    order = core.read_state("order-%s.json" % sym)
    print("%s: %d past sessions, %s to %s" % (sym, len(past), past[0][0]["t"].date(), past[-1][0]["t"].date()))
    hist, trades = timemachine.history(past, cfg)
    if order:
        et = datetime.datetime.strptime(order["signal_time"], "%Y-%m-%d %H:%M")
        mc = timemachine.monte_carlo(order, et, past, cfg, a.runs, a.seed)
        print("\nToday's plan: entry %.2f stop %.2f target %.2f" % (order["entry"], order["stop"], order["target"]))
        if mc:
            print("Monte Carlo %d runs: target first %.1f%% | stop first %.1f%% | flat by %s %.1f%% | expected %.2fR" %
                  (mc["runs"], mc["target_pct"], mc["stop_pct"], cfg["flat_by"], mc["flat_pct"], mc["expected_R"]))
    else:
        mc = None
        print("\n(no plan for today yet, so only the history test; run plan first for the Monte Carlo)")
    print("History: rule fired %d of %d days -> target %d, stop %d, flat %d | total %.2fR, avg %sR" %
          (hist["fired"], hist["sessions"], hist["target"], hist["stop"], hist["flat"], hist["total_R"], hist["avg_R"]))
    if a.verbose:
        for t in trades:
            print("   ", t)
    print("Verdict:", timemachine.verdict(mc, hist) if mc else ("history only: avg %sR per trade" % hist["avg_R"]))
    print("(charges not included in R; odds, not a promise)")
    core.write_state("backtest-%s.json" % sym, {"montecarlo": mc, "history": hist, "trades": trades})


def cmd_ticket(a, cfg):
    import glob
    files = sorted(glob.glob(core.state_path("order-*.json")))
    if not files:
        print("no orders planned")
    for f in files:
        print_ticket(json.load(open(f)))


def cmd_place(a, cfg):
    sym = a.symbol.upper()
    now = get_now(a)
    o = core.read_state("order-%s.json" % sym)
    if not o:
        print("no plan for %s: run plan first" % sym)
        return 1
    if not o["planned_at"].startswith(now.date().isoformat()):
        print("that plan is from %s, not today: plan again" % o["planned_at"][:10])
        return 1
    print_ticket(o)
    if o["risk"] != "PASS":
        print("BLOCKED by risk check. Not placing.")
        return 1
    live = a.live and cfg["mode"] == "live"
    if a.live and cfg["mode"] != "live":
        print('--live ignored: config.json "mode" is "%s". Paper trade instead.' % cfg["mode"])
    expect = "YES BUY %d %s" % (o["qty"], sym)
    print("%s order. To confirm, type exactly:  %s" % ("LIVE (real money)" if live else "PAPER", expect))
    typed = input("> ").strip()
    if typed != expect:
        print("Not confirmed. Nothing placed.")
        return 1
    rec = {"date": now.date().isoformat(), "symbol": sym, "qty": o["qty"], "entry": o["entry"],
           "stop": o["stop"], "target": o["target"], "mode": "live" if live else "paper",
           "placed_at": now.isoformat(timespec="seconds"), "closed": False}
    if live:
        try:
            resp = data.Dhan().place_super_order(data.lookup(sym)["id"], o["qty"], o["entry"], o["target"],
                                                 o["stop"], cfg["product_type"], "idt-%s" % sym)
        except data.DataError as e:
            print("Dhan rejected:", e)
            return 1
        rec["dhan"] = resp
        print("Sent to Dhan as Super Order:", resp)
    else:
        print("Paper trade recorded (no real order).")
    j = core.read_state("journal.json", [])
    j.append(rec)
    core.write_state("journal.json", j)
    return 0


def cmd_status(a, cfg):
    now = get_now(a)
    j = core.read_state("journal.json", [])
    changed = False
    for t in j:
        if t["mode"] != "paper" or t["closed"] or t["date"] != now.date().isoformat():
            continue
        bars = day_bars(t["symbol"], now, cfg, a.offline)
        placed = datetime.datetime.fromisoformat(t["placed_at"])
        after = [b for b in core.closed_bars(bars, now, cfg["interval"]) if b["t"] >= placed - datetime.timedelta(minutes=cfg["interval"])]
        # paper fill: limit buy fills once price trades at or below entry
        filled = [b for b in after if b["l"] <= t["entry"]]
        if not filled:
            print("%s paper: entry %.2f not filled yet" % (t["symbol"], t["entry"]))
            continue
        rest = after[after.index(filled[0]) + 1:]
        how, px, when = strategy.simulate_exit(rest, t, cfg)
        if how == "open" or (how == "flat" and now.time() < core.parse_hhmm(cfg["flat_by"])):
            last = after[-1]["c"]
            print("%s paper: open, last %.2f, P&L Rs %.0f" % (t["symbol"], last, (last - t["entry"]) * t["qty"]))
        else:
            t.update(closed=True, exit=how, exit_price=px, pnl=round((px - t["entry"]) * t["qty"], 2))
            changed = True
    if changed:
        core.write_state("journal.json", j)
    if cfg["mode"] == "live" and not a.offline:
        try:
            print("Dhan positions:", json.dumps(data.Dhan().positions(), indent=1)[:1500])
        except data.DataError as e:
            print("Dhan:", e)
    today = [t for t in j if t["date"] == now.date().isoformat()]
    for t in today:
        print("  %s %s %d @ %.2f -> %s" % (t["mode"], t["symbol"], t["qty"], t["entry"],
              ("%s %.2f  P&L Rs %.0f" % (t["exit"], t["exit_price"], t["pnl"])) if t["closed"] else "open"))
    print("closed P&L today: Rs %.0f" % sum(t.get("pnl", 0) for t in today if t["closed"]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, symbol=False):
        sp = sub.add_parser(name)
        if symbol:
            sp.add_argument("symbol")
        sp.add_argument("--now", help='replay time, "YYYY-MM-DD HH:MM" IST')
        sp.add_argument("--offline", help="saved bars file or folder")
        sp.set_defaults(fn=fn)
        return sp

    add("doctor", cmd_doctor)
    add("instruments", cmd_instruments).add_argument("--refresh", action="store_true")
    add("scan", cmd_scan)
    add("check", cmd_check, True)
    add("watch", cmd_watch, True).add_argument("--every", type=int, default=30)
    sp = add("plan", cmd_plan, True)
    sp.add_argument("--entry", type=float)
    sp.add_argument("--range-low", type=float)
    sp.add_argument("--range-high", type=float)
    sp = add("backtest", cmd_backtest, True)
    sp.add_argument("--days", type=int, default=90)
    sp.add_argument("--runs", type=int, default=10000)
    sp.add_argument("--seed", type=int)
    sp.add_argument("-v", "--verbose", action="store_true")
    add("ticket", cmd_ticket)
    add("place", cmd_place, True).add_argument("--live", action="store_true")
    add("status", cmd_status)
    a = p.parse_args(argv)
    cfg = core.load_config()
    try:
        return a.fn(a, cfg) or 0
    except data.DataError as e:
        print("error:", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
