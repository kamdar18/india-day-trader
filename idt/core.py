"""Shared helpers: IST clock, NSE session, config, files, bars."""

import datetime
import json
import os

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "trader")          # everything the tools write lands here

OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)

DEFAULTS = {
    "mode": "paper",                 # paper | live
    "capital": 100000,               # rupees in the trading account
    "max_loss_per_trade": 1000,      # rupees you accept losing on one trade
    "daily_loss_limit": 3000,        # stop trading for the day after this
    "max_trades_per_day": 3,
    "max_position_pct": 50,          # one position may use at most this % of capital
    "intraday_leverage": 1,          # 1 = no margin. Dhan intraday margin up to 5x; keep 1 to be safe
    "or_minutes": 15,                # opening range = 9:15 to 9:30
    "interval": 5,                   # minutes per bar
    "no_entries_before": "09:30",
    "no_entries_after": "14:30",
    "flat_by": "15:10",              # exit everything by this time (Dhan auto square-off is ~3:15-3:20)
    "min_over_pct": 0.0,             # trigger close must be at least this % above range high
    "min_volume_x": 0.0,             # trigger bar volume >= X times avg opening-range bar volume
    "stop": "or-mid",                # or-mid | or-low | pct:0.8
    "target_r": 1.5,                 # target = entry + R x risk
    "min_reward_risk": 1.0,
    "cost_pct_round_trip": 0.06,     # rough brokerage+STT+charges, % of turnover (edit for your plan)
    "data_source": "dhan",           # dhan | yahoo
    "product_type": "INTRADAY",
}


def now_ist():
    return datetime.datetime.now(IST).replace(tzinfo=None)


def parse_hhmm(s):
    h, m = s.split(":")
    return datetime.time(int(h), int(m))


def load_env():
    """Read KEY=VALUE lines from .env into os.environ (never printed)."""
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config():
    cfg = dict(DEFAULTS)
    path = os.path.join(ROOT, "config.json")
    if os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f))
    return cfg


def holidays():
    out = set()
    path = os.path.join(ROOT, "holidays.txt")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.split("#")[0].strip()
                if line:
                    out.add(line)
    return out


def is_trading_day(d):
    return d.weekday() < 5 and d.isoformat() not in holidays()


def watchlist():
    path = os.path.join(ROOT, "watchlist.txt")
    syms = []
    with open(path) as f:
        for line in f:
            line = line.split("#")[0].strip().upper()
            if line:
                syms.append(line)
    return syms


def state_path(name):
    os.makedirs(STATE, exist_ok=True)
    return os.path.join(STATE, name)


def read_state(name, default=None):
    p = state_path(name)
    if not os.path.exists(p):
        return default
    with open(p) as f:
        return json.load(f)


def write_state(name, data):
    p = state_path(name)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, p)
    return p


def round_tick(x, tick=0.05, mode="nearest"):
    import math
    n = x / tick
    n = math.floor(n + 1e-9) if mode == "down" else math.ceil(n - 1e-9) if mode == "up" else round(n)
    return round(n * tick, 2)


def sessions(bars):
    """Group bars (dicts with t=datetime IST) by date, keep regular-session bars only."""
    days = {}
    for b in bars:
        if OPEN <= b["t"].time() < CLOSE:
            days.setdefault(b["t"].date(), []).append(b)
    for d in days:
        days[d].sort(key=lambda b: b["t"])
    return days


def closed_bars(day_bars, now, interval):
    """Bars whose interval has fully elapsed at `now`."""
    step = datetime.timedelta(minutes=interval)
    return [b for b in day_bars if b["t"] + step <= now]
