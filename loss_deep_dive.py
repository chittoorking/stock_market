"""
Deep dive into every loss category — what happened, which stock, which day,
what was the market doing, any patterns?
"""
import sys, io, csv
if sys.platform=='win32': sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime as dt

data_dir=Path('data/5min'); all_data={}; date_bars=defaultdict(dict)
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
daily_ohlc=defaultdict(dict)
for sym,bars in all_data.items():
    by_d=defaultdict(list)
    for b in bars: by_d[b['timestamp'][:10]].append(b)
    for d,bs in sorted(by_d.items()):
        daily_ohlc[sym][d]={'open':bs[0]['open'],'close':bs[-1]['close'],
            'high':max(b['high'] for b in bs),'low':min(b['low'] for b in bs),
            'volume':sum(b['volume'] for b in bs),'range':max(b['high'] for b in bs)-min(b['low'] for b in bs)}

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

vix_data={}
vf=Path('data/vix_daily.csv')
if vf.exists():
    with open(vf) as f:
        for r in csv.DictReader(f): vix_data[r['date']]=float(r['close'])

POS=1000000;CHARGES=386;SB=10;T=1.75;S=1.50

losses=[]
wins=[]
for date in all_dates:
    # Market breadth
    up_count=0; dn_count=0
    for s2 in date_bars[date]:
        if s2 in ('NIFTY_50','NIFTY_BANK'): continue
        sdb=date_bars[date][s2]
        if len(sdb)>=2:
            if sdb[-1]['close']>sdb[0]['open']: up_count+=1
            else: dn_count+=1
    breadth_down=dn_count/(up_count+dn_count)*100 if up_count+dn_count>0 else 50

    for sym in date_bars[date]:
        if sym in ('NIFTY_50','NIFTY_BANK'): continue
        db=date_bars[date][sym]
        if len(db)<=SB+20: continue
        if daily_trend.get((date,sym))!='DOWN': continue
        pd=prev_day.get((date,sym))
        if not pd: continue
        rng=pd['high']-pd['low']
        if rng<=0: continue
        r3=pd['close']+rng*1.1/4
        triggered=False
        for j in range(1,SB+1):
            atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
            if abs(db[j]['high']-r3)<atr*0.3 and db[j]['close']<r3:
                triggered=True; break
        if not triggered: continue

        sd2=sorted(daily_ohlc[sym].keys())
        di=sd2.index(date) if date in sd2 else -1
        if di<7: continue
        cd=0
        for back in range(1,20):
            if di-back<1: break
            if daily_ohlc[sym][sd2[di-back]]['close']<daily_ohlc[sym][sd2[di-back-1]]['close']:
                cd+=1
            else: break
        if cd>2: continue

        entry=db[SB]['close']
        tp=entry*(1-T/100); sp=entry*(1+S/100)
        ep=db[min(69,len(db)-1)]['close']; exit_r='eod'
        mfe=0; mae=0; mfe_bar=SB; mae_bar=SB
        for k in range(SB+1,min(len(db),70)):
            fav=(entry-db[k]['low'])/entry*100; adv=(db[k]['high']-entry)/entry*100
            if fav>mfe: mfe=fav; mfe_bar=k
            if adv>mae: mae=adv; mae_bar=k
            if db[k]['low']<=tp: ep=tp; exit_r='target'; break
            if db[k]['high']>=sp: ep=sp; exit_r='stop'; break
        pnl=(entry-ep)/entry*100/100*POS-CHARGES

        cat='WIN'
        if pnl<=0:
            if exit_r=='stop': cat='STOP'
            elif mfe>=T*0.5: cat='ALMOST'
            elif mfe<0.3 and mae<0.5: cat='FLAT'
            elif mae>0.5 and mfe<0.3: cat='WRONG_DIR'
            else: cat='CHOPPY'

        dow=dt.strptime(date,'%Y-%m-%d').weekday()
        day_names=['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
        vix=vix_data.get(date,0)

        # What did stock do NEXT day?
        next_di=di+1
        next_move=0
        if next_di<len(sd2):
            nc=daily_ohlc[sym][sd2[next_di]]['close']
            next_move=(nc-daily_ohlc[sym][date]['close'])/daily_ohlc[sym][date]['close']*100

        # Time of worst/best
        mfe_time=f'{9+(mfe_bar*5)//60}:{(mfe_bar*5)%60+15:02d}'
        mae_time=f'{9+(mae_bar*5)//60}:{(mae_bar*5)%60+15:02d}'

        t={'pnl':pnl,'cat':cat,'date':date,'sym':sym,'exit':exit_r,
           'mfe':round(mfe,3),'mae':round(mae,3),'entry':round(entry,2),
           'ep':round(ep,2),'pnl_pct':round((entry-ep)/entry*100,3),
           'day':day_names[dow],'dow':dow,'vix':round(vix,1),'cd':cd,
           'breadth':round(breadth_down,0),'next_move':round(next_move,2),
           'mfe_time':mfe_time,'mae_time':mae_time,'r3':round(r3,2)}

        if pnl>0: wins.append(t)
        else: losses.append(t)

total=len(wins)+len(losses)
print(f'CD 0-2: {total} trades, {len(wins)} wins ({len(wins)/total*100:.0f}%), {len(losses)} losses ({len(losses)/total*100:.0f}%)\n')

# Categorize
cats={
    'STOP': [t for t in losses if t['cat']=='STOP'],
    'ALMOST': [t for t in losses if t['cat']=='ALMOST'],
    'FLAT': [t for t in losses if t['cat']=='FLAT'],
    'WRONG_DIR': [t for t in losses if t['cat']=='WRONG_DIR'],
    'CHOPPY': [t for t in losses if t['cat']=='CHOPPY'],
}

for cat_name, cat_trades in cats.items():
    if not cat_trades: continue

    if cat_name=='STOP':
        title='STOP HIT — Reversed hard past +1.5%'
        desc='Trade went in our favor first, then reversed violently'
    elif cat_name=='ALMOST':
        title='ALMOST WON — Got close to 1.75% target but reversed'
        desc='Stock dropped nicely, almost hit target, then bounced back up'
    elif cat_name=='FLAT':
        title='FLAT — Stock barely moved all day'
        desc='Signal fired but stock had no energy in either direction'
    elif cat_name=='WRONG_DIR':
        title='WRONG DIRECTION — Stock went UP from entry'
        desc='We shorted but stock immediately started going up'
    elif cat_name=='CHOPPY':
        title='CHOPPY — Bounced both ways, ended slightly negative'
        desc='Stock moved up and down, no clear direction, ended small loss'

    print(f'\n{"="*120}')
    print(f'{title}')
    print(f'{desc}')
    print(f'{len(cat_trades)} trades | Avg loss: Rs {sum(t["pnl"] for t in cat_trades)/len(cat_trades):,.0f} | Total: Rs {sum(t["pnl"] for t in cat_trades):,.0f}')
    print(f'{"="*120}')

    # Every trade
    print(f'\n  {"#":>3} {"Date":>10} {"Day":>3} {"Stock":>12} {"Entry":>8} {"Exit":>8} {"PnL%":>7} {"PnL Rs":>9} '
          f'{"MFE%":>5} {"MFEtime":>7} {"MAE%":>5} {"MAEtime":>7} {"VIX":>5} {"Breadth":>7} {"CD":>2} {"NextDay":>7}')
    print(f'  {"-"*130}')

    sorted_trades=sorted(cat_trades, key=lambda x: x['pnl'])
    for i,t in enumerate(sorted_trades,1):
        print(f'  {i:>3} {t["date"]:>10} {t["day"]:>3} {t["sym"]:>12} {t["entry"]:>8.1f} {t["ep"]:>8.1f} '
              f'{t["pnl_pct"]:>+6.3f}% Rs{t["pnl"]:>+8,.0f} '
              f'{t["mfe"]:>5.2f} {t["mfe_time"]:>7} {t["mae"]:>5.2f} {t["mae_time"]:>7} '
              f'{t["vix"]:>5.1f} {t["breadth"]:>5.0f}%dn {t["cd"]:>2}d {t["next_move"]:>+6.2f}%')

    # Summary stats
    print(f'\n  --- Summary ---')

    # By day
    day_counts=Counter(t['day'] for t in cat_trades)
    day_total=Counter(t['day'] for t in wins+losses)
    print(f'  By day: ', end='')
    for d in ['Mon','Tue','Wed','Thu','Fri']:
        cnt=day_counts.get(d,0)
        tot=day_total.get(d,0)
        pct=cnt/tot*100 if tot>0 else 0
        print(f'{d}={cnt}({pct:.0f}%) ', end='')
    print()

    # By stock (top losers)
    stock_counts=Counter(t['sym'] for t in cat_trades)
    top_stocks=stock_counts.most_common(10)
    print(f'  Top stocks: {", ".join(f"{s}({c})" for s,c in top_stocks)}')

    # By year
    year_counts=Counter(t['date'][:4] for t in cat_trades)
    print(f'  By year: {", ".join(f"{y}={c}" for y,c in sorted(year_counts.items()))}')

    # VIX
    avg_vix=sum(t['vix'] for t in cat_trades)/len(cat_trades)
    avg_vix_wins=sum(t['vix'] for t in wins)/len(wins) if wins else 0
    print(f'  Avg VIX: {avg_vix:.1f} (vs {avg_vix_wins:.1f} for wins)')

    # Breadth
    avg_breadth=sum(t['breadth'] for t in cat_trades)/len(cat_trades)
    avg_breadth_wins=sum(t['breadth'] for t in wins)/len(wins)
    print(f'  Avg breadth (% down): {avg_breadth:.0f}% (vs {avg_breadth_wins:.0f}% for wins)')

    # ConsecDown
    cd_counts=Counter(t['cd'] for t in cat_trades)
    print(f'  ConsecDown: {", ".join(f"CD{c}={n}" for c,n in sorted(cd_counts.items()))}')

    # Next day move
    avg_next=sum(t['next_move'] for t in cat_trades)/len(cat_trades)
    print(f'  Avg next day move: {avg_next:+.2f}% (stock continued or reversed?)')

    # Cluster check
    date_counts=Counter(t['date'] for t in cat_trades)
    multi_days=[(d,c) for d,c in date_counts.most_common() if c>=3]
    if multi_days:
        print(f'  Cluster days (3+ losses same day):')
        for d,c in multi_days[:5]:
            syms=[t['sym'] for t in cat_trades if t['date']==d]
            print(f'    {d}: {c} trades — {", ".join(syms)}')

# ═══════════════════════════════════════════════════════════════
print(f'\n{"="*120}')
print(f'LOSS CLUSTERING — Do losses happen together?')
print(f'{"="*120}')

loss_by_date=defaultdict(list)
for t in losses: loss_by_date[t['date']].append(t)

print(f'\n  Distribution:')
all_trade_dates=set(t['date'] for t in wins+losses)
zero_loss_days=len(all_trade_dates)-len(loss_by_date)
print(f'    0 losses: {zero_loss_days} days ({zero_loss_days/len(all_trade_dates)*100:.0f}%) — CLEAN')
for n_loss in range(1,8):
    days_with_n=sum(1 for d,tl in loss_by_date.items() if len(tl)==n_loss)
    if days_with_n>0:
        print(f'    {n_loss} losses: {days_with_n} days')

print(f'\n  Worst 15 days (multiple losses):')
worst=sorted(loss_by_date.items(), key=lambda x: sum(t['pnl'] for t in x[1]))
for date, tl in worst[:15]:
    day_pnl=sum(t['pnl'] for t in tl)
    # Also count wins that day
    day_wins=[t for t in wins if t['date']==date]
    day_win_pnl=sum(t['pnl'] for t in day_wins)
    net=day_pnl+day_win_pnl
    cats_today=Counter(t['cat'] for t in tl)
    cats_str=', '.join(f'{c}={n}' for c,n in cats_today.items())
    dow=dt.strptime(date,'%Y-%m-%d').strftime('%a')
    vix=vix_data.get(date,0)
    print(f'    {date} {dow} VIX={vix:.0f}: {len(tl)}L + {len(day_wins)}W = Rs {net:+,.0f} net | Losses: Rs {day_pnl:+,.0f} | {cats_str}')
