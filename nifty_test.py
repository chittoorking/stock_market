"""
Test our proven signals (CAM_R3 + ORB) on NIFTY index.
Then simulate what options returns would look like.
NIFTY options: 1% index move = 5-15% option premium move.
"""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

data_dir=Path('data/5min')
# Load NIFTY
nifty_bars=[]; date_bars_n=defaultdict(list)
f=data_dir/'NIFTY_50_5min.csv'
if f.exists():
    with open(f) as fh:
        for r in csv.DictReader(fh):
            b={'timestamp':r['timestamp'],'open':float(r['open']),'high':float(r['high']),
               'low':float(r['low']),'close':float(r['close']),'volume':int(float(r['volume']))}
            nifty_bars.append(b)
            date_bars_n[b['timestamp'][:10]].append(b)

all_dates=sorted(date_bars_n.keys())
print(f'NIFTY: {len(nifty_bars)} bars, {len(all_dates)} days')
print(f'Range: {all_dates[0]} to {all_dates[-1]}')

# Pre-compute prev day
prev_day={}
for i,d in enumerate(all_dates):
    if i>0:
        prev_day[d]={
            'high': max(b['high'] for b in date_bars_n[all_dates[i-1]]),
            'low': min(b['low'] for b in date_bars_n[all_dates[i-1]]),
            'close': date_bars_n[all_dates[i-1]][-1]['close'],
        }

scan_bar=10

# ═══ TEST 1: CAM_R3 on NIFTY ═══
print(f'\n{"="*80}')
print('TEST 1: CAMARILLA R3 on NIFTY (SHORT when price touches R3)')
print('='*80)

cam_results=[]
for date in all_dates:
    db=date_bars_n[date]; pd=prev_day.get(date)
    if not pd or len(db)<=scan_bar+20: continue
    h=pd['high'];l=pd['low'];c=pd['close'];rng=h-l
    if rng<=0: continue
    r3=c+rng*1.1/4; r4=c+rng*1.1/2; s3=c-rng*1.1/4; s4=c-rng*1.1/2

    for j in range(1, scan_bar+1):
        atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
        tol=atr*0.3

        # R3 SHORT
        if abs(db[j]['high']-r3)<tol and db[j]['close']<r3:
            entry=db[j]['close']; stop=r4; target=c
            mfe=0; ep=db[min(69,len(db)-1)]['close']; exit_r='eod'
            for k in range(j+1, min(len(db),70)):
                fav=(entry-db[k]['low'])/entry*100; mfe=max(mfe,fav)
                if db[k]['high']>=stop: ep=stop; exit_r='stop'; break
                if db[k]['low']<=target: ep=target; exit_r='target'; break
            pnl=(entry-ep)/entry*100
            cam_results.append({'date':date,'dir':'SHORT','pnl':round(pnl,4),'mfe':round(mfe,3),'win':pnl>0,'exit':exit_r,'type':'R3'})
            break

        # S3 LONG
        if abs(db[j]['low']-s3)<tol and db[j]['close']>s3:
            entry=db[j]['close']; stop=s4; target=c
            mfe=0; ep=db[min(69,len(db)-1)]['close']; exit_r='eod'
            for k in range(j+1, min(len(db),70)):
                fav=(db[k]['high']-entry)/entry*100; mfe=max(mfe,fav)
                if db[k]['low']<=stop: ep=stop; exit_r='stop'; break
                if db[k]['high']>=target: ep=target; exit_r='target'; break
            pnl=(ep-entry)/entry*100
            cam_results.append({'date':date,'dir':'LONG','pnl':round(pnl,4),'mfe':round(mfe,3),'win':pnl>0,'exit':exit_r,'type':'S3'})
            break

if cam_results:
    w=sum(r['win'] for r in cam_results); n=len(cam_results)
    gross=sum(r['pnl'] for r in cam_results)
    avg_mfe=sum(r['mfe'] for r in cam_results)/n
    # NIFTY charges: futures STT is much lower than equity
    # Options: brokerage Rs 20/lot, STT Rs 50/lot on sell. Total ~Rs 100/lot
    # 1 lot = ~Rs 12L notional. Charges = 100/1200000 = 0.0083%
    nifty_charges = 0.01 * n  # ~0.01% per trade on NIFTY
    net = gross - nifty_charges
    print(f'Trades: {n} | WR: {w}/{n} = {w/n*100:.0f}%')
    print(f'Gross: {gross:+.2f}% | Charges: {nifty_charges:.2f}% | Net: {net:+.2f}%')
    print(f'Avg PnL: {gross/n:+.3f}% | Avg MFE: {avg_mfe:.3f}%')

    # By type
    for t in ['R3','S3']:
        sub=[r for r in cam_results if r['type']==t]
        if sub:
            sw=sum(r['win'] for r in sub); sn=len(sub); sg=sum(r['pnl'] for r in sub)
            print(f'  {t}: {sn} trades, WR={sw/sn*100:.0f}%, gross={sg:+.2f}%')

    # Yearly
    from collections import Counter
    yearly=defaultdict(lambda:{'w':0,'l':0,'pnl':0})
    for r in cam_results:
        y=r['date'][:4]
        if r['win']: yearly[y]['w']+=1
        else: yearly[y]['l']+=1
        yearly[y]['pnl']+=r['pnl']
    print(f'\nYearly:')
    for y in sorted(yearly):
        m=yearly[y]; t2=m['w']+m['l']
        print(f'  {y}: {t2} trades, WR={m["w"]/t2*100:.0f}%, PnL={m["pnl"]:+.2f}%')

# ═══ TEST 2: ORB on NIFTY ═══
print(f'\n{"="*80}')
print('TEST 2: ORB on NIFTY')
print('='*80)

orb_results=[]
for date in all_dates:
    db=date_bars_n[date]
    if len(db)<=scan_bar+20: continue
    rh=db[0]['high']; rl=db[0]['low']; rs=rh-rl
    if rs<=0: continue

    for j in range(1, scan_bar+1):
        d=None
        if db[j]['close']>rh: d='LONG'
        elif db[j]['close']<rl: d='SHORT'
        if not d: continue
        if db[0]['volume']>0 and db[j]['volume']<db[0]['volume']*1.0: break

        entry=db[j]['close']
        stop=rl if d=='LONG' else rh
        risk=abs(entry-stop)
        if risk<=0: break
        target=entry+risk*2.5 if d=='LONG' else entry-risk*2.5

        mfe=0; ep=db[min(69,len(db)-1)]['close']; exit_r='eod'
        for k in range(j+1, min(len(db),70)):
            fav=(db[k]['high']-entry)/entry*100 if d=='LONG' else (entry-db[k]['low'])/entry*100
            mfe=max(mfe,fav)
            if d=='LONG' and db[k]['low']<=stop: ep=stop; exit_r='stop'; break
            if d=='SHORT' and db[k]['high']>=stop: ep=stop; exit_r='stop'; break
            if d=='LONG' and db[k]['high']>=target: ep=target; exit_r='target'; break
            if d=='SHORT' and db[k]['low']<=target: ep=target; exit_r='target'; break

        pnl=(ep-entry)/entry*100 if d=='LONG' else (entry-ep)/entry*100
        orb_results.append({'date':date,'dir':d,'pnl':round(pnl,4),'mfe':round(mfe,3),'win':pnl>0,'exit':exit_r})
        break

if orb_results:
    w=sum(r['win'] for r in orb_results); n=len(orb_results)
    gross=sum(r['pnl'] for r in orb_results)
    avg_mfe=sum(r['mfe'] for r in orb_results)/n
    net=gross-0.01*n
    print(f'Trades: {n} | WR: {w}/{n} = {w/n*100:.0f}%')
    print(f'Gross: {gross:+.2f}% | Net: {net:+.2f}%')
    print(f'Avg PnL: {gross/n:+.3f}% | Avg MFE: {avg_mfe:.3f}%')

    yearly=defaultdict(lambda:{'w':0,'l':0,'pnl':0})
    for r in orb_results:
        y=r['date'][:4]
        if r['win']: yearly[y]['w']+=1
        else: yearly[y]['l']+=1
        yearly[y]['pnl']+=r['pnl']
    print(f'\nYearly:')
    for y in sorted(yearly):
        m=yearly[y]; t2=m['w']+m['l']
        print(f'  {y}: {t2} trades, WR={m["w"]/t2*100:.0f}%, PnL={m["pnl"]:+.2f}%')

# ═══ TEST 3: SIMULATE OPTIONS RETURNS ═══
print(f'\n{"="*80}')
print('TEST 3: OPTIONS SIMULATION (ATM options on NIFTY signals)')
print('='*80)
print('Assumptions:')
print('  ATM option delta = 0.5 (at-the-money)')
print('  NIFTY lot = 25 units, ~Rs 15-20K premium for ATM option')
print('  1% NIFTY move = ~10% option premium move (with theta decay)')
print('  Stop: exit if option drops 25% (= ~0.25% NIFTY against)')
print()

# Combine CAM + ORB on NIFTY, simulate as options
all_nifty = cam_results + orb_results
# Deduplicate by date
seen=set(); unique=[]
for r in sorted(all_nifty, key=lambda x: abs(x['pnl']), reverse=True):
    if r['date'] not in seen: seen.add(r['date']); unique.append(r)

option_mult = 8  # Conservative: 1% NIFTY = 8% option
option_stop = -25  # Stop at -25% option premium

opt_results=[]
for r in unique:
    nifty_pnl = r['pnl']  # % move on NIFTY
    nifty_mfe = r['mfe']

    # Option return = nifty_pnl * multiplier (simplified)
    opt_pnl = nifty_pnl * option_mult
    opt_mfe = nifty_mfe * option_mult

    # Cap loss at stop
    if opt_pnl < option_stop:
        opt_pnl = option_stop

    opt_results.append({
        'date': r['date'], 'nifty_pnl': nifty_pnl, 'opt_pnl': round(opt_pnl, 2),
        'opt_mfe': round(opt_mfe, 2), 'win': opt_pnl > 0,
    })

if opt_results:
    w=sum(r['win'] for r in opt_results); n=len(opt_results)
    gross=sum(r['opt_pnl'] for r in opt_results)
    avg_mfe=sum(r['opt_mfe'] for r in opt_results)/n
    print(f'Trades: {n} | WR: {w}/{n} = {w/n*100:.0f}%')
    print(f'Gross option P&L: {gross:+.1f}%')
    print(f'Avg option P&L: {gross/n:+.2f}%')
    print(f'Avg option MFE: {avg_mfe:.1f}%')

    # Compound with Rs 20K per trade (1 ATM lot)
    capital = 100000  # Total capital
    lot_size = 20000  # Rs 20K per option lot
    bal = capital
    for r in sorted(opt_results, key=lambda x: x['date']):
        trade_return = r['opt_pnl'] / 100 * lot_size  # Rs return
        bal += trade_return
        if bal < lot_size: break  # Bust

    print(f'\nWith Rs 20K/lot (Rs 1L total capital):')
    print(f'  Final: Rs {bal:,.0f} ({(bal/capital-1)*100:+.1f}%)')

    # With progressive sizing (20% of capital per trade)
    bal2 = capital
    for r in sorted(opt_results, key=lambda x: x['date']):
        trade_size = bal2 * 0.20  # 20% of capital
        trade_return = r['opt_pnl'] / 100 * trade_size
        bal2 += trade_return
        if bal2 < 10000: break

    print(f'\nWith 20% of capital per trade (compound):')
    print(f'  Final: Rs {bal2:,.0f} ({(bal2/capital-1)*100:+.1f}%)')

    # Yearly
    yearly=defaultdict(lambda:{'pnl':0,'n':0})
    for r in opt_results:
        y=r['date'][:4]; yearly[y]['pnl']+=r['opt_pnl']; yearly[y]['n']+=1
    print(f'\nYearly option returns:')
    for y in sorted(yearly):
        m=yearly[y]
        print(f'  {y}: {m["n"]} trades, option P&L={m["pnl"]:+.1f}%')
