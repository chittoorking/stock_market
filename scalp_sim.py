"""
SCALP SIMULATION: Trade ALL high-probability signals each day.
Target: +0.15% per trade (just above breakeven). Exit immediately when hit.
Rs 10L capital, Rs 50K per trade. Up to 20 trades/day.
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

all_dates=sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))

prev_close={}; daily_ctx={}; prev_day_bars={}
for sym,bars in all_data.items():
    dfs=sorted(set(b['timestamp'][:10] for b in bars)); dc=[]
    for i,d in enumerate(dfs):
        db=date_bars[d].get(sym,[])
        if not db: continue
        c=db[-1]['close']; dc.append(c)
        if i>0:
            prev_close[(d,sym)]=date_bars[dfs[i-1]].get(sym,[])[-1]['close'] if date_bars[dfs[i-1]].get(sym) else None
            prev_day_bars[(d,sym)]=date_bars[dfs[i-1]].get(sym,[])
        if len(dc)>=5:
            up=sum(1 for j in range(1,len(dc[-5:])) if dc[-5:][j]>dc[-5:][j-1])
            daily_ctx[(d,sym)]={'trend':'UP' if up>=4 else 'DOWN' if up<=1 else 'SIDE'}

print('Done.\n', flush=True)

scan_bar = 10
SCALP_TARGET = 0.15  # Target +0.15% (after this, immediately exit)
SCALP_STOP = -0.30   # Stop at -0.30%

# Different capital/sizing scenarios
for CAPITAL, PER_TRADE, MAX_TRADES, label in [
    (100000, 100000, 1, 'Rs 1L, 1 trade/day'),
    (500000, 50000, 10, 'Rs 5L, Rs 50K/trade, max 10'),
    (1000000, 50000, 20, 'Rs 10L, Rs 50K/trade, max 20'),
    (1000000, 100000, 10, 'Rs 10L, Rs 1L/trade, max 10'),
]:
    # Charges per trade at this size
    brokerage = 40
    stt = PER_TRADE * 0.025 / 100
    exchange = PER_TRADE * 0.00345 / 100 * 2
    gst = (brokerage + exchange) * 0.18
    stamp = PER_TRADE * 0.003 / 100
    charges_rs = brokerage + stt + exchange + gst + stamp
    charges_pct = charges_rs / PER_TRADE * 100

    bal = CAPITAL
    total_trades = 0; total_wins = 0; total_pnl_rs = 0
    daily_log = []

    for date in all_dates[-200:]:
        # Market regime
        up=dn=tot=0
        for sym in date_bars[date]:
            db=date_bars[date][sym]
            if len(db)<=scan_bar: continue
            tot+=1; mv=(db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100
            if mv>0.15: up+=1
            elif mv<-0.15: dn+=1
        if tot==0: continue
        regime='UP' if up/tot>0.6 else 'DOWN' if dn/tot>0.6 else 'CHOPPY'

        day_trades = 0; day_pnl_rs = 0

        for sym in sorted(date_bars[date].keys()):
            if day_trades >= MAX_TRADES: break
            db=date_bars[date][sym]; pc=prev_close.get((date,sym))
            ctx=daily_ctx.get((date,sym),{})
            if pc is None or len(db)<=scan_bar+20: continue
            trend=ctx.get('trend','?')

            # Check CAM_R3
            lp=prev_day_bars.get((date,sym),[])
            if not lp: continue
            ph=max(b['high'] for b in lp);pl=min(b['low'] for b in lp);pcc=lp[-1]['close'];rng=ph-pl
            if rng<=0: continue
            r3=pcc+rng*1.1/4

            cam_r3=False
            for j in range(1,scan_bar+1):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                    cam_r3=True; break

            if not cam_r3: continue

            # Apply HIGH CONFIDENCE filter:
            # CAM_R3 SHORT + trend DOWN = 96% WR
            morning=(db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100
            if not (trend=='DOWN'): continue  # Only when daily trend confirms

            # SCALP: enter SHORT, target +0.15%, stop -0.30%
            entry=db[scan_bar]['close']
            target_price = entry * (1 - SCALP_TARGET/100)
            stop_price = entry * (1 + abs(SCALP_STOP)/100)

            exit_price = db[min(69,len(db)-1)]['close']  # Default: EOD
            exit_reason = 'eod'
            for k in range(scan_bar+1, min(len(db),70)):
                # Target hit?
                if db[k]['low'] <= target_price:
                    exit_price = target_price; exit_reason = 'target'; break
                # Stop hit?
                if db[k]['high'] >= stop_price:
                    exit_price = stop_price; exit_reason = 'stop'; break

            pnl_pct = (entry - exit_price) / entry * 100  # SHORT
            pnl_rs = pnl_pct / 100 * PER_TRADE - charges_rs
            win = pnl_rs > 0

            day_trades += 1; day_pnl_rs += pnl_rs
            total_trades += 1; total_pnl_rs += pnl_rs
            if win: total_wins += 1

        bal += day_pnl_rs
        if day_trades > 0:
            daily_log.append({'date':date,'trades':day_trades,'pnl_rs':round(day_pnl_rs),'bal':round(bal)})

    # Report
    wr = total_wins/total_trades*100 if total_trades else 0
    trading_days = len(daily_log)
    green_days = sum(1 for d in daily_log if d['pnl_rs']>0)

    print(f'\n{"="*80}')
    print(f'{label}')
    print(f'{"="*80}')
    print(f'Trades: {total_trades} over {trading_days} days ({total_trades/max(trading_days,1):.1f}/day)')
    print(f'WR: {total_wins}/{total_trades} = {wr:.0f}%')
    print(f'Total P&L: Rs {total_pnl_rs:+,.0f}')
    print(f'Capital: Rs {CAPITAL:,} -> Rs {bal:,.0f} ({(bal/CAPITAL-1)*100:+.1f}%)')
    print(f'Charges per trade: Rs {charges_rs:.0f} ({charges_pct:.3f}%)')
    print(f'Green days: {green_days}/{trading_days} ({green_days/max(trading_days,1)*100:.0f}%)')
    if daily_log:
        avg_daily = total_pnl_rs / trading_days
        print(f'Avg daily P&L: Rs {avg_daily:+,.0f}')

    # Show sample days
    if daily_log:
        print(f'\nSample days:')
        for d in daily_log[:5]+daily_log[-5:]:
            print(f'  {d["date"]}: {d["trades"]} trades, Rs {d["pnl_rs"]:+,}, bal=Rs {d["bal"]:,}')

# Also test: what if we do ALL qualifying signals (CAM_R3 + ORB + Momentum, trending days only)?
print(f'\n\n{"="*80}')
print('FULL SCALP: CAM_R3 + ORB + Momentum on trending days')
print('Rs 10L capital, Rs 50K/trade, no limit')
print(f'{"="*80}')

PER_TRADE=50000; CAPITAL=1000000
charges_rs = 40 + PER_TRADE*0.025/100 + PER_TRADE*0.00345/100*2 + (40+PER_TRADE*0.00345/100*2)*0.18 + PER_TRADE*0.003/100
bal=CAPITAL; total_trades=0; total_wins=0; total_pnl_rs=0; daily_log=[]

for date in all_dates[-200:]:
    up=dn=tot=0
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=scan_bar: continue
        tot+=1; mv=(db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100
        if mv>0.15: up+=1
        elif mv<-0.15: dn+=1
    if tot==0: continue
    up_pct=up/tot*100; dn_pct=dn/tot*100
    is_trending = up_pct>60 or dn_pct>60
    if not is_trending: continue  # ONLY trending days

    regime='UP' if up_pct>60 else 'DOWN'
    day_trades=0; day_pnl_rs=0

    for sym in sorted(date_bars[date].keys()):
        db=date_bars[date][sym]; ctx=daily_ctx.get((date,sym),{})
        if len(db)<=scan_bar+20: continue
        trend=ctx.get('trend','?')
        morning=(db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100
        entry=db[scan_bar]['close']

        take_short = False; take_long = False

        # CAM_R3 SHORT (96% WR when trend DOWN)
        lp=prev_day_bars.get((date,sym),[])
        if lp and trend=='DOWN':
            ph=max(b['high'] for b in lp);pl=min(b['low'] for b in lp);pcc=lp[-1]['close'];rng=ph-pl
            if rng>0:
                r3=pcc+rng*1.1/4
                for j in range(1,scan_bar+1):
                    atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                    if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3: take_short=True; break

        # ORB LONG (94% WR when trend UP)
        if trend=='UP' and regime=='UP':
            rh=db[0]['high']; rl=db[0]['low']
            if rh>rl and any(db[j]['close']>rh for j in range(1,scan_bar+1)):
                take_long=True

        # ORB SHORT (94% WR when trend DOWN)
        if trend=='DOWN' and regime=='DOWN':
            rh=db[0]['high']; rl=db[0]['low']
            if rh>rl and any(db[j]['close']<rl for j in range(1,scan_bar+1)):
                take_short=True

        # Momentum SHORT (95% when trend DOWN)
        if trend=='DOWN' and morning<-0.3:
            consec_red=0
            for k in range(scan_bar,max(scan_bar-5,0),-1):
                if db[k]['close']<db[k]['open']: consec_red+=1
                else: break
            if consec_red>=3: take_short=True

        for direction in (['SHORT'] if take_short else []) + (['LONG'] if take_long else []):
            target_price = entry*(1-SCALP_TARGET/100) if direction=='SHORT' else entry*(1+SCALP_TARGET/100)
            stop_price = entry*(1+abs(SCALP_STOP)/100) if direction=='SHORT' else entry*(1-abs(SCALP_STOP)/100)

            exit_price=db[min(69,len(db)-1)]['close']; exit_reason='eod'
            for k in range(scan_bar+1,min(len(db),70)):
                if direction=='SHORT':
                    if db[k]['low']<=target_price: exit_price=target_price; exit_reason='target'; break
                    if db[k]['high']>=stop_price: exit_price=stop_price; exit_reason='stop'; break
                else:
                    if db[k]['high']>=target_price: exit_price=target_price; exit_reason='target'; break
                    if db[k]['low']<=stop_price: exit_price=stop_price; exit_reason='stop'; break

            pnl_pct=(entry-exit_price)/entry*100 if direction=='SHORT' else (exit_price-entry)/entry*100
            pnl_rs=pnl_pct/100*PER_TRADE - charges_rs
            day_trades+=1; day_pnl_rs+=pnl_rs; total_trades+=1; total_pnl_rs+=pnl_rs
            if pnl_rs>0: total_wins+=1
            break  # 1 trade per stock

    bal+=day_pnl_rs
    if day_trades>0:
        daily_log.append({'date':date,'trades':day_trades,'pnl_rs':round(day_pnl_rs),'bal':round(bal)})

wr=total_wins/total_trades*100 if total_trades else 0
trading_days=len(daily_log)
green_days=sum(1 for d in daily_log if d['pnl_rs']>0)
print(f'Trading days: {trading_days}/200 (trending only)')
print(f'Trades: {total_trades} ({total_trades/max(trading_days,1):.1f}/day)')
print(f'WR: {total_wins}/{total_trades} = {wr:.0f}%')
print(f'Total P&L: Rs {total_pnl_rs:+,.0f}')
print(f'Capital: Rs {CAPITAL:,} -> Rs {bal:,.0f} ({(bal/CAPITAL-1)*100:+.1f}%)')
print(f'Green days: {green_days}/{trading_days} ({green_days/max(trading_days,1)*100:.0f}%)')
if trading_days:
    print(f'Avg daily: Rs {total_pnl_rs/trading_days:+,.0f}')
    print(f'Annualized: {(bal/CAPITAL-1)*100/200*365:+.0f}%')
