# Trading System v2 — Verification Checklist

## A. STARTUP
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| A1 | Broker loads 2676 scrips | `Broker()` | scrip_count=2676 | |
| A2 | Broker fails if import broken | Remove live/ from path | BrokerError raised | |
| A3 | Broker fails if <100 scrips | Mock 50 scrips | BrokerError raised | |
| A4 | .env loaded (GEMINI_API_KEY) | `os.environ['GEMINI_API_KEY']` | Non-empty string | |
| A5 | All 6 agents load | `--agents` default | 6 in health check | |
| A6 | Server starts on port 8905 | `curl /health` | `{"ok":true}` | |
| A7 | Autonomous agents start in threads | Check log | "Started autonomous:" x4 | |
| A8 | Crash recovery works | Agent raises on first run | Retries up to 3x | |

## B. BROKER OPERATIONS
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| B1 | buy() returns order_id | `broker.buy('SBIN',1)` | EQ-xxx string | |
| B2 | buy() raises on None return | Mock fail_next | BrokerError | |
| B3 | sell() returns order_id | `broker.sell('SBIN',1)` | EQ-xxx string | |
| B4 | sell() raises on None return | Mock fail_next | BrokerError | |
| B5 | buy_limit() places LIMIT order | `broker.buy_limit('SBIN',1,950)` | order_id | |
| B6 | buy_fno() places F&O order | Mock | FNO-xxx | |
| B7 | sell_fno() places F&O sell | Mock | FNO-xxx | |
| B8 | ltp() returns float | `broker.ltp('SBIN')` | 500-2000 | |
| B9 | ltp() raises for unknown sym | `broker.ltp('FAKE')` | BrokerError | |
| B10 | ltp_safe() returns default | `broker.ltp_safe('FAKE',42)` | 42.0 | |
| B11 | order_filled() checks order book | Fill then check | (True, price) | |
| B12 | order_filled() for unknown | `broker.order_filled('X')` | (False, 0) | |
| B13 | cancel() doesn't crash | `broker.cancel('X')` | No exception | |
| B14 | has_symbol() correct | SBIN=True, FAKE=False | bool | |
| B15 | _check_sym() raises for unknown | `broker.buy('FAKE',1)` | BrokerError | |
| B16 | Every order is audit-logged | Check audit JSONL | BUY_SUBMIT, BUY_OK | |

## C. POSITIONS
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| C1 | open() requires order_id | `open(..., order_id='')` | ValueError | |
| C2 | open() creates position | `open(...)` | Position object | |
| C3 | close() calculates BUY PnL | entry=850, exit=900, qty=10 | pnl=500 | |
| C4 | close() calculates SELL PnL | entry=850, exit=800, qty=10 | pnl=500 | |
| C5 | close() places sell for BUY | close BUY position | broker.sell() called | |
| C6 | close() places buy for SELL | close SELL position | broker.buy() called | |
| C7 | close() handles sell failure | Mock sell fail | Position still closed | |
| C8 | Double close returns None | close same pos twice | Second returns None | |
| C9 | close_all() closes everything | 3 positions open | 0 active after | |
| C10 | find_by_symbol() works | Open SBIN, find SBIN | Position found | |
| C11 | find_by_symbol() None for missing | find FAKE | None | |
| C12 | update_peak() BUY (higher) | peak=850, price=900 | peak=900 | |
| C13 | update_peak() BUY (lower ignored) | peak=900, price=880 | peak=900 | |
| C14 | update_peak() SELL (lower) | peak=850, price=800 | peak=800 | |
| C15 | Thread safety: 200 concurrent | 200 threads open/close | 0 errors, 0 active | |
| C16 | Concurrent close: only 1 wins | 3 threads close same | Exactly 1 non-None | |

## D. FUND MANAGER
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| D1 | request() approves | `fm.request('equity_notif','SBIN',9)` | (True, 15000, tid) | |
| D2 | Max concurrent enforced | 3rd equity_notif request | (False, 0, '') | |
| D3 | Low buffer rejects | Deplete to 10% | Rejected | |
| D4 | Daily loss limit blocks | pnl=-10001, then request | Rejected | |
| D5 | release() frees capital | request then release | available=pool | |
| D6 | release() unknown tid safe | `fm.release('fake')` | No crash | |
| D7 | reset_daily() clears all | After trades | deployed=0, pnl=0 | |
| D8 | Pool=0 rejects all | FundManager(pool=0) | All rejected | |
| D9 | Negative pool rejects all | FundManager(pool=-1000) | All rejected | |

## E. MONITOR
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| E1 | SL hit BUY: price <= sl | price=940, sl=950 | Position closed | |
| E2 | SL hit SELL: price >= sl | price=860, sl=850 | Position closed | |
| E3 | Target hit BUY: price >= target | price=1060, target=1050 | Position closed | |
| E4 | Target hit SELL: price <= target | price=940, target=950 | Position closed | |
| E5 | Trail activates at threshold | +3% move, activate=2% | trail_active=True | |
| E6 | Trail exits on drop | peak=1030, price=1019, trail=1% | Position closed | |
| E7 | EOD 3:10 PM closes all | Mock time 15:10 | All positions closed | |
| E8 | EOD 3:09 PM does NOT close | Mock time 15:09 | Positions still open | |
| E9 | force_exit() closes position | Open SBIN, force_exit | Closed | |
| E10 | force_exit() False for missing | force_exit('FAKE') | False | |
| E11 | _exit() releases FM capital | Close with trade_id | fm.release() called | |
| E12 | No positions = no crash | _tick() with 0 positions | No error | |
| E13 | LTP=0 skipped, no crash | ltp_safe returns 0 | Position unchanged | |

## F. BASE AGENT SUBMIT
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| F1 | FM reject -> return None | fm._loss_hit=True | None, 0 positions | |
| F2 | No price -> release capital | Unknown sym, no LTP | None, FM released | |
| F3 | Broker fail -> release capital | Mock fail_next | None, FM released | |
| F4 | Success -> position created | Valid TradeRequest | pos_id, 1 active | |
| F5 | BUY side -> broker.buy() | side=Side.BUY | buy() called | |
| F6 | SELL side -> broker.sell() | side=Side.SELL | sell() called | |
| F7 | LIMIT order -> buy_limit() | order_type=LIMIT | buy_limit() called | |
| F8 | FnO order -> buy_fno() | is_fno=True | buy_fno() called | |
| F9 | FnO SELL -> sell_fno() | is_fno=True, side=SELL | sell_fno() called | |

## G. EQUITY NOTIF AGENT
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| G1 | Structured signal parsed | {stock,entry,sl,target} | Correct values | |
| G2 | Text signal parsed | "BUY SBIN around 850..." | Correct values | |
| G3 | Body field parsed | {body: "BUY..."} | Correct values | |
| G4 | Message field parsed | {message: "BUY..."} | Correct values | |
| G5 | String prices handled | entry="1,280" | 1280.0 | |
| G6 | SL >= 3% -> SKIP | sl_pct=10% | decision=SKIP | |
| G7 | SL exactly 3% -> SKIP | sl_pct=3.0% | decision=SKIP | |
| G8 | SL 2.99% -> pass SL filter | sl_pct=2.99% | Passes to VWAP | |
| G9 | VWAP fail -> SKIP | Close < VWAP | decision=SKIP | |
| G10 | Market closed -> SKIP | 8 PM | decision=SKIP | |
| G11 | entry > sl -> BUY side | entry=1000, sl=990 | side=BUY | |
| G12 | entry < sl -> SELL side | entry=990, sl=1000 | side=SELL | |
| G13 | EXIT signal detected | "Exit SBIN" | action=EXIT | |
| G14 | BOOK PROFIT detected | "Book Profit SBIN" | action=EXIT | |
| G15 | "close" alone NOT exit | "BUY SBIN close to 850" | NOT exit | |
| G16 | "close position" IS exit | "Close Position SBIN" | action=EXIT | |
| G17 | Exit closes active position | Open then exit | 0 active | |
| G18 | Exit non-existent -> exited=False | No position | exited=False | |
| G19 | Empty signal -> error | {} | error key | |
| G20 | Garbage text -> error | "asdf 123" | error key | |
| G21 | Broker fail -> status=failed | Mock fail | trade.status=failed | |
| G22 | FM reject -> status=failed | loss_hit=True | trade.status=failed | |
| G23 | Sep 9 SKIPPER (SL 1.23%) | Real values | TAKE | |
| G24 | Sep 9 TIPSMUSIC (SL 9.7%) | Real values | SKIP | |

## H. NEWS ORB AGENT
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| H1 | COFORGE gap consumed -> SKIP | proj=-6%, gap=-6.3% | remaining=-0.3% SKIP | |
| H2 | INNOVISION entry_move -> SKIP | entry_move=16.2% | SKIP | |
| H3 | PARKHOSPS passes filters | proj=5%, gap=0.4% | entry_price set | |
| H4 | Limit order placed | After filter pass | order_id returned | |
| H5 | Fill detected via order book | Mock fill, check | Position created | |
| H6 | Unfilled cancelled at 2:45 | Mock deadline | FM released | |
| H7 | Broker fail -> FM released | Mock fail | 0 positions, FM full | |
| H8 | SELL limit order placed | call=SELL | broker.sell(LIMIT) | |
| H9 | SELL position SL above entry | Fill SELL trade | sl > entry | |
| H10 | Dedup same symbol | Two trades same sym | 1 result | |
| H11 | Conflict logged | BUY + SELL same sym | Warning logged | |
| H12 | Late start (after 9:30) | Mock time 9:35 | Returns False, skips | |
| H13 | NaN ATR -> fallback 3% | Bad yfinance data | sl_pct=3.0 | |

## I. COMMODITY AGENT
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| I1 | TRADE parsed correctly | Gemini returns TRADE | direction + confidence | |
| I2 | SKIP returns None | Gemini returns SKIP | No trade | |
| I3 | Low confidence (<7) -> None | confidence=4 | No trade | |
| I4 | Garbage Gemini -> None | "random text" | No trade | |
| I5 | GOLD BUY -> CE option | direction=BUY | fno_sec_id=CE | |
| I6 | GOLD SELL -> PE option | direction=SELL | fno_sec_id=PE | |
| I7 | SL set at -30% premium | premium=350 | sl=245 | |
| I8 | No chain -> no trade | Empty chain | 0 positions | |
| I9 | No LTP -> no trade | ltp=0 | 0 positions | |
| I10 | Broker fail -> FM released | Mock fail | FM available=pool | |
| I11 | Duplicate prevention | Scan twice | Still 1 position | |
| I12 | After close can trade again | Close then scan | New position | |

## J. MULTYFI OPTIONS AGENT
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| J1 | PE BUY signal extracted | "BUY SBIN PE 1000" | sym=SBIN, opt=PE | |
| J2 | CE BUY signal extracted | "BUY RELIANCE CE 1280" | sym=RELIANCE, opt=CE | |
| J3 | SELL signal extracted | "SELL SBIN PE" | action=SELL | |
| J4 | Garbage -> None | "random" | None | |
| J5 | Dedup by sym+date+action | Same signal twice | 1 processed | |
| J6 | Score >= 20 -> TAKE | Mock high score | Trade opened | |
| J7 | Score < 20 -> SKIP | Mock low score | No trade | |
| J8 | SL at -20% premium | premium=20 | sl=16 | |
| J9 | Duplicate position blocked | Open SBIN, try again | Still 1 | |
| J10 | SELL signal closes position | Open then SELL | 0 positions | |
| J11 | No auth token -> exits | Empty creds | run() returns | |
| J12 | Broker fail -> FM released | Mock fail | FM full | |

## K. SERVER
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| K1 | GET /health returns agents | curl | 6 agents listed | |
| K2 | GET /status returns FM data | curl | pool, deployed, pnl | |
| K3 | GET /positions returns active | curl | trades array | |
| K4 | POST /notify routes to agent | curl with signal | Agent processes | |
| K5 | POST /exit force-exits | curl with symbol | exited=true/false | |
| K6 | OPTIONS CORS headers | curl OPTIONS | Access-Control | |
| K7 | Invalid endpoint -> error | POST /foo | error response | |
| K8 | Empty body handled | POST /notify empty | error or {} | |

## L. CROSS-MODULE FLOW
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| L1 | Signal -> FM -> broker -> position -> monitor -> exit | Full lifecycle | All steps logged | |
| L2 | FM approved but broker fails -> capital released | Mock fail | available=pool | |
| L3 | Position closed -> sell order + FM release | Close position | Both happen | |
| L4 | EOD closes all + releases all FM | Mock 3:10 PM | 0 deployed | |
| L5 | Exit notification -> force_exit -> close -> sell -> FM | Send exit signal | All steps | |
| L6 | Two agents trade same stock | Both submit | Both tracked | |

## M. REAL API (SMOKE)
| # | Check | How to test | Expected | Status |
|---|-------|------------|----------|--------|
| M1 | Real broker init | Broker() on GCP | 2676 scrips | |
| M2 | Real SBIN LTP | broker.ltp('SBIN') | 500-2000 | |
| M3 | Real funds | broker.funds() | > 0 | |
| M4 | Real VWAP check | _check_vwap('SBIN') | close + vwap | |
| M5 | Real ATR SL | get_atr_sl('SBIN') | 1-20% | |
| M6 | Real RSS fetch | fetch_all() | 100+ articles | |
| M7 | Real Gemini call | call_safe('OK') | Non-empty | |
| M8 | Real server /health | curl localhost:8905 | ok=true, 6 agents | |
| M9 | Real server /notify SKIP | Wide SL signal | decision=SKIP | |
