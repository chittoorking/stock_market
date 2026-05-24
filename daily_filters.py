"""
DAILY-LEVEL PREDICTION: Can broader market context predict choppy/reversal days?
These signals are available BEFORE market opens.
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
    with open(f) as fh: rows=list(csv.DictReader(fh))
    bars=[{'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
           'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))} for r in rows]
    all_data[sym]=bars
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in by_d.items(): date_bars[d][sym]=bs

all_dates=sorted(date_bars.keys())

# Build daily OHLC per stock
daily_ohlc=defaultdict(dict)  # daily_ohlc[sym][date] = {open, high, low, close, volume}
for sym,bars in all_data.items():
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in sorted(by_d.items()):
        daily_ohlc[sym][d]={
            'open':bs[0]['open'],'high':max(b['high'] for b in bs),
            'low':min(b['low'] for b in bs),'close':bs[-1]['close'],
            'volume':sum(b['volume'] for b in bs),
            'range':max(b['high'] for b in bs)-min(b['low'] for b in bs),
        }

# Build trends and prev_day
prev_day={}; daily_trend={}
for sym in all_data:
    sym_dates=sorted(daily_ohlc[sym].keys())
    dc=[]
    for i,d in enumerate(sym_dates):
        c=daily_ohlc[sym][d]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[sym_dates[i-1]].get(sym,[])
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

# NIFTY daily
nifty_daily={}
if 'NIFTY_50' in daily_ohlc:
    nifty_daily=daily_ohlc['NIFTY_50']

POS=1000000; CHARGES=386; SCAN_BAR=10; TARGET=1.75; STOP=1.50
print(f'Loaded.\n')

# Compute daily features and run trades
trades=[]
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
        mfe=0; mae=0
        for k in range(SCAN_BAR+1,min(len(db),70)):
            fav=(entry-db[k]['low'])/entry*100; adv=(db[k]['high']-entry)/entry*100
            mfe=max(mfe,fav); mae=max(mae,adv)
            if db[k]['low']<=tp: ep=tp; exit_r='target'; break
            if db[k]['high']>=sp: ep=sp; exit_r='stop'; break
        pnl_pct=(entry-ep)/entry*100
        pnl_rs=pnl_pct/100*POS-CHARGES

        cat='WIN'
        if pnl_rs<=0:
            if exit_r=='stop': cat='STOP'
            elif mfe>=TARGET*0.5: cat='ALMOST'
            elif mfe<0.3 and mae<0.5: cat='FLAT'
            elif mae>0.5 and mfe<0.3: cat='WRONG_DIR'
            else: cat='CHOPPY'

        # ═══ DAILY FEATURES (known before market opens) ═══
        sym_dates=sorted(daily_ohlc[sym].keys())
        di=sym_dates.index(date) if date in sym_dates else -1
        if di<5: continue

        # 1. How many CONSECUTIVE down days before today?
        consec_down=0
        for back in range(1,20):
            if di-back<0: break
            prev_d=sym_dates[di-back]
            if daily_ohlc[sym][prev_d]['close']<daily_ohlc[sym][sym_dates[di-back-1]]['close'] if di-back-1>=0 else False:
                consec_down+=1
            else: break

        # 2. Previous day candle type
        prev_d=sym_dates[di-1]
        prev_candle=daily_ohlc[sym][prev_d]
        prev_body=abs(prev_candle['close']-prev_candle['open'])
        prev_range=prev_candle['range']
        # Doji = small body relative to range
        is_doji=prev_body<prev_range*0.2 if prev_range>0 else False
        # Hammer = long lower wick (reversal signal)
        lower_wick=min(prev_candle['open'],prev_candle['close'])-prev_candle['low']
        is_hammer=lower_wick>prev_body*2 and lower_wick>prev_range*0.4 if prev_range>0 else False
        # Engulfing = today's range engulfs yesterday's
        prev2_d=sym_dates[di-2] if di>=2 else None
        is_engulfing=False
        if prev2_d:
            prev2=daily_ohlc[sym][prev2_d]
            is_engulfing=prev_candle['close']>prev2['high'] and prev2['close']<prev2['open']

        # 3. Range expansion/contraction (last 3 days)
        ranges_3d=[daily_ohlc[sym][sym_dates[di-k]]['range'] for k in range(1,4) if di-k>=0]
        range_expanding=ranges_3d[0]>ranges_3d[-1]*1.3 if len(ranges_3d)>=2 and ranges_3d[-1]>0 else False
        range_contracting=ranges_3d[0]<ranges_3d[-1]*0.7 if len(ranges_3d)>=2 and ranges_3d[-1]>0 else False
        avg_range_3d=sum(ranges_3d)/len(ranges_3d) if ranges_3d else 0

        # 4. How far has stock fallen in last 5 days? (exhaustion)
        close_5d_ago=daily_ohlc[sym][sym_dates[di-5]]['close'] if di>=5 else entry
        fall_5d=(close_5d_ago-pd['close'])/close_5d_ago*100 if close_5d_ago>0 else 0

        # 5. NIFTY trend (market context)
        nifty_trend='UNKNOWN'
        if date in nifty_daily and di>=5:
            nifty_dates=sorted(nifty_daily.keys())
            ni=nifty_dates.index(date) if date in nifty_dates else -1
            if ni>=5:
                nc=[nifty_daily[nifty_dates[ni-k]]['close'] for k in range(5,-1,-1)]
                nup=sum(1 for j in range(1,len(nc)) if nc[j]>nc[j-1])
                nifty_trend='UP' if nup>=4 else 'DOWN' if nup<=1 else 'SIDE'

        # 6. Market breadth yesterday: how many stocks closed DOWN?
        prev_date=all_dates[all_dates.index(date)-1] if date!=all_dates[0] else date
        stocks_down=0; stocks_total=0
        for s in date_bars.get(prev_date,{}):
            if s in ('NIFTY_50','NIFTY_BANK'): continue
            sdb=date_bars[prev_date].get(s,[])
            if len(sdb)<2: continue
            stocks_total+=1
            if sdb[-1]['close']<sdb[0]['open']: stocks_down+=1
        breadth_down=stocks_down/stocks_total*100 if stocks_total>0 else 50

        # 7. VIX level and direction
        vix=vix_data.get(date,0)
        prev_vix=vix_data.get(prev_date,0) if prev_date!=date else 0
        vix_rising=vix>prev_vix*1.05 if prev_vix>0 else False

        # 8. Where did yesterday close within its range? (0=at low, 1=at high)
        close_in_range=(pd['close']-pd['low'])/rng if rng>0 else 0.5

        # 9. Volume trend: yesterday vs 3-day avg
        vol_yesterday=daily_ohlc[sym][prev_d]['volume'] if prev_d in daily_ohlc[sym] else 0
        vol_3d=sum(daily_ohlc[sym][sym_dates[di-k]]['volume'] for k in range(1,4) if di-k>=0)/min(3,di)
        vol_surge=vol_yesterday/vol_3d if vol_3d>0 else 1

        # 10. Gap from prev close to today's open
        today_open=db[0]['open']
        gap_pct=(today_open-pd['close'])/pd['close']*100

        trades.append({
            'cat':cat, 'pnl':pnl_rs, 'date':date, 'sym':sym,
            'consec_down':consec_down, 'is_doji':is_doji, 'is_hammer':is_hammer,
            'is_engulfing':is_engulfing, 'range_expanding':range_expanding,
            'range_contracting':range_contracting, 'fall_5d':round(fall_5d,2),
            'nifty_trend':nifty_trend, 'breadth_down':round(breadth_down,1),
            'vix':round(vix,1), 'vix_rising':vix_rising,
            'close_in_range':round(close_in_range,2), 'vol_surge':round(vol_surge,2),
            'gap_pct':round(gap_pct,2), 'avg_range_3d':round(avg_range_3d,2),
        })

def avg(lst): return sum(lst)/len(lst) if lst else 0

wins=[t for t in trades if t['cat']=='WIN']
choppy=[t for t in trades if t['cat']=='CHOPPY']
wrong=[t for t in trades if t['cat']=='WRONG_DIR']
all_losses=[t for t in trades if t['cat']!='WIN']

print(f'Total: {len(trades)} | Wins: {len(wins)} | Choppy: {len(choppy)} | Wrong: {len(wrong)}\n')

# ═══════════════════════════════════════════════════════════════
print('='*90)
print('DAILY FEATURES: Wins vs Choppy vs Wrong Direction')
print('='*90)

print(f'\n  {"Feature":>35} {"WINS":>8} {"CHOPPY":>8} {"WRONG":>8} {"Diff?":>6}')
print(f'  {"-"*70}')

feats=[
    ('Consecutive down days', 'consec_down'),
    ('Fall in last 5 days %', 'fall_5d'),
    ('Breadth (% stocks down yday)', 'breadth_down'),
    ('Close in range (0=low 1=high)', 'close_in_range'),
    ('Volume surge (yday vs 3d avg)', 'vol_surge'),
    ('VIX', 'vix'),
    ('Gap %', 'gap_pct'),
]
for name, key in feats:
    w=avg([t[key] for t in wins])
    c=avg([t[key] for t in choppy])
    wr=avg([t[key] for t in wrong])
    diff=max(abs(w-c),abs(w-wr))/max(abs(w),0.001)*100
    marker=' <<<' if diff>15 else ''
    print(f'  {name:>35} {w:>8.2f} {c:>8.2f} {wr:>8.2f} {diff:>5.0f}%{marker}')

bool_feats=[
    ('Prev day Doji %', 'is_doji'),
    ('Prev day Hammer %', 'is_hammer'),
    ('Prev day Engulfing %', 'is_engulfing'),
    ('Range expanding %', 'range_expanding'),
    ('Range contracting %', 'range_contracting'),
    ('VIX rising %', 'vix_rising'),
]
print()
for name, key in bool_feats:
    w=sum(1 for t in wins if t[key])/len(wins)*100
    c=sum(1 for t in choppy if t[key])/len(choppy)*100
    wr=sum(1 for t in wrong if t[key])/len(wrong)*100
    diff=max(abs(w-c),abs(w-wr))
    marker=' <<<' if diff>5 else ''
    print(f'  {name:>35} {w:>7.1f}% {c:>7.1f}% {wr:>7.1f}% {diff:>5.1f}pp{marker}')

# NIFTY trend
print(f'\n  NIFTY trend distribution:')
for trend in ['UP','DOWN','SIDE']:
    w=sum(1 for t in wins if t['nifty_trend']==trend)/len(wins)*100
    c=sum(1 for t in choppy if t['nifty_trend']==trend)/len(choppy)*100
    wr=sum(1 for t in wrong if t['nifty_trend']==trend)/len(wrong)*100
    print(f'    NIFTY {trend:>5}: Wins={w:.0f}%, Choppy={c:.0f}%, Wrong={wr:.0f}%')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('TEST DAILY FILTERS')
print('='*90)

baseline_per=sum(t['pnl'] for t in trades)/len(trades)
print(f'BASELINE: {len(trades)} trades, Rs {baseline_per:+,.0f}/trade\n')

filters=[
    # Consecutive down days
    ('ConsecDown >= 2', lambda t: t['consec_down']>=2),
    ('ConsecDown >= 3', lambda t: t['consec_down']>=3),
    ('ConsecDown <= 3', lambda t: t['consec_down']<=3),
    ('ConsecDown 1-3', lambda t: 1<=t['consec_down']<=3),
    # Fall exhaustion
    ('Fall5d < 3%', lambda t: t['fall_5d']<3),
    ('Fall5d < 5%', lambda t: t['fall_5d']<5),
    ('Fall5d 1-4%', lambda t: 1<=t['fall_5d']<=4),
    # Reversal candles (SKIP if prev day shows reversal)
    ('No Doji yesterday', lambda t: not t['is_doji']),
    ('No Hammer yesterday', lambda t: not t['is_hammer']),
    ('No Engulfing yesterday', lambda t: not t['is_engulfing']),
    ('No reversal signals', lambda t: not t['is_doji'] and not t['is_hammer'] and not t['is_engulfing']),
    # NIFTY trend alignment
    ('NIFTY also DOWN', lambda t: t['nifty_trend']=='DOWN'),
    ('NIFTY not UP', lambda t: t['nifty_trend']!='UP'),
    # Breadth
    ('Breadth > 50% down', lambda t: t['breadth_down']>50),
    ('Breadth > 60% down', lambda t: t['breadth_down']>60),
    # Range
    ('Range NOT expanding', lambda t: not t['range_expanding']),
    ('Range contracting', lambda t: t['range_contracting']),
    # VIX
    ('VIX not rising', lambda t: not t['vix_rising']),
    ('VIX > 13', lambda t: t['vix']>13),
    ('VIX 13-20', lambda t: 13<=t['vix']<=20),
    # Volume
    ('No volume surge (< 1.5x)', lambda t: t['vol_surge']<1.5),
    ('Volume surge (> 1.2x)', lambda t: t['vol_surge']>1.2),
    # Close position
    ('Prev close in lower half', lambda t: t['close_in_range']<0.5),
    ('Prev close in lower 40%', lambda t: t['close_in_range']<0.4),
    # Gap
    ('Gap down (< 0%)', lambda t: t['gap_pct']<0),
    ('Small gap (-0.5 to +0.5)', lambda t: -0.5<=t['gap_pct']<=0.5),
]

results=[]
for name, filt in filters:
    filtered=[t for t in trades if filt(t)]
    if len(filtered)<100: continue
    n=len(filtered); w=sum(1 for t in filtered if t['cat']=='WIN')
    total=sum(t['pnl'] for t in filtered)
    per=total/n
    skip_c=sum(1 for t in choppy if not filt(t))
    skip_w=sum(1 for t in wrong if not filt(t))
    skip_win=sum(1 for t in wins if not filt(t))
    imp=(per-baseline_per)/baseline_per*100
    results.append((name,n,w/n*100,per,total,skip_c,skip_w,skip_win,imp))

results.sort(key=lambda x:x[3],reverse=True)
print(f'  {"Filter":>35} {"N":>5} {"WR":>4} {"/trade":>8} {"SkipChop":>8} {"SkipWrng":>8} {"SkipWin":>8} {"Improv":>7}')
print(f'  {"-"*95}')
for name,n,wr,per,total,sc,sw,swi,imp in results:
    marker=' <<<' if imp>3 and swi<200 else ''
    print(f'  {name:>35} {n:>5} {wr:>3.0f}% Rs{per:>+7,.0f} {sc:>8} {sw:>8} {swi:>8} {imp:>+6.1f}%{marker}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*90}')
print('BEST COMBOS')
print('='*90)

combos=[
    ('BASELINE', lambda t: True),
    ('No reversal + NIFTY not UP', lambda t: not t['is_doji'] and not t['is_hammer'] and not t['is_engulfing'] and t['nifty_trend']!='UP'),
    ('No reversal + Breadth>50%', lambda t: not t['is_doji'] and not t['is_hammer'] and not t['is_engulfing'] and t['breadth_down']>50),
    ('No reversal + ConsecDown 1-3', lambda t: not t['is_doji'] and not t['is_hammer'] and not t['is_engulfing'] and 1<=t['consec_down']<=3),
    ('NIFTY DOWN + Breadth>50%', lambda t: t['nifty_trend']=='DOWN' and t['breadth_down']>50),
    ('No reversal + NIFTY DOWN', lambda t: not t['is_doji'] and not t['is_hammer'] and not t['is_engulfing'] and t['nifty_trend']=='DOWN'),
    ('ConsecDown 1-3 + CloseInLower50%', lambda t: 1<=t['consec_down']<=3 and t['close_in_range']<0.5),
    ('No hammer + Fall5d<5% + Breadth>50', lambda t: not t['is_hammer'] and t['fall_5d']<5 and t['breadth_down']>50),
]

print(f'\n  {"Combo":>50} {"N":>6} {"WR":>4} {"/trade":>8} {"Total":>12} {"Improv":>7}')
print(f'  {"-"*95}')
for name, filt in combos:
    filtered=[t for t in trades if filt(t)]
    if len(filtered)<100: continue
    n=len(filtered); w=sum(1 for t in filtered if t['cat']=='WIN')
    total=sum(t['pnl'] for t in filtered); per=total/n
    imp=(per-baseline_per)/baseline_per*100
    marker=' <<<' if imp>3 else ''
    print(f'  {name:>50} {n:>6} {w/n*100:>3.0f}% Rs{per:>+7,.0f} Rs{total:>+11,.0f} {imp:>+6.1f}%{marker}')

# Walk-forward
print(f'\n  Walk-forward verification:')
train=[t for t in trades if t['date']<'2025-01-01']
test=[t for t in trades if t['date']>='2025-01-01']
print(f'  {"Combo":>50} {"Train/tr":>9} {"Test/tr":>9} {"Drop":>6}')
print(f'  {"-"*80}')
for name, filt in combos:
    tr=[t for t in train if filt(t)]
    te=[t for t in test if filt(t)]
    if len(tr)<50 or len(te)<50: continue
    trp=sum(t['pnl'] for t in tr)/len(tr)
    tep=sum(t['pnl'] for t in te)/len(te)
    drop=(tep-trp)/trp*100
    marker=' <<<' if tep>3200 else ''
    print(f'  {name:>50} Rs{trp:>+8,.0f} Rs{tep:>+8,.0f} {drop:>+5.0f}%{marker}')
