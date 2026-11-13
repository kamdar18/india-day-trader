# India Day Trader (Dhan + NSE)

NSE na stocks par intraday **opening-range breakout** mate toolkit. Data ane orders
Dhan API v2 mathi aave che. Samay IST mujab che, ane market 9:15 thi 3:30 chale che.
`Jakeschincariol/day-trader-skill` (MIT) na vichar parthi Indian market mate navu lakhyu che.

**Default ma paper trading (khota paisa) chale che.** Real order tyare j jay jyare
`config.json` ma `"mode": "live"` hoy, command ma `--live` hoy, ane tame jate
`YES BUY <qty> <SYMBOL>` type karo.

Fakt Python 3.8+ joie. Bijo koi package install karvano nathi.

## Install (DigitalOcean droplet)

```bash
git clone <this repo> india-day-trader     # ke zip upload kari ne unzip
cd india-day-trader
bash setup.sh
nano .env                                  # DHAN_CLIENT_ID ane DHAN_ACCESS_TOKEN
python3 trader.py doctor                   # Dhan check + droplet nu IP
```

`doctor` je IP batave, e IP **web.dhan.co > Profile > DhanHQ APIs > Static IP** ma
umero. Order mukva mate aa jaruri che.

Dhan access token amuk samay pachhi expire thay che. Expire thay to navu token banavi
ne `.env` ma badli nakho.

## Roj no upyog

```bash
python3 trader.py scan                 # 9:30 pachhi: watchlist ma kon breakout thayu
python3 trader.py watch RELIANCE       # ek stock ne bar by bar jovo
python3 trader.py plan RELIANCE        # stop, target, qty, PASS/BLOCK
python3 trader.py backtest RELIANCE    # Time Machine: 10,000 replay + chhella ~60 divas
python3 trader.py place RELIANCE       # paper (ke live + typed YES)
python3 trader.py place RELIANCE --confirm "YES BUY 45 RELIANCE"   # ek j line ma (mobile console mate)
python3 trader.py status               # aaj na trades ane P&L
```

## Settings (`config.json`)

| key | default | arth |
|---|---|---|
| mode | paper | `live` karo tyare j real order |
| data_source | dhan | `dhan` mate Data API plan joie; `yahoo` free che pan delayed |
| capital | 100000 | account ma ketla rupiya |
| max_loss_per_trade | 1000 | ek trade ma vadhu ma vadhu nuksan |
| daily_loss_limit | 3000 | aatlu nuksan thay pachhi aaje trade band |
| max_trades_per_day | 3 | |
| no_entries_before / after | 09:30 / 14:30 | entry ni window |
| flat_by | 15:10 | aa samay sudhi ma badhu bandh (Dhan auto square-off pehla) |
| stop | or-mid | `or-mid`, `or-low`, ke `pct:0.8` |
| target_r | 1.5 | target = entry + 1.5 x risk |
| cost_pct_round_trip | 0.06 | brokerage+STT no andaje kharch, % ma |

`watchlist.txt` ma stocks ane `holidays.txt` ma NSE ni rajao umero.

## Claude sathe

`skills/india-trader/SKILL.md` ne droplet par `~/.claude/skills/india-trader/` ma copy
karo. Pachhi Claude Code ne kaho, jem ke "aaj nu scan chalavo ane RELIANCE no plan batavo".
Claude kyarey jate YES type nahi kare.

## Tests

```bash
python3 -m unittest discover -s tests -v   # internet vagar, sample data par
```

## Dhyan rakho

- Aa tool tamne odds batave che. Profit ni koi guarantee nathi. SEBI na study mujab
  F&O ma 10 ma thi lagbhag 9 individual traders e nuksan karyu hatu.
- Live karta pehla ochha ma ochha 2-4 athvadiya paper trading karo.
- `.env` (token) kyarey koi ne na aapo ane GitHub par na mukso. `.gitignore` ema
  pehle thi umerelu che.
- Real order mukta pehla Dhan app ma Super Order ane aaj na limits ek vaar jaate check karo.
