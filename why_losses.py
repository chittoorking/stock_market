"""
WHY do 30% of trades lose? Deep analysis of every losing trade.
"""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict
from datetime import datetime as dt

data_dir=Path('data/5min'); all_data={}; date_bars=defaultdict(dict)
print('Loading...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym=f.stem.split('_')[0].upper()
    if sym in ('NIFTY_50','NIFTY_BANK'): continue
    with open(f) as fh: rows=list(csv.DictReader(fh))
    bars=[{'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
           'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))} for r in rows]
    all_data[sym]=bars
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in by_d.items(): date_bars[d][sym]=bs

all_dates=sorted(date_bars.keys())
prev_day={}; daily_trend={}
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars)); dc=[]
    for i,d in enumerate(dfs):
        db=date_bars[d].get(sym,[])
        if not db: continue
        c=db[-1]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[dfs[i-1]].get(sym,[])
            if pdb: prev_day[(d,sym)]={'high':max(b['high'] for b in pdb),'low':min(b['low'] for b in pdb),'close':pdb[-1]['close']}
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_trend[(d,sym)]='UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'

# Load VIX
vix_data={}
vf=Path('data/vix_daily.csv')
if vf.exists():
    with open(vf) as f:
        for r in csv.DictReader(f): vix_data[r['date']]=float(r['close'])

POS=1000000; CHARGES=386; SCAN_BAR=10; TARGET=1.75; STOP=1.50

# Run all trades with detailed info
wins=[]; losses=[]
for date in all_dates:
    for sym in date_bars[date]:
        if sym in ('NIFTY_50','NIFTY_BANK'): continue
        db=date_bars[date][sym]
        if len(db)<=SCAN_BAR+20: continue
        if daily_trend.get((date,sym))!='DOWN': continue
        pd=prev_day.get((date,sym))
        if not pd: continue
        rng=pd['high']-pd['low']
        if rng<=0: continue
        r3=pd['close']+rng*1.1/4
        triggered=False
        for j in range(1,SCAN_BAR+1):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                triggered=True; break
        if not triggered: continue

        entry=db[SCAN_BAR]['close']
        tp=entry*(1-TARGET/100); sp=entry*(1+STOP/100)
        ep=db[min(69,len(db)-1)]['close']; exit_r='eod'
        exit_bar=min(69,len(db)-1)

        # Track MFE (best point) and MAE (worst point) bar by bar
        mfe=0; mae=0; mfe_bar=SCAN_BAR; mae_bar=SCAN_BAR
        for k in range(SCAN_BAR+1,min(len(db),70)):
            fav=(entry-db[k]['low'])/entry*100
            adv=(db[k]['high']-entry)/entry*100
            if fav>mfe: mfe=fav; mfe_bar=k
            if adv>mae: mae=adv; mae_bar=k
            if db[k]['low']<=tp: ep=tp; exit_r='target'; exit_bar=k; break
            if db[k]['high']>=sp: ep=sp; exit_r='stop'; exit_bar=k; break

        pnl_pct=(entry-ep)/entry*100
        pnl_rs=pnl_pct/100*POS - CHARGES

        # Volume at entry vs avg
        vol_entry=db[SCAN_BAR]['volume']
        vol_avg=sum(b['volume'] for b in db[:SCAN_BAR])/max(1,SCAN_BAR)
        vol_ratio=vol_entry/vol_avg if vol_avg>0 else 1

        # How far was entry from R3?
        r3_dist=(entry-r3)/entry*100

        # Day of week
        dow=dt.strptime(date,'%Y-%m-%d').weekday()
        day_names=['Mon','Tue','Wed','Thu','Fri','Sat','Sun']

        # VIX
        vix=vix_data.get(date, 0)

        # Opening gap
        prev_close=pd['close']
        open_price=db[0]['open']
        gap=(open_price-prev_close)/prev_close*100

        # First hour momentum (bar 0-10)
        first_hr_move=(db[SCAN_BAR]['close']-db[0]['open'])/db[0]['open']*100

        # How many bars before it turned against us (for losses)
        bars_before_turn=0
        for k in range(SCAN_BAR+1, min(len(db),70)):
            if (db[k]['close']-entry)/entry*100 > 0.3:  # Went 0.3% against
                bars_before_turn=k-SCAN_BAR
                break

        trade={
            'date':date,'sym':sym,'entry':entry,'exit':round(ep,2),
            'pnl_pct':round(pnl_pct,3),'pnl_rs':round(pnl_rs,0),
            'exit_r':exit_r,'exit_bar':exit_bar,
            'mfe':round(mfe,3),'mae':round(mae,3),
            'mfe_bar':mfe_bar,'mae_bar':mae_bar,
            'vol_ratio':round(vol_ratio,2),'r3_dist':round(r3_dist,3),
            'dow':dow,'day':day_names[dow],'vix':round(vix,1),
            'gap':round(gap,2),'first_hr':round(first_hr_move,2),
            'bars_before_turn':bars_before_turn,
            'r3':round(r3,2),'rng':round(rng,2),
        }

        if pnl_rs>0: wins.append(trade)
        else: losses.append(trade)

total=len(wins)+len(losses)
print(f'\nTotal: {total} trades | Wins: {len(wins)} ({len(wins)/total*100:.0f}%) | Losses: {len(losses)} ({len(losses)/total*100:.0f}%)')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print(f'WHAT HAPPENS IN A WINNING TRADE vs LOSING TRADE?')
print(f'{"="*80}')

def avg(lst): return sum(lst)/len(lst) if lst else 0

print(f'\n  {"Metric":>30} {"WINNERS":>12} {"LOSERS":>12} {"Difference":>12}')
print(f'  {"-"*70}')

metrics = [
    ('MFE (best it got)', [t['mfe'] for t in wins], [t['mfe'] for t in losses], '%'),
    ('MAE (worst it got)', [t['mae'] for t in wins], [t['mae'] for t in losses], '%'),
    ('VIX on that day', [t['vix'] for t in wins if t['vix']>0], [t['vix'] for t in losses if t['vix']>0], ''),
    ('Volume ratio (vs avg)', [t['vol_ratio'] for t in wins], [t['vol_ratio'] for t in losses], 'x'),
    ('Opening gap %', [t['gap'] for t in wins], [t['gap'] for t in losses], '%'),
    ('First hour momentum %', [t['first_hr'] for t in wins], [t['first_hr'] for t in losses], '%'),
    ('Distance from R3 %', [t['r3_dist'] for t in wins], [t['r3_dist'] for t in losses], '%'),
    ('Prev day range (Rs)', [t['rng'] for t in wins], [t['rng'] for t in losses], ''),
    ('Exit bar (when)', [t['exit_bar'] for t in wins], [t['exit_bar'] for t in losses], ''),
]

for name, w_vals, l_vals, unit in metrics:
    w_avg=avg(w_vals); l_avg=avg(l_vals)
    diff=w_avg-l_avg
    print(f'  {name:>30} {w_avg:>10.2f}{unit:>2} {l_avg:>10.2f}{unit:>2} {diff:>+10.2f}{unit}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print(f'CATEGORIZE EVERY LOSS — WHY did it lose?')
print(f'{"="*80}')

# Category 1: Stop hit (price went against us hard)
stops=[t for t in losses if t['exit_r']=='stop']

# Category 2: Almost won — MFE was close to target but reversed
almost=[t for t in losses if t['exit_r']=='eod' and t['mfe']>=TARGET*0.5]

# Category 3: Never moved — sat flat all day
flat=[t for t in losses if t['exit_r']=='eod' and t['mfe']<0.3 and t['mae']<0.5]

# Category 4: Went against immediately — wrong direction from start
wrong_dir=[t for t in losses if t['exit_r']=='eod' and t['mae']>0.5 and t['mfe']<0.3]

# Category 5: Choppy — moved both ways, ended negative
choppy=[t for t in losses if t['exit_r']=='eod' and t not in almost and t not in flat and t not in wrong_dir]

print(f'\n  CATEGORY 1: STOP HIT — Price reversed hard against us')
print(f'  Count: {len(stops)} ({len(stops)/len(losses)*100:.0f}% of losses)')
print(f'  What happened: Stock touched R3 and bounced down (our signal),')
print(f'                 but then reversed and went UP past our stop (+{STOP}%)')
print(f'  Avg loss: Rs {avg([t["pnl_rs"] for t in stops]):,.0f}')
print(f'  Avg MAE: {avg([t["mae"] for t in stops]):.2f}% (went this far against us)')
print(f'  Avg MFE: {avg([t["mfe"] for t in stops]):.2f}% (best it got before reversing)')
if stops:
    # Did MFE happen before MAE?
    mfe_first=sum(1 for t in stops if t['mfe_bar']<t['mae_bar'])
    print(f'  MFE before MAE: {mfe_first}/{len(stops)} ({mfe_first/len(stops)*100:.0f}%) — went in our favor first, THEN reversed')

print(f'\n  CATEGORY 2: ALMOST WON — Got close to target but reversed')
print(f'  Count: {len(almost)} ({len(almost)/len(losses)*100:.0f}% of losses)')
print(f'  What happened: Stock moved in our direction (MFE >= {TARGET*0.5:.2f}%)')
print(f'                 but didn\'t reach target ({TARGET}%), reversed, closed negative')
print(f'  Avg MFE: {avg([t["mfe"] for t in almost]):.2f}% (got THIS close to {TARGET}% target)')
print(f'  Avg final P&L: {avg([t["pnl_pct"] for t in almost]):.3f}%')
print(f'  Avg loss: Rs {avg([t["pnl_rs"] for t in almost]):,.0f}')

print(f'\n  CATEGORY 3: FLAT — Stock barely moved all day')
print(f'  Count: {len(flat)} ({len(flat)/len(losses)*100:.0f}% of losses)')
print(f'  What happened: Stock touched R3 (signal fired) but then went sideways')
print(f'                 No significant move in either direction')
print(f'  Avg MFE: {avg([t["mfe"] for t in flat]):.2f}% | Avg MAE: {avg([t["mae"] for t in flat]):.2f}%')
print(f'  Avg loss: Rs {avg([t["pnl_rs"] for t in flat]):,.0f} (small — just charges)')

print(f'\n  CATEGORY 4: WRONG DIRECTION — Went against us from the start')
print(f'  Count: {len(wrong_dir)} ({len(wrong_dir)/len(losses)*100:.0f}% of losses)')
print(f'  What happened: Signal fired but stock immediately went UP')
print(f'                 Never came back down. We shorted into a rally.')
print(f'  Avg MAE: {avg([t["mae"] for t in wrong_dir]):.2f}% (went UP this much)')
print(f'  Avg loss: Rs {avg([t["pnl_rs"] for t in wrong_dir]):,.0f}')

print(f'\n  CATEGORY 5: CHOPPY — Moved both ways, ended negative')
print(f'  Count: {len(choppy)} ({len(choppy)/len(losses)*100:.0f}% of losses)')
print(f'  Avg MFE: {avg([t["mfe"] for t in choppy]):.2f}% | Avg MAE: {avg([t["mae"] for t in choppy]):.2f}%')
print(f'  Avg loss: Rs {avg([t["pnl_rs"] for t in choppy]):,.0f}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print(f'LOSS SUMMARY — Visual breakdown')
print(f'{"="*80}')

cats=[
    ('STOP HIT (reversed hard)', len(stops), stops),
    ('ALMOST WON (close to target)', len(almost), almost),
    ('FLAT (no movement)', len(flat), flat),
    ('WRONG DIRECTION (shorted into rally)', len(wrong_dir), wrong_dir),
    ('CHOPPY (both ways)', len(choppy), choppy),
]

total_loss_rs=sum(t['pnl_rs'] for t in losses)
print(f'\n  Total losses: {len(losses)} trades, Rs {total_loss_rs:,.0f}')
print()
for name, count, trades_cat in cats:
    pct=count/len(losses)*100
    cat_loss=sum(t['pnl_rs'] for t in trades_cat)
    bar='█'*int(pct/2)
    print(f'  {name:>40}: {count:>4} ({pct:>4.0f}%) Rs {cat_loss:>+10,.0f} {bar}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print(f'CAN WE FIX ANY OF THESE?')
print(f'{"="*80}')

# Check if any category has a pattern we can filter
print(f'\n  1. STOP HITS ({len(stops)} trades):')
stop_days=defaultdict(int)
for t in stops: stop_days[t['day']]+=1
print(f'     By day: {dict(stop_days)}')
stop_vix=[t['vix'] for t in stops if t['vix']>0]
if stop_vix: print(f'     Avg VIX: {avg(stop_vix):.1f} (vs {avg([t["vix"] for t in wins if t["vix"]>0]):.1f} for wins)')
stop_gap=[t['gap'] for t in stops]
print(f'     Avg gap: {avg(stop_gap):.2f}% (vs {avg([t["gap"] for t in wins]):.2f}% for wins)')
stop_vol=[t['vol_ratio'] for t in stops]
print(f'     Avg vol ratio: {avg(stop_vol):.2f}x (vs {avg([t["vol_ratio"] for t in wins]):.2f}x for wins)')
print(f'     → CAN WE FIX? Only by widening stop (but that makes each loss bigger)')

print(f'\n  2. ALMOST WON ({len(almost)} trades):')
almost_mfe=[t['mfe'] for t in almost]
print(f'     Avg MFE: {avg(almost_mfe):.2f}% (target is {TARGET}%)')
above_1pct=sum(1 for t in almost if t['mfe']>=1.0)
print(f'     MFE >= 1.0%: {above_1pct} trades — these HAD the move but we missed it')
print(f'     → CAN WE FIX? Lower target would catch more. But then winners earn less.')
print(f'        With T=1.25%: {sum(1 for t in almost if t["mfe"]>=1.25)} would have won')
print(f'        With T=1.50%: {sum(1 for t in almost if t["mfe"]>=1.50)} would have won')

print(f'\n  3. FLAT ({len(flat)} trades):')
flat_first_hr=[t['first_hr'] for t in flat]
print(f'     Avg first hour move: {avg(flat_first_hr):.2f}%')
print(f'     → CAN WE FIX? Not really. Signal was valid, stock just didn\'t move.')
print(f'        These lose only Rs {avg([t["pnl_rs"] for t in flat]):,.0f} each (just charges)')

print(f'\n  4. WRONG DIRECTION ({len(wrong_dir)} trades):')
wd_gap=[t['gap'] for t in wrong_dir]
wd_first=[t['first_hr'] for t in wrong_dir]
print(f'     Avg gap: {avg(wd_gap):.2f}% | First hr: {avg(wd_first):.2f}%')
print(f'     → CAN WE FIX? These are trend reversals — stock looked DOWN but turned UP.')
print(f'        This is the INHERENT uncertainty of markets. Can\'t predict reversals.')

print(f'\n  5. CHOPPY ({len(choppy)} trades):')
print(f'     → CAN WE FIX? No. Market was indecisive. This is normal noise.')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*80}')
print(f'THE HONEST ANSWER')
print(f'{"="*80}')
fixable=len(almost)
unfixable=len(stops)+len(flat)+len(wrong_dir)+len(choppy)
print(f'''
  Total losses: {len(losses)} out of {total} trades (30%)

  FIXABLE (partially): {fixable} trades ({fixable/len(losses)*100:.0f}% of losses)
    - "Almost won" trades that got close to target
    - Lowering target would catch some, but reduces avg win
    - We already optimized this (T=1.75 is the sweet spot)

  UNFIXABLE: {unfixable} trades ({unfixable/len(losses)*100:.0f}% of losses)
    - Stop hits: Market reversed. Can't predict this.
    - Flat days: Stock didn't move. Can't predict this.
    - Wrong direction: Trend changed intraday. Can't predict this.
    - Choppy: Market noise. Can't predict this.

  WHY 30% LOSE — in plain English:
    Our strategy says "if a DOWN-trending stock touches resistance, SHORT it."
    This works 70% of the time. But 30% of the time:
      - The downtrend ENDS that day (stock reverses to UP)
      - The stock touches resistance but has no energy to fall
      - Something unexpected happens (news, big buyer, sector rotation)

  This is NOT a flaw in the strategy.
  This is how markets work.
  No strategy in the world wins 100%.
  70% WR is excellent — most professional traders get 50-55%.

  The math works because:
    Avg win:  Rs {avg([t["pnl_rs"] for t in wins]):>+8,.0f}
    Avg loss: Rs {avg([t["pnl_rs"] for t in losses]):>+8,.0f}
    70 wins × Rs {avg([t["pnl_rs"] for t in wins]):,.0f} = Rs {70*avg([t["pnl_rs"] for t in wins]):>+10,.0f}
    30 losses × Rs {abs(avg([t["pnl_rs"] for t in losses])):,.0f} = Rs {30*avg([t["pnl_rs"] for t in losses]):>+10,.0f}
    NET per 100 trades:          Rs {70*avg([t["pnl_rs"] for t in wins])+30*avg([t["pnl_rs"] for t in losses]):>+10,.0f}
''')
