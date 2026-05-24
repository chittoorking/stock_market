"""
REALISTIC COMPOUND: 20% of AVAILABLE capital per trade.
Capital returns to pool when trade closes. Bar-by-bar simulation.
"""
import sys, io, csv
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
prev_day_bars={}; daily_ctx={}
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars)); dc=[]
    for i,d in enumerate(dfs):
        db=date_bars[d].get(sym,[])
        if not db: continue
        c=db[-1]['close']; dc.append(c)
        if i>0: prev_day_bars[(d,sym)]=date_bars[dfs[i-1]].get(sym,[])
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_ctx[(d,sym)]={'trend':'UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'}
print('Done.\n', flush=True)

scan_bar=10; TARGET=1.50; STOP=1.00; LEVERAGE=5

for CAPITAL in [500000, 1000000, 2500000]:
    available = float(CAPITAL)
    total_capital = float(CAPITAL)  # Tracks total value (available + in trades)
    max_drawdown = 0; peak_capital = total_capital
    total_trades = 0; total_wins = 0; total_pnl_rs = 0
    yearly = defaultdict(lambda: {'start':0,'end':0,'trades':0,'wins':0,'pnl':0})

    for date in all_dates:
        # Get signals
        signals = []
        for sym in date_bars[date]:
            db=date_bars[date][sym]; ctx=daily_ctx.get((date,sym),{})
            if len(db)<=scan_bar+20 or ctx.get('trend')!='DOWN': continue
            lp=prev_day_bars.get((date,sym),[])
            if not lp: continue
            ph=max(b['high'] for b in lp);pl=min(b['low'] for b in lp);pcc=lp[-1]['close'];rng=ph-pl
            if rng<=0: continue
            r3=pcc+rng*1.1/4
            trigger_bar=0
            for j in range(1,scan_bar+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                    trigger_bar=j; break
            if trigger_bar>0:
                signals.append({'sym':sym,'trigger_bar':trigger_bar})

        if not signals: continue

        y = date[:4]
        if yearly[y]['start']==0: yearly[y]['start']=total_capital

        # Simulate bar by bar with capital pool
        active_trades = []  # (sym, entry, stop, target, margin_used, position_size)

        for bar in range(1, min(max(len(date_bars[date].get(s['sym'],[])) for s in signals), 70)):
            # Open new trades
            for s in signals:
                sym = s['sym']
                db = date_bars[date].get(sym,[])
                if len(db) <= bar or bar < scan_bar: continue
                if s['trigger_bar'] > bar: continue  # Not triggered yet
                if any(a[0]==sym for a in active_trades): continue  # Already in

                # Size: 20% of available capital as margin
                margin = available * 0.20
                if margin < 5000: continue  # Min margin
                pos_size = margin * LEVERAGE
                entry = db[scan_bar]['close']
                stop_p = entry * (1 + STOP/100)
                target_p = entry * (1 - TARGET/100)

                # Charges
                charges = min(20, pos_size*0.05/100) + pos_size*0.025/100 + pos_size*0.00345/100*2 + pos_size*0.003/100 + 1 + (min(20,pos_size*0.05/100)+pos_size*0.00345/100*2)*0.18

                available -= margin
                active_trades.append((sym, entry, stop_p, target_p, margin, pos_size, charges))

            # Check exits
            still_active = []
            for sym, entry, stop_p, target_p, margin, pos_size, charges in active_trades:
                db = date_bars[date].get(sym,[])
                if bar >= len(db):
                    # EOD close
                    ep = db[-1]['close']
                    pnl_rs = (entry-ep)/entry * pos_size - charges
                    available += margin + pnl_rs
                    total_pnl_rs += pnl_rs
                    total_trades += 1
                    if pnl_rs > 0: total_wins += 1
                    yearly[y]['trades']+=1; yearly[y]['pnl']+=pnl_rs
                    if pnl_rs>0: yearly[y]['wins']+=1
                    continue

                # Check target
                if db[bar]['low'] <= target_p:
                    pnl_rs = (entry-target_p)/entry * pos_size - charges
                    available += margin + pnl_rs
                    total_pnl_rs += pnl_rs
                    total_trades += 1; total_wins += 1
                    yearly[y]['trades']+=1; yearly[y]['wins']+=1; yearly[y]['pnl']+=pnl_rs
                    continue

                # Check stop
                if db[bar]['high'] >= stop_p:
                    pnl_rs = (entry-stop_p)/entry * pos_size - charges
                    available += margin + pnl_rs
                    total_pnl_rs += pnl_rs
                    total_trades += 1
                    yearly[y]['trades']+=1; yearly[y]['pnl']+=pnl_rs
                    continue

                still_active.append((sym, entry, stop_p, target_p, margin, pos_size, charges))

            active_trades = still_active

        # EOD: close all remaining
        for sym, entry, stop_p, target_p, margin, pos_size, charges in active_trades:
            db = date_bars[date].get(sym,[])
            ep = db[min(69,len(db)-1)]['close']
            pnl_rs = (entry-ep)/entry * pos_size - charges
            available += margin + pnl_rs
            total_pnl_rs += pnl_rs
            total_trades += 1
            if pnl_rs > 0: total_wins += 1
            yearly[y]['trades']+=1; yearly[y]['pnl']+=pnl_rs
            if pnl_rs>0: yearly[y]['wins']+=1

        total_capital = available  # All cash after EOD
        yearly[y]['end'] = total_capital
        peak_capital = max(peak_capital, total_capital)
        dd = (peak_capital - total_capital) / peak_capital * 100
        max_drawdown = max(max_drawdown, dd)

    # Report
    wr = total_wins/total_trades*100 if total_trades else 0
    ret = (total_capital/CAPITAL-1)*100

    print(f'{"="*70}')
    print(f'Rs {CAPITAL/100000:.0f}L STARTING CAPITAL | 20% of available | 5x leverage')
    print(f'{"="*70}')
    print(f'Trades: {total_trades} | WR: {total_wins}/{total_trades} = {wr:.0f}%')
    print(f'Start: Rs {CAPITAL:>14,.0f}')
    print(f'End:   Rs {total_capital:>14,.0f} ({ret:+,.0f}%)')
    print(f'Max drawdown: {max_drawdown:.1f}%')
    print(f'Total P&L: Rs {total_pnl_rs:>14,.0f}')
    print(f'\nYearly:')
    for y in sorted(yearly):
        m=yearly[y]
        if m['trades']==0: continue
        ywr=m['wins']/m['trades']*100
        yret=(m['end']/m['start']-1)*100 if m['start']>0 else 0
        print(f'  {y}: {m["trades"]} trades, WR={ywr:.0f}%, Rs {m["start"]:>12,.0f} -> Rs {m["end"]:>12,.0f} ({yret:+,.0f}%) P&L=Rs {m["pnl"]:>10,.0f}')
    print()
