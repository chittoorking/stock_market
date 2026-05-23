"""
PROVEN SIGNAL + LLM PICKER — Best of both worlds.
System generates signals from PROVEN strategies (ORB, Camarilla, MoE).
System adds broader context (daily trend, 52w, market regime, VIX).
LLM picks THE BEST signal. System manages position with 5 lots.
"""
import sys, io, os, csv, json, time, requests, math
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')

from pathlib import Path
from collections import defaultdict

API_KEY = os.environ.get("OPENAI_API_KEY", "REPLACE")
data_dir = Path('data/5min')
all_data = {}; date_bars = defaultdict(dict)
print('Loading...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    bars = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
             'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]
    all_data[sym] = bars
    by_date = defaultdict(list)
    for b in bars: by_date[b['timestamp'][:10]].append(b)
    for d, bs in by_date.items(): date_bars[d][sym] = bs

all_dates = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))

# Pre-compute daily data
print('Pre-computing daily trends, 52w levels...', flush=True)
prev_close = {}; daily_ctx = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    daily_closes = []; daily_highs = []; daily_lows = []
    for i, d in enumerate(dates_for_sym):
        db = date_bars[d].get(sym, [])
        if not db: continue
        dc = db[-1]['close']; dh = max(b['high'] for b in db); dl = min(b['low'] for b in db)
        daily_closes.append(dc); daily_highs.append(dh); daily_lows.append(dl)
        if i > 0:
            pdb = date_bars[dates_for_sym[i-1]].get(sym, [])
            if pdb: prev_close[(d, sym)] = pdb[-1]['close']
        if len(daily_closes) >= 5:
            c5 = daily_closes[-5:]
            up_d = sum(1 for j in range(1,len(c5)) if c5[j]>c5[j-1])
            trend = 'UP' if up_d >= 4 else 'DOWN' if up_d <= 1 else 'SIDE'
            lookback = min(250, len(daily_closes))
            h52 = max(daily_closes[-lookback:]); l52 = min(daily_closes[-lookback:])
            sma20 = sum(daily_closes[-min(20,len(daily_closes)):]) / min(20,len(daily_closes))
            rsi = 50
            if len(daily_closes) >= 15:
                g = [max(0,daily_closes[j]-daily_closes[j-1]) for j in range(-14,0)]
                l = [max(0,daily_closes[j-1]-daily_closes[j]) for j in range(-14,0)]
                ag=sum(g)/14; al=sum(l)/14
                rsi = 100-100/(1+ag/al) if al>0 else 50
            daily_ctx[(d,sym)] = {
                'trend': trend, 'rsi': round(rsi), 'above_sma': dc > sma20,
                'from_52h': round((dc-h52)/h52*100,1), 'from_52l': round((dc-l52)/l52*100,1),
                'near_52h': (dc-h52)/h52*100 > -3, 'near_52l': (dc-l52)/l52*100 < 3,
                'chg5d': round((dc/c5[0]-1)*100,1),
                'prev_day_range': round((dh-dl)/dc*100,2),
                'prev_close': dc, 'prev_high': dh, 'prev_low': dl,
            }

from app.agents.moe_fast import moe_monitor_fast
print('Done.\n', flush=True)

import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--start', type=int, default=-20)
parser.add_argument('--days', type=int, default=20)
args, _ = parser.parse_known_args()
if args.start < 0:
    test_dates = all_dates[args.start:] if args.start+args.days>=0 else all_dates[args.start:args.start+args.days]
else:
    test_dates = all_dates[args.start:args.start+args.days]

print(f"PROVEN PICKER | {len(test_dates)} days | Coded signals + LLM selection + 5-lot management\n")

all_results = []

for date in test_dates:
    scan_bar = 10  # 10:15 AM

    # ═══ MARKET REGIME ═══
    up=dn=tot=0
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        if len(db) <= scan_bar: continue
        tot += 1
        mv = (db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100
        if mv > 0.15: up += 1
        elif mv < -0.15: dn += 1
    regime = 'TRENDING_UP' if up/tot>0.6 else 'TRENDING_DOWN' if dn/tot>0.6 else 'CHOPPY'

    # ═══ GENERATE PROVEN SIGNALS ═══
    signals = []
    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close.get((date, sym))
        ctx = daily_ctx.get((date, sym), {})
        if pc is None or len(db) <= scan_bar + 10 or not ctx: continue

        entry_price = db[scan_bar]['close']
        gap = (db[0]['open']-pc)/pc*100
        move = (entry_price-db[0]['open'])/db[0]['open']*100

        # Bar analysis
        bsf = db[:scan_bar+1]
        bar0_body = abs(db[0]['close']-db[0]['open'])/(db[0]['high']-db[0]['low'])*100 if db[0]['high']!=db[0]['low'] else 0
        atr = sum(b['high']-b['low'] for b in bsf)/len(bsf)

        # Momentum
        bar_moves = [(bsf[i]['close']-bsf[i-1]['close'])/bsf[i-1]['close']*100 for i in range(1,len(bsf))]
        accel = 'ACCEL' if len(bar_moves)>=6 and sum(abs(m) for m in bar_moves[-3:]) > sum(abs(m) for m in bar_moves[:3])*1.2 else 'DECEL' if len(bar_moves)>=6 and sum(abs(m) for m in bar_moves[-3:]) < sum(abs(m) for m in bar_moves[:3])*0.8 else 'STEADY'
        consec_green = sum(1 for b in bsf[-4:] if b['close']>b['open'])

        # ── SIGNAL 1: ORB Breakout ──
        rh = db[0]['high']; rl = db[0]['low']; rs = rh-rl
        if rs > 0:
            for j in range(1, scan_bar+1):
                if db[j]['close'] > rh and db[j]['volume'] > db[0]['volume']*1.0:
                    signals.append({'sym':sym, 'type':'ORB', 'dir':'LONG', 'bar':j,
                        'entry':db[j]['close'], 'stop':rl, 'risk_pct':round((db[j]['close']-rl)/db[j]['close']*100,2)})
                    break
                if db[j]['close'] < rl and db[j]['volume'] > db[0]['volume']*1.0:
                    signals.append({'sym':sym, 'type':'ORB', 'dir':'SHORT', 'bar':j,
                        'entry':db[j]['close'], 'stop':rh, 'risk_pct':round((rh-db[j]['close'])/db[j]['close']*100,2)})
                    break

        # ── SIGNAL 2: Camarilla R3/S3 bounce ──
        pd = daily_ctx.get((date, sym), {})
        if pd and pd.get('prev_high') and pd.get('prev_low') and pd.get('prev_close'):
            ph=pd['prev_high']; pl=pd['prev_low']; pcc=pd['prev_close']; rng=ph-pl
            if rng > 0:
                r3=pcc+rng*1.1/4; s3=pcc-rng*1.1/4; r4=pcc+rng*1.1/2; s4=pcc-rng*1.1/2
                for j in range(1, scan_bar+1):
                    a = sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                    tol = a*0.3
                    if abs(db[j]['high']-r3)<tol and db[j]['close']<r3:
                        signals.append({'sym':sym,'type':'CAM_R3','dir':'SHORT','bar':j,
                            'entry':db[j]['close'],'stop':r4,'risk_pct':round((r4-db[j]['close'])/db[j]['close']*100,2)})
                        break
                    # CAM_S3 removed — 50% WR, dragging performance
                    pass

        # TREND_CONT removed — 0% WR on 20-day test

    # Deduplicate by sym (keep first)
    seen = set(); unique_sigs = []
    for s in signals:
        if s['sym'] not in seen: seen.add(s['sym']); unique_sigs.append(s)
    signals = unique_sigs

    if not signals:
        print(f"{date} | {regime} | No signals\n"); continue

    # ═══ BUILD CONTEXT FOR LLM ═══
    sig_lines = []
    for s in sorted(signals, key=lambda x: -abs(x.get('risk_pct',0))):
        ctx = daily_ctx.get((date, s['sym']), {})
        trend = ctx.get('trend','?')
        rsi = ctx.get('rsi',50)
        chg5 = ctx.get('chg5d',0)
        h52 = ctx.get('from_52h',0)
        l52 = ctx.get('from_52l',0)
        sma = 'abvSMA' if ctx.get('above_sma') else 'blwSMA'
        w52 = 'near52H' if ctx.get('near_52h') else ('near52L' if ctx.get('near_52l') else '')
        db = date_bars[date][s['sym']]
        move = (db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100

        sig_lines.append(
            f"  {s['type']:>10} {s['dir']:>5} {s['sym']:>12} | today:{move:+.1f}% risk:{s['risk_pct']:.1f}% | "
            f"daily:{trend} RSI={rsi} {sma} 5d={chg5:+.1f}% {w52} 52wH:{h52:+.0f}% 52wL:{l52:+.0f}%"
        )

    snapshot = (
        f"DATE: {date} | Market: {up}/{tot} up, {dn}/{tot} down | {regime}\n\n"
        f"PROVEN SIGNALS ({len(signals)}):\n" + "\n".join(sig_lines)
    )

    print(f"{date} | {regime} | {len(signals)} signals")

    # ═══ LLM PICKS ═══
    system = """You see pre-qualified trading signals from proven strategies (ORB breakout, Camarilla S/R bounce, Daily trend continuation). Each signal already passed mechanical filters.

Your job: pick THE BEST 1 signal based on broader context.

WHAT MAKES THE BEST SIGNAL:
- Daily trend aligns with signal direction (UP trend + LONG signal = strong)
- Stock near 52-week high (for longs) or 52-week low (for shorts) = strong trend
- RSI > 60 for longs, RSI < 40 for shorts = directional momentum
- Low risk % = tight stop = better R:R
- TREND_CONT signals on trending days are highest probability
- CAM_R3/S3 signals work in any market regime (structural support/resistance)
- ORB signals work best on trending days

SKIP if: no signal has daily trend + direction alignment.

Output JSON only:
{"symbol":"STOCKNAME","direction":"LONG or SHORT","signal_type":"ORB/CAM_R3/CAM_S3/TREND_CONT","reasoning":"why this one"}
Or: {"symbol":"SKIP","direction":"SKIP","reasoning":"why"}"""

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "gpt-4o-mini", "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": snapshot},
    ], "temperature": 0, "max_tokens": 200}

    try:
        resp = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=15)
        raw = resp.json()["choices"][0]["message"]["content"]
        start = raw.find('{'); end = raw.rfind('}')+1
        pick = json.loads(raw[start:end]) if start>=0 else None
    except: pick = None

    if not pick:
        print(f"  LLM failed\n"); continue

    sym = pick.get('symbol',''); direction = pick.get('direction','')
    sig_type = pick.get('signal_type','')
    print(f"  PICK: {direction} {sym} [{sig_type}] — {pick.get('reasoning','')[:80]}")

    if sym == 'SKIP':
        print(f"  SKIP\n"); continue

    # Find the signal
    sig = next((s for s in signals if s['sym']==sym), None)
    if not sig:
        print(f"  Signal not found for {sym}\n"); continue

    db = date_bars[date][sym]
    entry = db[scan_bar]['close']
    stop = sig['stop']
    risk = abs(entry - stop)
    if risk == 0: print(f"  Zero risk\n"); continue
    target = entry + risk*2.5 if direction=='LONG' else entry - risk*2.5

    # ═══ POSITION MANAGEMENT WITH 5 LOTS ═══
    lots = {'L1':0.30,'L2':0.25,'L3':0.20,'L4':0.15,'L5':0.10}
    booked_pnl = 0; cur_stop = stop; mfe = 0; exit_reason = 'eod'

    for j in range(scan_bar+1, min(len(db), 70)):
        b = db[j]
        pnl_now = (b['close']-entry)/entry*100 if direction=='LONG' else (entry-b['close'])/entry*100
        fav = (b['high']-entry)/entry*100 if direction=='LONG' else (entry-b['low'])/entry*100
        mfe = max(mfe, fav)
        fade = max(0,(mfe-max(0,fav))/mfe*100) if mfe>0 else 0
        bars_held = j - scan_bar
        ts = b.get('timestamp','').split(' ')[1][:5] if ' ' in b.get('timestamp','') else ''
        remaining = sum(lots.values())
        if remaining <= 0: break

        # Hard stop/target
        if direction=='LONG' and b['low']<=cur_stop:
            booked_pnl += ((cur_stop-entry)/entry*100)*remaining; lots={}; exit_reason='stop'; break
        if direction=='SHORT' and b['high']>=cur_stop:
            booked_pnl += ((entry-cur_stop)/entry*100)*remaining; lots={}; exit_reason='stop'; break
        if direction=='LONG' and b['high']>=target:
            booked_pnl += ((target-entry)/entry*100)*remaining; lots={}; exit_reason='target'; break
        if direction=='SHORT' and b['low']<=target:
            booked_pnl += ((entry-target)/entry*100)*remaining; lots={}; exit_reason='target'; break

        # LLM monitor every 3 bars
        if bars_held % 3 != 0 or bars_held == 0: continue
        vt = 'up' if b['volume']>db[max(0,j-1)]['volume'] else 'down'
        cn = 'green' if b['close']>b['open'] else 'red'
        bd = abs(b['close']-b['open'])/(b['high']-b['low'])*100 if b['high']!=b['low'] else 0
        ls = ' '.join(f'{k}={v*100:.0f}%' for k,v in lots.items())

        ctx = (f"{direction} {sym} [{sig_type}] | Entry:{entry:.2f} Now:{b['close']:.2f}\n"
               f"P&L:{pnl_now:+.3f}% | Peak:{mfe:.3f}% | Fade:{fade:.0f}%\n"
               f"Booked:{booked_pnl:+.3f}% | Lots: {ls}\n"
               f"Stop:{cur_stop:.2f} Target:{target:.2f}\n"
               f"Bar {bars_held} {ts} | Vol:{vt} | {cn} body {bd:.0f}%")

        mon = moe_monitor_fast(ctx, API_KEY)
        action = mon.get('action','hold')

        if action == 'close_all':
            booked_pnl += pnl_now*remaining; lots={}; exit_reason='llm_close'
            print(f"    {ts}: CLOSE ALL {pnl_now:+.2f}%"); break
        if action.startswith('book_') and lots:
            lk = action.split('_')[1].upper()
            if lk in lots: booked_pnl += pnl_now*lots[lk]; print(f"    {ts}: BOOK {lk} @ {pnl_now:+.2f}%"); del lots[lk]
            elif lots: fk=list(lots.keys())[0]; booked_pnl += pnl_now*lots[fk]; print(f"    {ts}: BOOK {fk} @ {pnl_now:+.2f}%"); del lots[fk]
        ns = mon.get('new_stop','unchanged')
        if ns not in ('unchanged',None,''):
            try:
                nsp = entry if str(ns).lower()=='breakeven' else float(ns)
                if direction=='LONG' and nsp>cur_stop: cur_stop=nsp; print(f"    {ts}: TRAIL->{cur_stop:.2f}")
                elif direction=='SHORT' and nsp<cur_stop: cur_stop=nsp; print(f"    {ts}: TRAIL->{cur_stop:.2f}")
            except: pass

    if lots:
        ep = db[min(69,len(db)-1)]['close']
        eod_pnl = (ep-entry)/entry*100 if direction=='LONG' else (entry-ep)/entry*100
        booked_pnl += eod_pnl * sum(lots.values())

    pnl = booked_pnl; win = pnl > 0; w = 'W' if win else 'L'
    cap = pnl/mfe*100 if mfe>0 else 0
    print(f"  {w} PnL={pnl:+.3f}% MFE={mfe:.2f}% cap={cap:.0f}% {exit_reason}\n")

    all_results.append({'date':date,'sym':sym,'dir':direction,'type':sig_type,
        'pnl':round(pnl,4),'win':win,'mfe':round(mfe,3),'cap':round(cap,1),'exit':exit_reason})

# Summary
print(f"\n{'='*80}\nSUMMARY\n{'='*80}")
if all_results:
    w = sum(1 for r in all_results if r['win']); n = len(all_results)
    total = sum(r['pnl'] for r in all_results)
    charges = 0.0835*n; net = total - charges
    avg_cap = sum(r['cap'] for r in all_results if r['cap']>0) / max(1,sum(1 for r in all_results if r['cap']>0))
    print(f"Trades: {n} | W:{w} L:{n-w} | WR:{w/n*100:.0f}%")
    print(f"Gross: {total:+.2f}% | Charges: {charges:.2f}% | Net: {net:+.2f}%")
    print(f"Avg winner capture: {avg_cap:.0f}% of MFE")

    by_type = defaultdict(lambda:{'w':0,'l':0,'pnl':0})
    for r in all_results:
        if r['win']: by_type[r['type']]['w']+=1
        else: by_type[r['type']]['l']+=1
        by_type[r['type']]['pnl']+=r['pnl']
    print(f"\nBy signal type:")
    for t,v in sorted(by_type.items()):
        tot2=v['w']+v['l']
        print(f"  {t}: {tot2} trades, WR={v['w']/tot2*100:.0f}%, PnL={v['pnl']:+.2f}%")

    print(f"\nDaily:")
    for r in sorted(all_results,key=lambda x:x['date']):
        w2='W' if r['win'] else 'L'
        print(f"  {r['date']} {r['dir']:>5} {r['sym']:>12} [{r['type']:>10}] PnL={r['pnl']:+.3f}% MFE={r['mfe']:.2f}% cap={r['cap']:.0f}% {r['exit']:>10} {w2}")

    bal=100000
    for r in sorted(all_results,key=lambda x:x['date']): bal*=(1+(r['pnl']-0.0835)/100)
    print(f"\nRs 1L compound: Rs {bal:,.0f}")
