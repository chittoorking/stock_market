"""Full 4-year backtest of bot v3 (SHORT + LONG)."""
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

POS=1000000;CHARGES=386;SB=10;T=1.75;S=1.50;TA=1.00;TL=0.075
print(f'{len(all_data)} stocks, {len(all_dates)} days\n')

shorts=[];longs=[];all_trades=[]
daily_pnl=defaultdict(float)
yearly=defaultdict(lambda:{'sn':0,'sw':0,'sp':0,'ln':0,'lw':0,'lp':0})
monthly=defaultdict(lambda:{'n':0,'w':0,'pnl':0})

for date in all_dates:
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=SB+20: continue
        trend=daily_trend.get((date,sym))
        if trend not in ('DOWN','UP'): continue
        pd=prev_day.get((date,sym))
        if not pd: continue
        rng=pd['high']-pd['low']
        if rng<=0: continue

        if trend=='DOWN':
            # SHORT filters
            sd2=sorted(daily_ohlc[sym].keys())
            di=sd2.index(date) if date in sd2 else -1
            if di<7: continue
            cd=0
            for back in range(1,20):
                if di-back<1: break
                if daily_ohlc[sym][sd2[di-back]]['close']<daily_ohlc[sym][sd2[di-back-1]]['close']: cd+=1
                else: break
            if cd>2: continue
            prev_d=sd2[di-1]; pc=daily_ohlc[sym][prev_d]
            yr=pc['range']/pc['close']*100 if pc['close']>0 else 0
            yb=abs(pc['close']-pc['open'])/pc['range'] if pc['range']>0 else 0.5
            if yr<2.0 or yb<0.2: continue

            level=pd['close']+rng*1.1/4
            triggered=False
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['high']-level)<atr*0.3 and db[j]['close']<level:
                    triggered=True; break
            if not triggered: continue
            direction='SHORT'
        else:
            level=pd['close']-rng*1.1/4
            triggered=False
            for j in range(1,SB+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['low']-level)<atr*0.3 and db[j]['close']>level:
                    triggered=True; break
            if not triggered: continue
            direction='LONG'

        entry=db[SB]['close']
        if direction=='SHORT':
            tp=entry*(1-T/100);sp=entry*(1+S/100);lp=entry*(1-TL/100)
        else:
            tp=entry*(1+T/100);sp=entry*(1-S/100);lp=entry*(1+TL/100)

        ep=db[min(69,len(db)-1)]['close'];exit_r='eod';mfe=0;ta=False
        for k in range(SB+1,min(len(db),70)):
            if direction=='SHORT':
                fav=(entry-db[k]['low'])/entry*100;mfe=max(mfe,fav)
                if mfe>=TA: ta=True
                if db[k]['low']<=tp: ep=tp;exit_r='target';break
                if ta and db[k]['high']>=lp: ep=lp;exit_r='trail';break
                if not ta and db[k]['high']>=sp: ep=sp;exit_r='stop';break
            else:
                fav=(db[k]['high']-entry)/entry*100;mfe=max(mfe,fav)
                if mfe>=TA: ta=True
                if db[k]['high']>=tp: ep=tp;exit_r='target';break
                if ta and db[k]['low']<=lp: ep=lp;exit_r='trail';break
                if not ta and db[k]['low']<=sp: ep=sp;exit_r='stop';break

        if direction=='SHORT': pnl_pct=(entry-ep)/entry*100
        else: pnl_pct=(ep-entry)/entry*100
        pnl=pnl_pct/100*POS-CHARGES
        win=pnl>0

        t={'pnl':pnl,'win':win,'date':date,'sym':sym,'dir':direction,'exit':exit_r,
           'entry':round(entry,2),'ep':round(ep,2),'pnl_pct':round(pnl_pct,3),'mfe':round(mfe,2)}
        all_trades.append(t)
        if direction=='SHORT': shorts.append(t)
        else: longs.append(t)
        daily_pnl[date]+=pnl
        y=date[:4]; m=date[:7]
        if direction=='SHORT':
            yearly[y]['sn']+=1;yearly[y]['sp']+=pnl
            if win: yearly[y]['sw']+=1
        else:
            yearly[y]['ln']+=1;yearly[y]['lp']+=pnl
            if win: yearly[y]['lw']+=1
        monthly[m]['n']+=1;monthly[m]['pnl']+=pnl
        if win: monthly[m]['w']+=1

# Results
n=len(all_trades);w=sum(1 for t in all_trades if t['win'])
total=sum(t['pnl'] for t in all_trades)
sn=len(shorts);sw=sum(1 for t in shorts if t['win']);st=sum(t['pnl'] for t in shorts)
ln=len(longs);lw=sum(1 for t in longs if t['win']);lt=sum(t['pnl'] for t in longs)
daily_vals=[v for v in daily_pnl.values() if v!=0]
cum=0;peak=0;dd=0
for d in sorted(daily_pnl.keys()):
    cum+=daily_pnl[d];peak=max(peak,cum);dd=max(dd,peak-cum)

wins_list=[t for t in all_trades if t['win']]
losses_list=[t for t in all_trades if not t['win']]

print('='*70)
print('FULL BACKTEST — BOT v3 (SHORT + LONG)')
print('='*70)
print(f'''
  COMBINED:
    Trades:       {n}
    Wins:         {w} ({w/n*100:.1f}%)
    Losses:       {n-w} ({(n-w)/n*100:.1f}%)
    Per trade:    Rs {total/n:+,.0f} NET
    Total (4yr):  Rs {total:+,.0f}
    Per year:     Rs {total/4:+,.0f}

  SHORT:          {sn} trades, WR={sw/sn*100:.1f}%, Rs {st/sn:+,.0f}/trade, Total Rs {st:+,.0f}
  LONG:           {ln} trades, WR={lw/ln*100:.1f}%, Rs {lt/ln:+,.0f}/trade, Total Rs {lt:+,.0f}

  Avg win:        Rs {sum(t["pnl"] for t in wins_list)/len(wins_list):+,.0f}
  Avg loss:       Rs {sum(t["pnl"] for t in losses_list)/len(losses_list):+,.0f}
  Win/Loss ratio: {sum(t["pnl"] for t in wins_list)/abs(sum(t["pnl"] for t in losses_list)):.0f}x

  Trades/day:     {n/len(daily_vals):.1f}
  Green days:     {sum(1 for v in daily_vals if v>0)}/{len(daily_vals)} ({sum(1 for v in daily_vals if v>0)/len(daily_vals)*100:.0f}%)
  Avg daily:      Rs {sum(daily_vals)/len(daily_vals):+,.0f}
  Best day:       Rs {max(daily_vals):+,.0f}
  Worst day:      Rs {min(daily_vals):+,.0f}
  Max drawdown:   Rs {dd:,.0f}
  Capital:        Rs 10,00,000
''')

# Yearly
print('  YEARLY:')
print(f'    {"Year":>6} {"SHORT":>15} {"LONG":>15} {"COMBINED":>20}')
for y in sorted(yearly):
    m=yearly[y]
    swr=m['sw']/m['sn']*100 if m['sn'] else 0
    lwr=m['lw']/m['ln']*100 if m['ln'] else 0
    tn=m['sn']+m['ln'];tw=m['sw']+m['lw'];tp=m['sp']+m['lp']
    twr=tw/tn*100 if tn else 0
    print(f'    {y:>6} {m["sn"]:>4}t {swr:.0f}%WR {m["ln"]:>4}t {lwr:.0f}%WR {tn:>5}t {twr:.0f}%WR Rs{tp:>+10,.0f}')

# Monthly
print(f'\n  MONTHLY:')
green=0
for m in sorted(monthly):
    d=monthly[m]; wr=d['w']/d['n']*100
    marker='GREEN' if d['pnl']>0 else 'RED'
    if d['pnl']>0: green+=1
    print(f'    {m}: {d["n"]:>3} trades, WR={wr:.0f}%, Rs {d["pnl"]:>+8,.0f} [{marker}]')
print(f'    Green months: {green}/{len(monthly)} ({green/len(monthly)*100:.0f}%)')

# Walk forward
train=[t for t in all_trades if t['date']<'2025-01-01']
test=[t for t in all_trades if t['date']>='2025-01-01']
tr_w=sum(1 for t in train if t['win']);te_w=sum(1 for t in test if t['win'])
tr_p=sum(t['pnl'] for t in train);te_p=sum(t['pnl'] for t in test)
print(f'\n  WALK-FORWARD:')
print(f'    Train (2022-2024): {len(train)} trades, WR={tr_w/len(train)*100:.1f}%, Rs {tr_p/len(train):+,.0f}/trade')
print(f'    Test  (2025-2026): {len(test)} trades, WR={te_w/len(test)*100:.1f}%, Rs {te_p/len(test):+,.0f}/trade')

# Compound
print(f'\n  COMPOUND:')
for start in [500000, 1000000, 2500000]:
    cap=float(start);pk=cap;mdd=0
    for t in all_trades:
        pos=min(cap*0.20*5, POS)
        scale=pos/POS
        cap+=t['pnl']*scale
        cap=max(cap,10000)
        pk=max(pk,cap);mdd=max(mdd,(pk-cap)/pk*100)
    print(f'    Rs {start/100000:.0f}L -> Rs {cap:>12,.0f} ({(cap/start-1)*100:>+,.0f}%) | Max DD: {mdd:.1f}%')
