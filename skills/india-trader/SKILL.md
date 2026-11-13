---
name: india-trader
description: NSE intraday opening-range-breakout workflow on the Dhan API - scan the watchlist, plan stop/target/quantity, run the Time Machine backtest, and place paper or (with typed confirmation) live Dhan Super Orders. Use when the user asks to scan Indian stocks, plan or test an intraday trade, check paper/live P&L, or place a Dhan order.
---

# India Day Trader

Tools live in the `india-day-trader` folder. Run them with `python3 trader.py <command>`
from that folder. All times are IST. Market 9:15-15:30.

## Order of work for one trade

1. `scan` - every symbol in `watchlist.txt`: waiting / triggered / expired.
2. `check SYMBOL` or `watch SYMBOL` - one stock, bar by bar.
3. `plan SYMBOL` - stop (opening-range midpoint by default), target (1.5R), quantity
   sized so a stop-out loses at most `max_loss_per_trade`, then a PASS/BLOCK risk check.
4. `backtest SYMBOL` - Time Machine: 10,000 Monte Carlo replays of the rest of the
   session from the stock's own past bars, plus the same rule on ~60 past sessions.
   Report the numbers plainly. If it says coin flip or against, say so.
5. `ticket` - show the order in plain words.
6. `place SYMBOL` - the USER types the confirmation line themselves in the terminal.

## Hard rules for Claude

- Never type the confirmation line for the user, never pass `--confirm` yourself, never pipe `yes`/`echo` into `place`,
  never edit config.json to switch `"mode"` to `"live"` unless the user explicitly asks
  in this conversation.
- Never print, read aloud or copy the contents of `.env`.
- Never call it a sure trade or promise profit. Give odds, the stop, the max loss.
- If the risk check says BLOCK, do not work around it (no changing limits to make
  one trade pass) unless the user asks to change their limits on purpose.
- Remind the user once per session: about 9 in 10 individual F&O traders lost money
  in SEBI's study; intraday cash is also risky. Paper trade first.

## Settings

`config.json`: capital, max_loss_per_trade, daily_loss_limit, max_trades_per_day,
entry window, flat_by (15:10, before Dhan's auto square-off), stop rule, target_r,
data_source (`dhan` needs the Data API plan; `yahoo` is free and delayed, bars only).

Errors: DH-905 / IP -> Static IP not whitelisted. 401 / DH-901 -> token expired,
make a new one in web.dhan.co and update `.env`. DH-902 -> Data API plan not active.
