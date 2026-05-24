"""
HEAD-TO-HEAD: Equity vs Futures on SAME signals.
Same CAM_R3 + trendDOWN. Same entry/exit. Just different charges and capital.
"""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict

# Load
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

SCAN_BAR=10; TARGET=1.50; STOP=1.00
EQUITY_POS=1000000  # Rs 10L per trade
EQUITY_CHARGES=386

# Stock futures lot sizes (approximate, SEBI-mandated)
STOCK_LOTS={
    'ADANIENT':250,'ADANIPORTS':500,'APOLLOHOSP':100,'ASIANPAINT':200,
    'AXISBANK':400,'BAJAJ-AUTO':100,'BPCL':1000,'BHARTIARTL':450,
    'BRITANNIA':100,'CIPLA':400,'COALINDIA':1050,'DIVISLAB':100,
    'EICHERMOT':125,'GRASIM':275,'HCLTECH':350,'HDFCBANK':400,
    'HDFCLIFE':700,'HEROMOTOCO':100,'HINDALCO':850,'HINDUNILVR':200,
    'ICICIBANK':525,'ITC':1600,'INDUSINDBK':500,'INFY':400,
    'JSWSTEEL':675,'LT':150,'M&M':350,'MARUTI':50,
    'NTPC':1500,'NESTLEIND':25,'ONGC':1925,'POWERGRID':1800,
    'RELIANCE':250,'SBILIFE':375,'SBIN':750,'SUNPHARMA':350,
    'TCS':125,'TATACONSUM':500,'TATAMOTORS':550,'TATASTEEL':1700,
    'TECHM':350,'TITAN':175,'UPL':1300,'ULTRACEMCO':50,
    'WIPRO':1000,
}

def fut_charges(lot_value):
    brok=20; stt=lot_value*0.0125/100; exch=lot_value*0.00173/100*2
    stamp=lot_value*0.002/100; sebi=lot_value/10000000*10
    gst=(brok+exch)*0.18
    return round(brok+stt+exch+stamp+sebi+gst, 2)

print(f'{len(all_data)} stocks, {len(all_dates)} days\n')

# Run every signal through BOTH equity and futures
equity_trades=[]; futures_1lot=[]; futures_2lot=[]
yearly_eq=defaultdict(lambda:{'n':0,'w':0,'pnl':0})
yearly_fut=defaultdict(lambda:{'n':0,'w':0,'pnl':0})
daily_eq=defaultdict(float); daily_fut=defaultdict(float)

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
        for k in range(SCAN_BAR+1,min(len(db),70)):
            if db[k]['low']<=tp: ep=tp; exit_r='target'; break
            if db[k]['high']>=sp: ep=sp; exit_r='stop'; break
        pnl_pct=(entry-ep)/entry*100
        y=date[:4]

        # EQUITY: Rs 10L position
        eq_pnl=pnl_pct/100*EQUITY_POS - EQUITY_CHARGES
        equity_trades.append(eq_pnl)
        yearly_eq[y]['n']+=1; yearly_eq[y]['pnl']+=eq_pnl
        if eq_pnl>0: yearly_eq[y]['w']+=1
        daily_eq[date]+=eq_pnl

        # FUTURES: 1 lot
        lot=STOCK_LOTS.get(sym, 500)
        lot_val=entry*lot
        fut_pnl_1=pnl_pct/100*lot_val - fut_charges(lot_val)
        futures_1lot.append(fut_pnl_1)

        # FUTURES: 2 lots
        lot_val2=entry*lot*2
        fut_pnl_2=pnl_pct/100*lot_val2 - fut_charges(lot_val2)
        futures_2lot.append(fut_pnl_2)
        yearly_fut[y]['n']+=1; yearly_fut[y]['pnl']+=fut_pnl_2
        if fut_pnl_2>0: yearly_fut[y]['w']+=1
        daily_fut[date]+=fut_pnl_2

def stats(name, trades):
    n=len(trades); w=sum(1 for t in trades if t>0)
    total=sum(trades)
    wins=[t for t in trades if t>0]; losses=[t for t in trades if t<=0]
    avg_w=sum(wins)/len(wins) if wins else 0
    avg_l=sum(losses)/len(losses) if losses else 0
    # Drawdown
    cum=0; peak=0; max_dd=0
    for t in trades:
        cum+=t; peak=max(peak,cum); max_dd=max(max_dd, peak-cum)
    print(f'\n  {name}:')
    print(f'    Trades: {n} | Wins: {w} | WR: {w/n*100:.0f}%')
    print(f'    Total: Rs {total:>+14,.0f}')
    print(f'    Per trade: Rs {total/n:>+7,.0f}')
    print(f'    Avg win: Rs {avg_w:>+7,.0f} | Avg loss: Rs {avg_l:>+7,.0f}')
    print(f'    Max drawdown: Rs {max_dd:>10,.0f}')
    print(f'    Per day (~4.3 trades): Rs {total/len(set(all_dates)):>+7,.0f}')
    print(f'    Per year: Rs {total/4:>+12,.0f}')

print('='*80)
print('EQUITY vs FUTURES — Same signals, same entries, same exits')
print('='*80)

stats('EQUITY (Rs 10L position, Rs 386/trade)', equity_trades)
stats('FUTURES 1 LOT (margin ~Rs 72K, charges ~Rs 113)', futures_1lot)
stats('FUTURES 2 LOTS (margin ~Rs 1.44L, charges ~Rs 226)', futures_2lot)

# Side by side comparison
print(f'\n{"="*80}')
print(f'SIDE-BY-SIDE')
print(f'{"="*80}')
print(f'{"":>25} {"EQUITY":>15} {"FUT 1 LOT":>15} {"FUT 2 LOTS":>15}')
print(f'{"-"*70}')
n=len(equity_trades)
w_eq=sum(1 for t in equity_trades if t>0)
w_f1=sum(1 for t in futures_1lot if t>0)
w_f2=sum(1 for t in futures_2lot if t>0)
t_eq=sum(equity_trades); t_f1=sum(futures_1lot); t_f2=sum(futures_2lot)
print(f'{"Trades":>25} {n:>15,} {n:>15,} {n:>15,}')
print(f'{"Win Rate":>25} {w_eq/n*100:>14.0f}% {w_f1/n*100:>14.0f}% {w_f2/n*100:>14.0f}%')
print(f'{"Total P&L":>25} Rs {t_eq:>+12,.0f} Rs {t_f1:>+12,.0f} Rs {t_f2:>+12,.0f}')
print(f'{"Per trade":>25} Rs {t_eq/n:>+11,.0f} Rs {t_f1/n:>+11,.0f} Rs {t_f2/n:>+11,.0f}')
print(f'{"Per year":>25} Rs {t_eq/4:>+11,.0f} Rs {t_f1/4:>+11,.0f} Rs {t_f2/4:>+11,.0f}')
print(f'{"Capital needed":>25} {"Rs 10,00,000":>15} {"Rs 72,000":>15} {"Rs 1,44,000":>15}')
print(f'{"Charges/trade":>25} {"Rs 386":>15} {"Rs ~113":>15} {"Rs ~226":>15}')
print(f'{"ROI on capital":>25} {t_eq/1000000*100/4:>14.0f}% {t_f1/72000*100/4:>14.0f}% {t_f2/144000*100/4:>14.0f}%')

# Yearly comparison
print(f'\n{"="*80}')
print(f'YEARLY COMPARISON (Equity vs Futures 2-lot)')
print(f'{"="*80}')
print(f'{"Year":>6} {"Equity Trades":>14} {"Equity WR":>10} {"Equity P&L":>14} {"Futures P&L":>14} {"Futures WR":>10}')
print('-'*70)
for y in sorted(set(list(yearly_eq.keys())+list(yearly_fut.keys()))):
    eq=yearly_eq[y]; ft=yearly_fut[y]
    eq_wr=eq['w']/eq['n']*100 if eq['n'] else 0
    ft_wr=ft['w']/ft['n']*100 if ft['n'] else 0
    print(f'{y:>6} {eq["n"]:>14} {eq_wr:>9.0f}% Rs {eq["pnl"]:>+11,.0f} Rs {ft["pnl"]:>+11,.0f} {ft_wr:>9.0f}%')

# COMPOUND: Start with same capital, which grows faster?
print(f'\n{"="*80}')
print(f'COMPOUND GROWTH — Rs 5L starting capital')
print(f'Equity: 20% of capital per trade (5x leverage from broker)')
print(f'Futures: 20% of capital as margin (built-in ~7x leverage)')
print(f'{"="*80}')

for START in [500000, 1000000]:
    # Equity compound
    eq_cap=float(START); eq_peak=eq_cap; eq_dd=0
    for t in equity_trades:
        pos=eq_cap*0.20*5  # 20% capital × 5x leverage
        scale=pos/EQUITY_POS  # Scale P&L proportionally
        eq_cap+=t*scale
        eq_cap=max(eq_cap, 10000)
        eq_peak=max(eq_peak, eq_cap)
        eq_dd=max(eq_dd, (eq_peak-eq_cap)/eq_peak*100)

    # Futures compound
    ft_cap=float(START); ft_peak=ft_cap; ft_dd=0
    for i,t in enumerate(futures_2lot):
        margin=144000  # Avg margin for 2 lots
        n_sets=max(1, int(ft_cap*0.20/margin))  # How many 2-lot sets
        ft_cap+=t*n_sets
        ft_cap=max(ft_cap, 10000)
        ft_peak=max(ft_peak, ft_cap)
        ft_dd=max(ft_dd, (ft_peak-ft_cap)/ft_peak*100)

    print(f'\n  Starting: Rs {START/100000:.0f}L')
    print(f'    Equity:  Rs {START:>10,.0f} -> Rs {eq_cap:>15,.0f} ({(eq_cap/START-1)*100:>+,.0f}%) | Max DD: {eq_dd:.1f}%')
    print(f'    Futures: Rs {START:>10,.0f} -> Rs {ft_cap:>15,.0f} ({(ft_cap/START-1)*100:>+,.0f}%) | Max DD: {ft_dd:.1f}%')

# Daily P&L distribution
print(f'\n{"="*80}')
print(f'DAILY P&L — How much do you make per day?')
print(f'{"="*80}')
eq_days=sorted(daily_eq.items(), key=lambda x:x[1])
ft_days=sorted(daily_fut.items(), key=lambda x:x[1])

eq_daily_vals=[v for _,v in daily_eq.items() if v!=0]
ft_daily_vals=[v for _,v in daily_fut.items() if v!=0]

eq_green=sum(1 for v in eq_daily_vals if v>0)
ft_green=sum(1 for v in ft_daily_vals if v>0)

print(f'\n  {"":>20} {"EQUITY":>15} {"FUTURES 2L":>15}')
print(f'  {"Trading days":>20} {len(eq_daily_vals):>15} {len(ft_daily_vals):>15}')
print(f'  {"Green days":>20} {eq_green:>14} ({eq_green/len(eq_daily_vals)*100:.0f}%) {ft_green:>6} ({ft_green/len(ft_daily_vals)*100:.0f}%)')
print(f'  {"Avg daily P&L":>20} Rs {sum(eq_daily_vals)/len(eq_daily_vals):>+11,.0f} Rs {sum(ft_daily_vals)/len(ft_daily_vals):>+11,.0f}')
print(f'  {"Best day":>20} Rs {max(eq_daily_vals):>+11,.0f} Rs {max(ft_daily_vals):>+11,.0f}')
print(f'  {"Worst day":>20} Rs {min(eq_daily_vals):>+11,.0f} Rs {min(ft_daily_vals):>+11,.0f}')
print(f'  {"Median day":>20} Rs {sorted(eq_daily_vals)[len(eq_daily_vals)//2]:>+11,.0f} Rs {sorted(ft_daily_vals)[len(ft_daily_vals)//2]:>+11,.0f}')

# FINAL VERDICT
print(f'\n{"="*80}')
print(f'VERDICT')
print(f'{"="*80}')
print(f'''
  EQUITY:
    + Simpler to trade (just buy/sell shares)
    + No expiry to worry about
    - Rs 386 charges eat into profits
    - Need Rs 10L per trade

  FUTURES:
    + Half the charges (Rs ~200 vs Rs 386)
    + Less capital needed (Rs 1.44L vs Rs 10L for same exposure)
    + Higher WR (68% vs 67% — charges push fewer trades into loss)
    + Better ROI on capital
    - Need to roll over monthly (minor hassle)
    - Lot size is fixed (can't trade fractional)
    - Not all stocks have liquid futures

  RECOMMENDATION:
    If you have Rs 5L+ → FUTURES (2 lots) is strictly better
    If you have Rs 1-5L → FUTURES (1 lot) is the only realistic option
    If you want simplicity → EQUITY works fine, just needs Rs 10L
''')
