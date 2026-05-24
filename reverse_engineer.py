"""
REVERSE ENGINEER: For every day, find THE stock that moved the most.
Then look at what it looked like at 10:15 AM.
Find the PATTERN that separates the best stock from the rest.
"""
import sys, io, csv, json
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
sys.path.insert(0,'.')
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
print('Done.\n', flush=True)

scan_bar=10
winners=[]

for date in all_dates[-200:]:
    best_peak=0; best_info=None
    all_stocks=[]

    up=dn=tot=0
    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=scan_bar: continue
        tot+=1
        mv=(db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100
        if mv>0.15: up+=1
        elif mv<-0.15: dn+=1

    regime='UP' if tot>0 and up/tot>0.6 else 'DOWN' if tot>0 and dn/tot>0.6 else 'CHOPPY'

    for sym in date_bars[date]:
        db=date_bars[date][sym]
        if len(db)<=scan_bar+10: continue
        entry=db[scan_bar]['close']
        morning=(db[scan_bar]['close']-db[0]['open'])/db[0]['open']*100
        b0_body=abs(db[0]['close']-db[0]['open'])/(db[0]['high']-db[0]['low'])*100 if db[0]['high']!=db[0]['low'] else 0
        b0_green=db[0]['close']>db[0]['open']
        vol=sum(b['volume'] for b in db[:scan_bar+1])/(scan_bar+1)
        last4_green=sum(1 for b in db[7:11] if b['close']>b['open'])
        orb_range=(max(b['high'] for b in db[:scan_bar+1])-min(b['low'] for b in db[:scan_bar+1]))/entry*100

        # Peak move after entry (both directions)
        peak_long=max((db[j]['high']-entry)/entry*100 for j in range(scan_bar+1,min(len(db),70)))
        peak_short=max((entry-db[j]['low'])/entry*100 for j in range(scan_bar+1,min(len(db),70)))

        if peak_long>peak_short:
            direction='LONG'; peak=peak_long
        else:
            direction='SHORT'; peak=peak_short

        info={
            'sym':sym,'dir':direction,'peak':round(peak,2),
            'morning':round(morning,2),'b0_body':round(b0_body,0),
            'b0_green':b0_green,'vol':round(vol,0),
            'last4_green':last4_green,'orb_range':round(orb_range,2),
            'morning_aligned':(direction=='LONG' and morning>0) or (direction=='SHORT' and morning<0),
            'abs_morning':round(abs(morning),2),
        }
        all_stocks.append(info)

        if peak>best_peak:
            best_peak=peak; best_info=info

    if best_info and all_stocks:
        best_info['regime']=regime
        best_info['rank_by_morning']=sorted(range(len(all_stocks)),key=lambda i:-abs(all_stocks[i]['morning'])).index(all_stocks.index(best_info))+1 if best_info in all_stocks else 99
        best_info['rank_by_vol']=sorted(range(len(all_stocks)),key=lambda i:-all_stocks[i]['vol']).index(all_stocks.index(best_info))+1 if best_info in all_stocks else 99
        best_info['total_stocks']=len(all_stocks)
        winners.append(best_info)

print(f'REVERSE ENGINEERING: {len(winners)} days analyzed')
print('='*80)

# Distributions
print(f'\n1. DIRECTION:')
l=sum(1 for w in winners if w['dir']=='LONG'); s=len(winners)-l
print(f'   LONG: {l} ({l/len(winners)*100:.0f}%) | SHORT: {s} ({s/len(winners)*100:.0f}%)')

print(f'\n2. MORNING ALIGNMENT (is 10:15 move WITH final direction?):')
al=sum(1 for w in winners if w['morning_aligned'])
print(f'   Aligned: {al}/{len(winners)} ({al/len(winners)*100:.0f}%)')
print(f'   NOT aligned: {len(winners)-al}/{len(winners)} ({(len(winners)-al)/len(winners)*100:.0f}%)')

print(f'\n3. MORNING MOVE SIZE:')
for lo,hi in [(0,0.3),(0.3,0.5),(0.5,1),(1,1.5),(1.5,2),(2,5)]:
    sub=[w for w in winners if lo<=abs(w['morning'])<hi]
    if len(sub)>=3:
        avg_p=sum(w['peak'] for w in sub)/len(sub)
        al_pct=sum(1 for w in sub if w['morning_aligned'])/len(sub)*100
        print(f'   |morning| {lo:.1f}-{hi:.1f}%: {len(sub)} days ({len(sub)/len(winners)*100:.0f}%), peak={avg_p:.1f}%, aligned={al_pct:.0f}%')

print(f'\n4. BAR0 BODY:')
for lo,hi in [(0,20),(20,40),(40,60),(60,80),(80,100)]:
    sub=[w for w in winners if lo<=w['b0_body']<hi]
    if len(sub)>=3:
        avg_p=sum(w['peak'] for w in sub)/len(sub)
        print(f'   body {lo}-{hi}%: {len(sub)} days ({len(sub)/len(winners)*100:.0f}%), avg peak={avg_p:.1f}%')

print(f'\n5. WHERE WAS THE WINNER RANKED BY MORNING MOVE?')
for lo,hi in [(1,3),(3,5),(5,10),(10,20),(20,45)]:
    sub=[w for w in winners if lo<=w['rank_by_morning']<hi]
    if len(sub)>=3:
        print(f'   Rank {lo}-{hi} by |morning|: {len(sub)} days ({len(sub)/len(winners)*100:.0f}%)')

print(f'\n6. WHERE WAS THE WINNER RANKED BY VOLUME?')
for lo,hi in [(1,3),(3,5),(5,10),(10,20),(20,45)]:
    sub=[w for w in winners if lo<=w['rank_by_vol']<hi]
    if len(sub)>=3:
        print(f'   Rank {lo}-{hi} by volume: {len(sub)} days ({len(sub)/len(winners)*100:.0f}%)')

print(f'\n7. MARKET REGIME:')
for r in ['UP','DOWN','CHOPPY']:
    sub=[w for w in winners if w['regime']==r]
    if sub:
        avg_p=sum(w['peak'] for w in sub)/len(sub)
        print(f'   {r}: {len(sub)} days, avg peak={avg_p:.1f}%')

print(f'\n8. ORB RANGE:')
for lo,hi in [(0,0.5),(0.5,1),(1,1.5),(1.5,2),(2,5)]:
    sub=[w for w in winners if lo<=w['orb_range']<hi]
    if len(sub)>=3:
        avg_p=sum(w['peak'] for w in sub)/len(sub)
        print(f'   ORB {lo:.1f}-{hi:.1f}%: {len(sub)} days ({len(sub)/len(winners)*100:.0f}%), avg peak={avg_p:.1f}%')

print(f'\n9. MOST COMMON WINNING STOCKS:')
from collections import Counter
stock_freq=Counter(w['sym'] for w in winners)
for sym,cnt in stock_freq.most_common(15):
    sub=[w for w in winners if w['sym']==sym]
    avg_p=sum(w['peak'] for w in sub)/len(sub)
    dirs=Counter(w['dir'] for w in sub)
    print(f'   {sym:>12}: {cnt} days ({cnt/len(winners)*100:.0f}%), avg peak={avg_p:.1f}%, {dict(dirs)}')

# THE KEY INSIGHT
print(f'\n{"="*80}')
print(f'KEY INSIGHTS:')
print(f'{"="*80}')

with open('data/daily_winners.json','w') as f:
    json.dump(winners, f, indent=2)
print(f'\nSaved to data/daily_winners.json')
