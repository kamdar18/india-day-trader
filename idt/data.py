"""Market data and broker access.

Dhan API v2 (https://dhanhq.co/docs/v2/) is the main source:
  POST /v2/charts/intraday   5-min bars (max 90 days per call)   needs Data API plan
  POST /v2/marketfeed/ltp    last traded price                    needs Data API plan
  GET  /v2/fundlimit         available balance                    free
  POST /v2/super/orders      entry + target + stop in one order   free, needs Static IP
Yahoo Finance (SYMBOL.NS) is a free, delayed fallback for bars only.

The access token is read from the environment (.env) and never printed.
"""

import csv
import datetime
import io
import json
import os
import time
import urllib.error
import urllib.request

from . import core

DHAN = "https://api.dhan.co/v2"
SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"
YAHOO = "https://query2.finance.yahoo.com/v8/finance/chart/%s.NS?range=%s&interval=%dm"
UA = "Mozilla/5.0 (india-day-trader)"


class DataError(Exception):
    pass


def _http(method, url, headers=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(text)
            msg = "%s %s" % (j.get("errorCode", ""), j.get("errorMessage", text))
        except ValueError:
            msg = text[:300]
        hint = ""
        if "DH-905" in msg or "IP" in msg.upper():
            hint = " (check the Static IP in web.dhan.co > Profile > DhanHQ APIs)"
        elif e.code in (401, 403) or "DH-901" in msg:
            hint = " (access token expired or wrong: make a new one and put it in .env)"
        elif "DH-902" in msg:
            hint = " (Data API plan is not active: use data_source yahoo, or subscribe)"
        raise DataError("Dhan HTTP %d: %s%s" % (e.code, msg.strip(), hint))
    except urllib.error.URLError as e:
        raise DataError("network error: %s" % e.reason)


class Dhan:
    def __init__(self):
        core.load_env()
        self.client_id = os.environ.get("DHAN_CLIENT_ID", "")
        self.token = os.environ.get("DHAN_ACCESS_TOKEN", "")
        if not self.client_id or not self.token:
            raise DataError("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN missing: copy .env.example to .env and fill it")

    def _headers(self):
        return {"access-token": self.token, "client-id": self.client_id,
                "Content-Type": "application/json", "Accept": "application/json"}

    def get(self, path):
        return json.loads(_http("GET", DHAN + path, self._headers()))

    def post(self, path, body):
        body = dict(body)
        body.setdefault("dhanClientId", self.client_id)
        return json.loads(_http("POST", DHAN + path, self._headers(), body))

    # ---- data ----
    def intraday(self, security_id, start, end, interval=5):
        """5-min bars from start to end (dates). Splits into 85-day chunks."""
        out = []
        a = start
        while a <= end:
            b = min(end, a + datetime.timedelta(days=85))
            j = self.post("/charts/intraday", {
                "securityId": str(security_id), "exchangeSegment": "NSE_EQ",
                "instrument": "EQUITY", "interval": str(interval), "oi": False,
                "fromDate": a.isoformat(), "toDate": (b + datetime.timedelta(days=1)).isoformat()})
            out.extend(parse_dhan_bars(j))
            a = b + datetime.timedelta(days=1)
            time.sleep(0.25)          # stay under the data rate limit
        seen, uniq = set(), []
        for bar in sorted(out, key=lambda x: x["t"]):
            if bar["t"] not in seen:
                seen.add(bar["t"])
                uniq.append(bar)
        return uniq

    def ltp(self, security_ids):
        j = self.post("/marketfeed/ltp", {"NSE_EQ": [int(s) for s in security_ids]})
        data = j.get("data", j).get("NSE_EQ", {})
        return {str(k): float(v.get("last_price", 0)) for k, v in data.items()}

    # ---- account / orders ----
    def funds(self):
        return self.get("/fundlimit")

    def positions(self):
        return self.get("/positions")

    def super_orders(self):
        return self.get("/super/orders")

    def place_super_order(self, security_id, qty, entry, target, stop, product="INTRADAY", tag=None):
        body = {"transactionType": "BUY", "exchangeSegment": "NSE_EQ", "productType": product,
                "orderType": "LIMIT", "securityId": str(security_id), "quantity": int(qty),
                "price": float(entry), "targetPrice": float(target), "stopLossPrice": float(stop),
                "trailingJump": 0.0}
        if tag:
            body["correlationId"] = tag[:25]
        return self.post("/super/orders", body)


def parse_dhan_bars(j):
    """Dhan returns {"open":[..],"high":[..],...,"timestamp":[..]} (sometimes wrapped in "data")."""
    if isinstance(j, dict) and "data" in j and isinstance(j["data"], (dict, list)):
        j = j["data"]
    bars = []
    if isinstance(j, dict) and isinstance(j.get("timestamp"), list):
        for i, ts in enumerate(j["timestamp"]):
            bars.append(_bar(ts, j["open"][i], j["high"][i], j["low"][i], j["close"][i],
                             (j.get("volume") or [0] * len(j["timestamp"]))[i]))
    elif isinstance(j, list):
        for r in j:
            bars.append(_bar(r["timestamp"], r["open"], r["high"], r["low"], r["close"], r.get("volume", 0)))
    return bars


def _bar(ts, o, h, l, c, v):
    t = datetime.datetime.fromtimestamp(float(ts), core.IST).replace(tzinfo=None)
    return {"t": t, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v or 0)}


# ---- instruments ----

def instruments(refresh=False):
    """symbol -> {"id":..., "tick":..., "lot":...} for NSE equities (EQ series)."""
    cached = core.read_state("instruments.json")
    if cached and not refresh:
        return cached
    text = _http("GET", SCRIP_MASTER, {"User-Agent": UA}, timeout=120)
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("SEM_EXM_EXCH_ID") != "NSE" or r.get("SEM_SEGMENT") != "E":
            continue
        if r.get("SEM_SERIES", "EQ") not in ("EQ", "BE") or r.get("SEM_INSTRUMENT_NAME") not in ("EQUITY", None):
            continue
        tick = float(r.get("SEM_TICK_SIZE") or 0.05)
        if tick >= 1:            # some files give paise
            tick = tick / 100.0
        out[r["SEM_TRADING_SYMBOL"].upper()] = {"id": r["SEM_SMST_SECURITY_ID"], "tick": tick or 0.05}
    if not out:
        raise DataError("scrip master had no NSE equity rows (format may have changed)")
    core.write_state("instruments.json", out)
    return out


def lookup(symbol):
    inst = instruments()
    s = symbol.upper()
    if s not in inst:
        raise DataError("%s not found in Dhan's NSE list (run: python3 trader.py instruments --refresh)" % s)
    return inst[s]


# ---- unified bar access ----

def yahoo_bars(symbol, days, interval=5):
    rng = "5d" if days <= 5 else "1mo" if days <= 30 else "60d"
    text = _http("GET", YAHOO % (symbol.upper(), rng, interval), {"User-Agent": UA})
    j = json.loads(text)["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(j.get("timestamp") or []):
        if q["close"][i] is None:
            continue
        bars.append(_bar(ts, q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]))
    return bars


def load_offline(path, symbol):
    p = os.path.join(path, symbol.upper() + ".json") if os.path.isdir(path) else path
    with open(p) as f:
        return parse_dhan_bars(json.load(f))


def bars(symbol, days=1, cfg=None, offline=None, end=None):
    """5-min bars for the last `days` calendar days (IST naive datetimes)."""
    cfg = cfg or core.load_config()
    if offline:
        return load_offline(offline, symbol)
    end = end or core.now_ist().date()
    start = end - datetime.timedelta(days=days)
    if cfg.get("data_source") == "yahoo":
        return yahoo_bars(symbol, days, cfg["interval"])
    return Dhan().intraday(lookup(symbol)["id"], start, end, cfg["interval"])


def tick_of(symbol, offline=None):
    if offline:
        return 0.05
    try:
        return lookup(symbol)["tick"]
    except DataError:
        return 0.05
