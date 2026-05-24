"""
MIRROR TEST: If CAM_R3 SHORT works on downtrends,
does CAM_S3 LONG work on uptrends?

SHORT: DOWN trend + touches R3 resistance + bounces DOWN
LONG:  UP trend   + touches S3 support    + bounces UP
"""
import sys,io,csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

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
daily_ohlc=defaultdict(dict)
for sym,bars in all_data.items():
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in sorted(by_d.items()):
        daily_ohlc[sym][d]={'open':bs[0]['open'],'close':bs[-1]['close'],
            'high':max(b['high'] for b in bs),'low':min(b['low'] for b in bs),
            'range':max(b['high'] for b in bs)-min(b['low'] for b in bs)}
prev_day={}; daily_trend={}
for sym in all_data:
    sd=sorted(daily_ohlc[sym].keys()); dc=[]
    for i,d in enumerate(sd):
        c=daily_ohlc[sym][d]['close']; dc.append(c)
        if i>0:
            pdb=date_bars[sd[i-1]].get(sym,[])
            if pdb: prev_day[(d,sym)]={'high':max(b['high'] for b in pdb),'low':min(b['low'] for b in pdb),'close':pdb[-1]['close']}
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_trend[(d,sym)]='UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'
print(f'{len(all_data)} stocks, {len(all_dates)} days\n')

POS=1000000;CHARGES=386;SB=10;T=1.75;S=1.50

def run_test(direction, cd_filter, yd_filter, trail_act, trail_lock, label):
    """Test SHORT (R3) or LONG (S3) with all filters."""
    trades=[]
    yearly=defaultdict(lambda:{'n':0,'w':0,'pnl':0})

    for date in all_dates:
        for sym in date_bars[date]:
            db=date_bars[date][sym]
            if len(db)<=SB+20: continue

            if direction=='SHORT':
                if daily_trend.get((date,sym))!='DOWN': continue
            else:
                if daily_trend.get((date,sym))!='UP': continue

            pd=prev_day.get((date,sym))
            if not pd: continue
            rng=pd['high']-pd['low']
            if rng<=0: continue

            # CD filter
            if cd_filter:
                sd2=sorted(daily_ohlc[sym].keys())
                di=sd2.index(date) if date in sd2 else -1
                if di<7: continue
                cd=0
                for back in range(1,20):
                    if di-back<1: break
                    prev_c=daily_ohlc[sym][sd2[di-back]]['close']
                    prev2_c=daily_ohlc[sym][sd2[di-back-1]]['close']
                    if direction=='SHORT':
                        if prev_c<prev2_c: cd+=1
                        else: break
                    else:  # LONG: count consecutive UP days
                        if prev_c>prev2_c: cd+=1
                        else: break
                if cd>2: continue

                # Yesterday filter
                if yd_filter:
                    prev_d=sd2[di-1]; pc=daily_ohlc[sym][prev_d]
                    yd_range_pct=pc['range']/pc['close']*100 if pc['close']>0 else 0
                    yd_body_ratio=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
                    if yd_range_pct<2.0 or yd_body_ratio<0.2: continue

            if direction=='SHORT':
                # R3 = prev_close + range * 1.1 / 4
                level=pd['close']+rng*1.1/4
            else:
                # S3 = prev_close - range * 1.1 / 4
                level=pd['close']-rng*1.1/4

            # Check if price touched the level in first 50 min
            triggered=False
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if direction=='SHORT':
                    if abs(db[j]['high']-level)<atr*0.3 and db[j]['close']<level:
                        triggered=True; break
                else:
                    if abs(db[j]['low']-level)<atr*0.3 and db[j]['close']>level:
                        triggered=True; break
            if not triggered: continue

            entry=db[SB]['close']
            if direction=='SHORT':
                tp=entry*(1-T/100); sp=entry*(1+S/100)
                lp=entry*(1-trail_lock/100)
            else:
                tp=entry*(1+T/100); sp=entry*(1-S/100)
                lp=entry*(1+trail_lock/100)

            ep=db[min(69,len(db)-1)]['close'];exit_r='eod';mfe=0;ta=False
            for k in range(SB+1,min(len(db),70)):
                if direction=='SHORT':
                    fav=(entry-db[k]['low'])/entry*100
                    mfe=max(mfe,fav)
                    if mfe>=trail_act: ta=True
                    if db[k]['low']<=tp: ep=tp;exit_r='target';break
                    if ta and db[k]['high']>=lp: ep=lp;exit_r='trail';break
                    if not ta and db[k]['high']>=sp: ep=sp;exit_r='stop';break
                else:
                    fav=(db[k]['high']-entry)/entry*100
                    mfe=max(mfe,fav)
                    if mfe>=trail_act: ta=True
                    if db[k]['high']>=tp: ep=tp;exit_r='target';break
                    if ta and db[k]['low']<=lp: ep=lp;exit_r='trail';break
                    if not ta and db[k]['low']<=sp: ep=sp;exit_r='stop';break

            if direction=='SHORT':
                pnl_pct=(entry-ep)/entry*100
            else:
                pnl_pct=(ep-entry)/entry*100
            pnl=pnl_pct/100*POS-CHARGES
            trades.append({'pnl':pnl,'win':pnl>0,'date':date})
            y=date[:4]
            yearly[y]['n']+=1;yearly[y]['pnl']+=pnl
            if pnl>0: yearly[y]['w']+=1

    if not trades: return
    n=len(trades);w=sum(1 for t in trades if t['win']);total=sum(t['pnl'] for t in trades)
    # Walk forward
    train=[t for t in trades if t['date']<'2025-01-01']
    test=[t for t in trades if t['date']>='2025-01-01']
    tr_wr=sum(1 for t in train if t['win'])/len(train)*100 if train else 0
    te_wr=sum(1 for t in test if t['win'])/len(test)*100 if test else 0
    tr_per=sum(t['pnl'] for t in train)/len(train) if train else 0
    te_per=sum(t['pnl'] for t in test)/len(test) if test else 0

    print(f'\n  {label}:')
    print(f'    {n} trades | WR: {w/n*100:.1f}% | Rs {total/n:+,.0f}/trade | Total: Rs {total:+,.0f}')
    print(f'    Walk-forward: Train {tr_wr:.1f}% Rs{tr_per:+,.0f} | Test {te_wr:.1f}% Rs{te_per:+,.0f}')
    print(f'    Yearly: ', end='')
    for y in sorted(yearly):
        m=yearly[y]
        wr=m['w']/m['n']*100
        print(f'{y}={wr:.0f}%({m["n"]}) ', end='')
    print()

# ═══ TEST ═══
print('='*90)
print('SHORT (our proven system) vs LONG (mirror)')
print('='*90)

# SHORT baseline (what we have)
run_test('SHORT', False, False, 999, 0.075, 'SHORT — no filters (baseline)')
run_test('SHORT', True, True, 1.00, 0.075, 'SHORT — CD 0-2 + yesterday + trail (FINAL)')

# LONG mirror — step by step
print(f'\n{"-"*90}')
run_test('LONG', False, False, 999, 0.075, 'LONG — no filters (raw)')
run_test('LONG', True, False, 999, 0.075, 'LONG — CD 0-2 only')
run_test('LONG', True, True, 999, 0.075, 'LONG — CD 0-2 + yesterday filter')
run_test('LONG', True, True, 1.00, 0.075, 'LONG — CD 0-2 + yesterday + trail (FULL)')

# BOTH combined
print(f'\n{"="*90}')
print('COMBINED: SHORT + LONG together')
print('='*90)

# Run both and combine
short_trades=[];long_trades=[]
for date in all_dates:
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=SB+20: continue
        pd=prev_day.get((date,sym))
        if not pd: continue
        rng=pd['high']-pd['low']
        if rng<=0: continue
        sd2=sorted(daily_ohlc[sym].keys())
        di=sd2.index(date) if date in sd2 else -1
        if di<7: continue

        # Yesterday filter
        prev_d=sd2[di-1]; pc=daily_ohlc[sym][prev_d]
        yd_range_pct=pc['range']/pc['close']*100 if pc['close']>0 else 0
        yd_body_ratio=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
        if yd_range_pct<2.0 or yd_body_ratio<0.2: continue

        for direction in ['SHORT','LONG']:
            if direction=='SHORT':
                if daily_trend.get((date,sym))!='DOWN': continue
            else:
                if daily_trend.get((date,sym))!='UP': continue

            cd=0
            for back in range(1,20):
                if di-back<1: break
                p1=daily_ohlc[sym][sd2[di-back]]['close']
                p2=daily_ohlc[sym][sd2[di-back-1]]['close']
                if direction=='SHORT':
                    if p1<p2: cd+=1
                    else: break
                else:
                    if p1>p2: cd+=1
                    else: break
            if cd>2: continue

            if direction=='SHORT':
                level=pd['close']+rng*1.1/4
            else:
                level=pd['close']-rng*1.1/4

            triggered=False
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if direction=='SHORT':
                    if abs(db[j]['high']-level)<atr*0.3 and db[j]['close']<level:
                        triggered=True;break
                else:
                    if abs(db[j]['low']-level)<atr*0.3 and db[j]['close']>level:
                        triggered=True;break
            if not triggered: continue

            entry=db[SB]['close']
            if direction=='SHORT':
                tp=entry*(1-T/100);sp=entry*(1+S/100);lp=entry*(1-0.075/100)
            else:
                tp=entry*(1+T/100);sp=entry*(1-S/100);lp=entry*(1+0.075/100)

            ep=db[min(69,len(db)-1)]['close'];mfe=0;ta=False
            for k in range(SB+1,min(len(db),70)):
                if direction=='SHORT':
                    fav=(entry-db[k]['low'])/entry*100;mfe=max(mfe,fav)
                    if mfe>=1.0: ta=True
                    if db[k]['low']<=tp: ep=tp;break
                    if ta and db[k]['high']>=lp: ep=lp;break
                    if not ta and db[k]['high']>=sp: ep=sp;break
                else:
                    fav=(db[k]['high']-entry)/entry*100;mfe=max(mfe,fav)
                    if mfe>=1.0: ta=True
                    if db[k]['high']>=tp: ep=tp;break
                    if ta and db[k]['low']<=lp: ep=lp;break
                    if not ta and db[k]['low']<=sp: ep=sp;break

            if direction=='SHORT':
                pnl_pct=(entry-ep)/entry*100
            else:
                pnl_pct=(ep-entry)/entry*100
            pnl=pnl_pct/100*POS-CHARGES

            if direction=='SHORT': short_trades.append({'pnl':pnl,'win':pnl>0,'date':date})
            else: long_trades.append({'pnl':pnl,'win':pnl>0,'date':date})

all_trades=short_trades+long_trades
all_trades.sort(key=lambda x:x['date'])
sn=len(short_trades);sw=sum(1 for t in short_trades if t['win'])
ln=len(long_trades);lw=sum(1 for t in long_trades if t['win'])
an=len(all_trades);aw=sum(1 for t in all_trades if t['win'])
st=sum(t['pnl'] for t in short_trades)
lt=sum(t['pnl'] for t in long_trades)
at=sum(t['pnl'] for t in all_trades)

print(f'\n  SHORT: {sn} trades, WR={sw/sn*100:.1f}%, Rs {st/sn:+,.0f}/trade, Total Rs {st:+,.0f}')
if ln>0:
    print(f'  LONG:  {ln} trades, WR={lw/ln*100:.1f}%, Rs {lt/ln:+,.0f}/trade, Total Rs {lt:+,.0f}')
else:
    print(f'  LONG:  0 trades (no signals with full filters)')
print(f'  BOTH:  {an} trades, WR={aw/an*100:.1f}%, Rs {at/an:+,.0f}/trade, Total Rs {at:+,.0f}')
print(f'  Per year: Rs {at/4:+,.0f} (vs Rs {st/4:+,.0f} short-only)')
