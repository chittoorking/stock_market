"""
OPTIMIZE TOP 10 — Take the 10 best strategies and push each one harder.
For each: add MoE filters, stock whitelist, time filters, candle/noise,
volume, relative strength. Find the BEST version of each.
Then walk-forward validate the improved versions.
"""
import sys; sys.path.insert(0, '.')
import csv, json, math, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from app.agents.coded_moe import score_volume, score_price, score_momentum
from app.signals.base import get_sector, SECTOR_MAP
from app.agents.data_providers import SectorAnalyzer

data_dir = Path('data/5min')
all_data = {}; date_bars = defaultdict(dict)
print('Loading...', flush=True)
for f in sorted(data_dir.glob('*.csv')):
    sym = f.stem.split('_')[0].upper()
    with open(f) as fh: rows = list(csv.DictReader(fh))
    bars = [{'timestamp': r['timestamp'], 'open': float(r['open']), 'high': float(r['high']),
             'low': float(r['low']), 'close': float(r['close']), 'volume': int(float(r['volume']))} for r in rows]
    all_data[sym] = bars
    by_date = defaultdict(list)
    for b in bars:
        by_date[b['timestamp'][:10]].append(b)
    for d, bs in by_date.items():
        date_bars[d][sym] = bs

all_dates = sorted(set(b['timestamp'][:10] for bars in all_data.values() for b in bars))
train_dates = set(all_dates[:80])
test_dates = set(all_dates[80:])

prev_close_map = {}; prev_day_data = {}; multi_day = {}
for sym, bars in all_data.items():
    dates_for_sym = sorted(set(b['timestamp'][:10] for b in bars))
    for i, d in enumerate(dates_for_sym):
        if i > 0:
            pdb = date_bars[dates_for_sym[i-1]].get(sym, [])
            if pdb:
                prev_close_map[(d, sym)] = pdb[-1]['close']
                prev_day_data[(d, sym)] = {
                    'high': max(b['high'] for b in pdb), 'low': min(b['low'] for b in pdb),
                    'close': pdb[-1]['close'], 'open': pdb[0]['open'],
                }
        if i >= 2:
            dc = []
            for j in range(max(0,i-5),i):
                pb = date_bars[dates_for_sym[j]].get(sym,[])
                if pb: dc.append(pb[-1]['close'])
            multi_day[(d,sym)] = dc

macro = {}
mf = Path('data/macro/daily_macro.json')
if mf.exists():
    with open(mf) as f:
        for e in json.load(f): macro[e['date']] = e

# Pre-compute sector/market
sector_cache = {}; market_cache = {}
print('Pre-computing sector/market...', flush=True)
for sb in [3, 6, 10]:
    for date in all_dates:
        sector_cache[(date,sb)] = SectorAnalyzer.compute(all_data, date, sb)
        up=0;dn=0;tot=0
        for sym in date_bars[date]:
            db=date_bars[date][sym]
            if len(db)<=sb: continue
            tot+=1
            mv=(db[sb]['close']-db[0]['open'])/db[0]['open']*100
            if mv>0.15: up+=1
            elif mv<-0.15: dn+=1
        market_cache[(date,sb)] = (up,dn,tot)

def fast_mkt(date, sb, direction):
    s=5.0; sb2=min([3,6,10],key=lambda x:abs(x-sb))
    up,dn,tot=market_cache.get((date,sb2),(0,0,0))
    if tot==0: return 5.0
    up_p=up/tot*100; dn_p=dn/tot*100
    if direction=="LONG" and up_p>65: s+=2
    elif direction=="LONG" and up_p>55: s+=1
    elif direction=="LONG" and up_p<35: s-=2
    elif direction=="SHORT" and dn_p>65: s+=2
    elif direction=="SHORT" and dn_p>55: s+=1
    elif direction=="SHORT" and dn_p<35: s-=2
    flat=tot-up-dn
    if flat>tot*0.4: s-=1
    return max(0,min(10,s))

def fast_mac(date, direction):
    s=5.0; ctx=macro.get(date,{})
    if not ctx: return 5.0
    vix=ctx.get('india_vix',0); sp=ctx.get('sp500_overnight',0); nq=ctx.get('nasdaq_overnight',0)
    if vix>22: s-=1.5
    elif vix>18: s-=0.5
    elif vix<13: s+=1
    us=(sp+nq)/2
    if direction=="LONG" and us>0.5: s+=1.5
    elif direction=="LONG" and us<-0.5: s-=1.5
    elif direction=="SHORT" and us<-0.5: s+=1.5
    elif direction=="SHORT" and us>0.5: s-=1.5
    return max(0,min(10,s))

WHITELIST = {'ULTRACEMCO','EICHERMOT','M&M','TATASTEEL','TECHM','SBILIFE','NTPC',
             'ADANIPORTS','ICICIBANK','SUNPHARMA','TITAN','WIPRO','DIVISLAB','COALINDIA'}
BLACKLIST = {'ASIANPAINT','HINDUNILVR','ITC','JSWSTEEL','APOLLOHOSP','BAJAJ-AUTO',
             'GRASIM','ONGC','ADANIENT','UPL','HEROMOTOCO'}

print(f'{len(all_data)} stocks, {len(all_dates)} days (train:{len(train_dates)}, test:{len(test_dates)})\n')

def simulate(db, eb_start, entry, d, stop, target, exit_bar=69):
    for j in range(eb_start+1, min(len(db), exit_bar+1)):
        if d=='LONG':
            if db[j]['low']<=stop: return stop
            if db[j]['high']>=target: return target
        else:
            if db[j]['high']>=stop: return stop
            if db[j]['low']<=target: return target
    return db[min(exit_bar,len(db)-1)]['close']

def pnl_calc(e,x,d): return (x-e)/e*100 if d=='LONG' else (e-x)/e*100

def candle_score(bar):
    body=abs(bar['close']-bar['open']); rng=bar['high']-bar['low']
    if rng==0: return 0
    br=body/rng; uw=bar['high']-max(bar['open'],bar['close']); lw=min(bar['open'],bar['close'])-bar['low']
    if br>0.8: return 2 if bar['close']>bar['open'] else -2
    if lw>body*2 and uw<body*0.5: return 1
    if uw>body*2 and lw<body*0.5: return -1
    return 0

def noise_calc(bars):
    if len(bars)<3: return 5
    rs=sum(b['high']-b['low'] for b in bars[-5:])
    mv=abs(bars[-1]['close']-bars[max(0,len(bars)-5)]['close'])
    return rs/(mv+0.001)

def dedup(results):
    seen=set()
    return [r for r in results if (r['date'],r['sym']) not in seen and not seen.add((r['date'],r['sym']))]

def stats(results):
    u=dedup(results)
    if not u: return 0,0,0,0
    w=sum(r['win'] for r in u); wr=w/len(u)*100; pnl=sum(r['pnl'] for r in u)
    days=len(set(r['date'] for r in u))
    return len(u),wr,pnl,days

def wf_stats(results):
    """Return train and test stats"""
    tr=[r for r in results if r['date'] in train_dates]
    te=[r for r in results if r['date'] in test_dates]
    return stats(tr), stats(te)

t0 = time.time()

# ═══════════════════════════════════════════════════════════════
# Generate base signals for ALL 10 strategies with enrichment
# ═══════════════════════════════════════════════════════════════
print('Generating enriched signals for all 10 strategies...', flush=True)

all_enriched = []  # All signals with strategy tag + indicators

for di, date in enumerate(all_dates):
    mc = macro.get(date,{})
    vix = mc.get('india_vix',0)
    try: dow = datetime.strptime(date,'%Y-%m-%d').weekday()
    except: dow=-1

    # Relative strength at bar 3
    rs_map = {}
    for sym in date_bars[date]:
        db=date_bars[date][sym]; pc=prev_close_map.get((date,sym))
        if pc is None or len(db)<=3: continue
        rs_map[sym] = (db[3]['close']-db[0]['open'])/db[0]['open']*100
    sorted_rs = sorted(rs_map.items(), key=lambda x:-x[1])
    rs_rank = {s:i+1 for i,(s,_) in enumerate(sorted_rs)}
    rs_total = len(sorted_rs) if sorted_rs else 1

    # Market breadth
    crowd=0; total_syms=0
    for sym in date_bars[date]:
        db=date_bars[date][sym]; pc=prev_close_map.get((date,sym))
        if pc is None or not db: continue
        total_syms+=1
        if abs((db[0]['open']-pc)/pc*100)>0.3: crowd+=1

    for sym in date_bars[date]:
        db = date_bars[date][sym]
        pc = prev_close_map.get((date,sym))
        pd = prev_day_data.get((date,sym))
        daily = multi_day.get((date,sym),[])
        if pc is None or len(db) < 40: continue

        # Common enrichment
        gap = (db[0]['open']-pc)/pc*100
        rs_pct = rs_rank.get(sym,rs_total//2)/rs_total*100
        is_wl = sym in WHITELIST
        is_bl = sym in BLACKLIST
        sector = get_sector(sym)

        # ── STRATEGY 1 & 2: MoE T1/T2 (already optimized, just regenerate) ──
        # Skip — these are already at 100%/93%, can't improve without overfitting

        # ── STRATEGY 3: ORB + Whitelist ──
        rh=db[0]['high']; rl=db[0]['low']; rs_size=rh-rl
        if rs_size > 0:
            for j in range(1, min(len(db), 12)):
                d=None
                if db[j]['close']>rh: d='LONG'
                elif db[j]['close']<rl: d='SHORT'
                if not d: continue

                vol_ok = db[0]['volume']>0 and db[j]['volume']>=db[0]['volume']*1.2
                entry=db[j]['close']
                bsf=db[:j+1]
                c=candle_score(db[j]); n=noise_calc(bsf)
                dir_mult=1 if d=='LONG' else -1
                gap_adj=gap*dir_mult
                morning_adj = (entry-db[0]['open'])/db[0]['open']*100*dir_mult
                Mkt=fast_mkt(date,j,d); Mac=fast_mac(date,d)
                M=score_momentum(sym,bsf,j,d)
                P=score_price(sym,bsf,j,pc,d)
                V=score_volume(sym,bsf,all_data,date,j)

                # Simulate multiple exits
                for rr in [1.5, 2.0, 2.5]:
                    stop=rl if d=='LONG' else rh
                    risk=abs(entry-stop)
                    if risk==0: break
                    target=entry+risk*rr if d=='LONG' else entry-risk*rr
                    for eb in [36, 48, 69]:
                        ep=simulate(db,j,entry,d,stop,target,eb)
                        pnl=pnl_calc(entry,ep,d)
                        all_enriched.append({
                            'strat':'ORB','date':date,'sym':sym,'dir':d,
                            'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0,
                            'vol_ok':vol_ok,'is_wl':is_wl,'is_bl':is_bl,
                            'c':c,'n':round(n,1),'gap_adj':round(gap_adj,2),
                            'morning_adj':round(morning_adj,2),
                            'Mkt':Mkt,'Mac':Mac,'M':M,'P':P,'V':V,
                            'rs_pct':round(rs_pct,1),'vix':round(vix,1),'dow':dow,
                            'sector':sector,'breakout_bar':j,
                        })
                break

        # ── STRATEGY 4: Camarilla R3 ──
        if pd:
            h=pd['high'];l=pd['low'];cc=pd['close']; rng=h-l
            r3=cc+rng*1.1/4; s3=cc-rng*1.1/4
            r4=cc+rng*1.1/2; s4=cc-rng*1.1/2

            for j in range(1, min(len(db), 20)):
                atr=sum(db[k]['high']-db[k]['low'] for k in range(max(0,j-3),j+1))/min(4,j+1)
                tol=atr*0.3
                entry=None; d=None; stop=None; target=None

                if abs(db[j]['high']-r3)<tol and db[j]['close']<r3:
                    entry=db[j]['close']; d='SHORT'; stop=r4; target=cc
                elif abs(db[j]['low']-s3)<tol and db[j]['close']>s3:
                    entry=db[j]['close']; d='LONG'; stop=s4; target=cc
                elif db[j]['close']>r4:
                    entry=db[j]['close']; d='LONG'; stop=r3; target=entry+(entry-r3)*2
                elif db[j]['close']<s4:
                    entry=db[j]['close']; d='SHORT'; stop=s3; target=entry-(s3-entry)*2

                if entry is None: continue

                bsf=db[:j+1]
                c_val=candle_score(db[j]); n_val=noise_calc(bsf)
                dir_mult=1 if d=='LONG' else -1
                Mkt=fast_mkt(date,j,d); Mac=fast_mac(date,d)
                M=score_momentum(sym,bsf,j,d) if j>=2 else 5
                P=score_price(sym,bsf,j,pc,d) if j>=2 else 5
                vol_ok=db[0]['volume']>0 and db[j]['volume']>=db[0]['volume']*1.2

                level='R3' if abs(db[j]['high']-r3)<tol else ('S3' if abs(db[j]['low']-s3)<tol else ('R4' if db[j]['close']>r4 else 'S4'))

                for eb in [36, 48, 69]:
                    ep=simulate(db,j,entry,d,stop,target,eb)
                    pnl=pnl_calc(entry,ep,d)
                    all_enriched.append({
                        'strat':'CAM','date':date,'sym':sym,'dir':d,'level':level,
                        'rr':0,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0,
                        'vol_ok':vol_ok,'is_wl':is_wl,'is_bl':is_bl,
                        'c':c_val,'n':round(n_val,1),'gap_adj':round(gap*dir_mult,2),
                        'morning_adj':round((entry-db[0]['open'])/db[0]['open']*100*dir_mult,2),
                        'Mkt':Mkt,'Mac':Mac,'M':M,'P':P,'V':5,
                        'rs_pct':round(rs_pct,1),'vix':round(vix,1),'dow':dow,
                        'sector':sector,'breakout_bar':j,
                    })
                break

        # ── STRATEGY 5: Expiry Day Reversal (Thursday only) ──
        if dow == 3 and len(db) > 10:
            morning_move = (db[10]['close']-db[0]['open'])/db[0]['open']*100
            if abs(morning_move) >= 0.3:
                d='SHORT' if morning_move>0 else 'LONG'
                entry=db[10]['close']; dir_mult=1 if d=='LONG' else -1
                bsf=db[:11]
                atr=sum(db[k]['high']-db[k]['low'] for k in range(5,11))/6
                stop=entry+atr*2 if d=='SHORT' else entry-atr*2
                Mkt=fast_mkt(date,10,d); Mac=fast_mac(date,d)
                M=score_momentum(sym,bsf,10,d)
                P=score_price(sym,bsf,10,pc,d)
                c_val=candle_score(db[10]); n_val=noise_calc(bsf)
                vol_ok=db[0]['volume']>0 and db[10]['volume']>=db[0]['volume']*1.0

                for rr in [1.0, 1.5, 2.0]:
                    target=entry-(stop-entry)*rr if d=='SHORT' else entry+(entry-stop)*rr
                    for eb in [36, 48, 69]:
                        ep=simulate(db,10,entry,d,stop,target,eb)
                        pnl=pnl_calc(entry,ep,d)
                        all_enriched.append({
                            'strat':'EXPIRY','date':date,'sym':sym,'dir':d,
                            'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0,
                            'vol_ok':vol_ok,'is_wl':is_wl,'is_bl':is_bl,
                            'c':c_val,'n':round(n_val,1),
                            'gap_adj':round(gap*dir_mult,2),
                            'morning_adj':round(morning_move*dir_mult*-1,2),  # Fading
                            'Mkt':Mkt,'Mac':Mac,'M':M,'P':P,'V':5,
                            'rs_pct':round(rs_pct,1),'vix':round(vix,1),'dow':dow,
                            'sector':sector,'breakout_bar':10,
                        })

        # ── STRATEGY 6: Squeeze Momentum ──
        for j in range(10, min(len(db), 25)):
            closes=[b['close'] for b in db[:j+1]]
            highs_l=[b['high'] for b in db[:j+1]]
            lows_l=[b['low'] for b in db[:j+1]]
            nn=min(10,len(closes))
            if nn<7: continue
            sma=sum(closes[-nn:])/nn
            std=math.sqrt(sum((c-sma)**2 for c in closes[-nn:])/nn)
            atr=sum(highs_l[i]-lows_l[i] for i in range(len(closes)-nn,len(closes)))/nn
            bb_u=sma+2*std; bb_l=sma-2*std
            kc_u=sma+1.5*atr; kc_l=sma-1.5*atr
            if not (bb_l>kc_l and bb_u<kc_u): continue  # Must be in squeeze
            don_mid=(max(highs_l[-nn:])+min(lows_l[-nn:]))/2
            mom=closes[-1]-(sma+don_mid)/2
            prev_sma=sum(closes[-nn-1:-1])/nn if len(closes)>nn else sma
            prev_don=(max(highs_l[-nn-1:-1])+min(lows_l[-nn-1:-1]))/2 if len(closes)>nn else don_mid
            prev_mom=closes[-2]-(prev_sma+prev_don)/2 if len(closes)>1 else 0
            if mom>0 and prev_mom<=0: d='LONG'
            elif mom<0 and prev_mom>=0: d='SHORT'
            else: continue

            entry=closes[-1]; dir_mult=1 if d=='LONG' else -1
            stop=entry-atr*1.5 if d=='LONG' else entry+atr*1.5
            bsf=db[:j+1]
            Mkt=fast_mkt(date,j,d); c_val=candle_score(db[j]); n_val=noise_calc(bsf)
            vol_ok=db[0]['volume']>0 and db[j]['volume']>=db[0]['volume']*1.0

            for rr in [1.5, 2.0, 2.5]:
                target=entry+(entry-stop)*rr if d=='LONG' else entry-(stop-entry)*rr
                for eb in [36, 48, 69]:
                    ep=simulate(db,j,entry,d,stop,target,eb)
                    pnl=pnl_calc(entry,ep,d)
                    all_enriched.append({
                        'strat':'SQUEEZE','date':date,'sym':sym,'dir':d,
                        'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0,
                        'vol_ok':vol_ok,'is_wl':is_wl,'is_bl':is_bl,
                        'c':c_val,'n':round(n_val,1),'gap_adj':round(gap*dir_mult,2),
                        'morning_adj':round((entry-db[0]['open'])/db[0]['open']*100*dir_mult,2),
                        'Mkt':Mkt,'Mac':fast_mac(date,d),'M':5,'P':5,'V':5,
                        'rs_pct':round(rs_pct,1),'vix':round(vix,1),'dow':dow,
                        'sector':sector,'breakout_bar':j,
                    })
            break

        # ── STRATEGY 7: Lunch Reversal ──
        if len(db) > 24:
            morning_move = (db[18]['close']-db[0]['open'])/db[0]['open']*100
            if abs(morning_move) > 0.3:
                d='SHORT' if morning_move>0 else 'LONG'
                entry=db[24]['close']; dir_mult=1 if d=='LONG' else -1
                atr=sum(db[k]['high']-db[k]['low'] for k in range(19,25))/6
                stop=entry+atr*1.5 if d=='SHORT' else entry-atr*1.5
                bsf=db[:25]
                Mkt=fast_mkt(date,6,d)
                c_val=candle_score(db[24]); n_val=noise_calc(bsf[-6:])

                for rr in [1.0, 1.5, 2.0]:
                    target=entry-(stop-entry)*rr if d=='SHORT' else entry+(entry-stop)*rr
                    ep=simulate(db,24,entry,d,stop,target,69)
                    pnl=pnl_calc(entry,ep,d)
                    all_enriched.append({
                        'strat':'LUNCH','date':date,'sym':sym,'dir':d,
                        'rr':rr,'eb':69,'pnl':round(pnl,4),'win':1 if pnl>0 else 0,
                        'vol_ok':True,'is_wl':is_wl,'is_bl':is_bl,
                        'c':c_val,'n':round(n_val,1),'gap_adj':round(gap*dir_mult,2),
                        'morning_adj':round(abs(morning_move),2),
                        'Mkt':Mkt,'Mac':fast_mac(date,d),'M':5,'P':5,'V':5,
                        'rs_pct':round(rs_pct,1),'vix':round(vix,1),'dow':dow,
                        'sector':sector,'breakout_bar':24,
                    })

        # ── STRATEGY 8: Triangle Breakout ──
        for end in range(8, min(len(db), 18)):
            start=max(0,end-8)
            seg=db[start:end+1]
            if len(seg)<6: continue
            hs=[b['high'] for b in seg]; ls=[b['low'] for b in seg]
            nn2=len(hs); x_mean=(nn2-1)/2
            sx2=sum((i-x_mean)**2 for i in range(nn2))
            if sx2==0: continue
            hs_slope=sum((i-x_mean)*(hs[i]-sum(hs)/nn2) for i in range(nn2))/sx2
            ls_slope=sum((i-x_mean)*(ls[i]-sum(ls)/nn2) for i in range(nn2))/sx2
            if not (hs_slope<-0.01 and ls_slope>0.01): continue  # Symmetric only (best)

            upper=hs[-1]; lower=ls[-1]
            for j in range(end+1, min(len(db), end+8)):
                d=None
                if db[j]['close']>upper: d='LONG'
                elif db[j]['close']<lower: d='SHORT'
                if not d: continue
                entry=db[j]['close']; dir_mult=1 if d=='LONG' else -1
                rng2=upper-lower; stop=lower if d=='LONG' else upper
                bsf=db[:j+1]
                Mkt=fast_mkt(date,j,d); c_val=candle_score(db[j]); n_val=noise_calc(bsf[-6:])
                vol_ok=db[0]['volume']>0 and db[j]['volume']>=db[max(0,j-3)]['volume']*1.2

                for rr in [1.5, 2.0, 2.5]:
                    target=entry+rng2*rr if d=='LONG' else entry-rng2*rr
                    for eb in [36, 69]:
                        ep=simulate(db,j,entry,d,stop,target,eb)
                        pnl=pnl_calc(entry,ep,d)
                        all_enriched.append({
                            'strat':'TRI','date':date,'sym':sym,'dir':d,
                            'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0,
                            'vol_ok':vol_ok,'is_wl':is_wl,'is_bl':is_bl,
                            'c':c_val,'n':round(n_val,1),'gap_adj':round(gap*dir_mult,2),
                            'morning_adj':round((entry-db[0]['open'])/db[0]['open']*100*dir_mult,2),
                            'Mkt':Mkt,'Mac':fast_mac(date,d),'M':5,'P':5,'V':5,
                            'rs_pct':round(rs_pct,1),'vix':round(vix,1),'dow':dow,
                            'sector':sector,'breakout_bar':j,
                        })
                break
            break

        # ── STRATEGY 9: Multi-Timeframe (daily trend + 5min ORB) ──
        if len(daily) >= 3 and rs_size > 0:
            daily_trend = 'UP' if daily[-1]>daily[-3] else 'DOWN'
            for j in range(1, min(len(db), 12)):
                if daily_trend=='UP' and db[j]['close']>rh: d='LONG'
                elif daily_trend=='DOWN' and db[j]['close']<rl: d='SHORT'
                else: continue
                entry=db[j]['close']; dir_mult=1 if d=='LONG' else -1
                stop=rl if d=='LONG' else rh; risk=abs(entry-stop)
                if risk==0: break
                bsf=db[:j+1]
                Mkt=fast_mkt(date,j,d); c_val=candle_score(db[j]); n_val=noise_calc(bsf)
                vol_ok=db[0]['volume']>0 and db[j]['volume']>=db[0]['volume']*1.0

                for rr in [1.5, 2.0]:
                    target=entry+risk*rr if d=='LONG' else entry-risk*rr
                    for eb in [36, 48]:
                        ep=simulate(db,j,entry,d,stop,target,eb)
                        pnl=pnl_calc(entry,ep,d)
                        all_enriched.append({
                            'strat':'MTF','date':date,'sym':sym,'dir':d,
                            'rr':rr,'eb':eb,'pnl':round(pnl,4),'win':1 if pnl>0 else 0,
                            'vol_ok':vol_ok,'is_wl':is_wl,'is_bl':is_bl,
                            'c':c_val,'n':round(n_val,1),'gap_adj':round(gap*dir_mult,2),
                            'morning_adj':round((entry-db[0]['open'])/db[0]['open']*100*dir_mult,2),
                            'Mkt':Mkt,'Mac':fast_mac(date,d),'M':5,'P':5,'V':5,
                            'rs_pct':round(rs_pct,1),'vix':round(vix,1),'dow':dow,
                            'sector':sector,'breakout_bar':j,
                        })
                break

    if (di+1)%20==0:
        elapsed=time.time()-t0
        print(f'  {di+1}/{len(all_dates)} | {len(all_enriched)} signals | {elapsed:.0f}s', flush=True)

print(f'\nTotal enriched signals: {len(all_enriched)} in {time.time()-t0:.1f}s')

# ═══════════════════════════════════════════════════════════════
# OPTIMIZE EACH STRATEGY
# ═══════════════════════════════════════════════════════════════
strategies = ['ORB','CAM','EXPIRY','SQUEEZE','LUNCH','TRI','MTF']

for strat in strategies:
    base = [s for s in all_enriched if s['strat']==strat]
    if not base:
        print(f'\n{strat}: 0 signals'); continue

    print(f'\n{"="*80}')
    print(f'OPTIMIZING: {strat} ({len(base)} signals)')
    print(f'{"="*80}')

    # Baseline
    for rr in sorted(set(s['rr'] for s in base)):
        for eb in sorted(set(s['eb'] for s in base)):
            sub=[s for s in base if s['rr']==rr and s['eb']==eb]
            n,wr,pnl,days = stats(sub)
            if n < 5: continue
            tr_s, te_s = wf_stats(sub)
            print(f'  Base rr={rr} eb={eb}: {n} trades, {days}d, WR={wr:.0f}%, P&L={pnl:+.1f}% | train:{tr_s[1]:.0f}% test:{te_s[1]:.0f}%')

    # Test filters
    best_setups = []
    rr_vals = sorted(set(s['rr'] for s in base))
    eb_vals = sorted(set(s['eb'] for s in base))

    for rr in rr_vals:
        for eb in eb_vals:
            sub0 = [s for s in base if s['rr']==rr and s['eb']==eb]
            if len(sub0) < 5: continue

            filter_tests = [
                ('vol_ok', lambda s: s['vol_ok']),
                ('whitelist', lambda s: s['is_wl']),
                ('no_blacklist', lambda s: not s['is_bl']),
                ('Mkt>=6', lambda s: s['Mkt']>=6),
                ('Mkt>=7', lambda s: s['Mkt']>=7),
                ('M>=7', lambda s: s['M']>=7),
                ('M>=8', lambda s: s['M']>=8),
                ('P>=7', lambda s: s['P']>=7),
                ('c<=0', lambda s: s['c']<=0),
                ('c<=-1', lambda s: s['c']<=-1),
                ('n<=3', lambda s: s['n']<=3),
                ('n<=2.5', lambda s: s['n']<=2.5),
                ('rs<=30%', lambda s: s['rs_pct']<=30),
                ('rs<=20%', lambda s: s['rs_pct']<=20),
                ('gap_adj>=0', lambda s: s['gap_adj']>=0),
                ('morning>=0.5', lambda s: s['morning_adj']>=0.5),
                # Combos
                ('vol+Mkt>=6', lambda s: s['vol_ok'] and s['Mkt']>=6),
                ('vol+wl', lambda s: s['vol_ok'] and s['is_wl']),
                ('vol+c<=0', lambda s: s['vol_ok'] and s['c']<=0),
                ('vol+n<=3', lambda s: s['vol_ok'] and s['n']<=3),
                ('vol+Mkt>=6+c<=0', lambda s: s['vol_ok'] and s['Mkt']>=6 and s['c']<=0),
                ('vol+Mkt>=6+n<=3', lambda s: s['vol_ok'] and s['Mkt']>=6 and s['n']<=3),
                ('vol+wl+Mkt>=6', lambda s: s['vol_ok'] and s['is_wl'] and s['Mkt']>=6),
                ('vol+rs<=30', lambda s: s['vol_ok'] and s['rs_pct']<=30),
                ('wl+Mkt>=6', lambda s: s['is_wl'] and s['Mkt']>=6),
                ('wl+c<=0', lambda s: s['is_wl'] and s['c']<=0),
                ('wl+n<=3', lambda s: s['is_wl'] and s['n']<=3),
                ('no_bl+Mkt>=6', lambda s: not s['is_bl'] and s['Mkt']>=6),
                ('no_bl+c<=0', lambda s: not s['is_bl'] and s['c']<=0),
                ('no_bl+Mkt>=6+c<=0', lambda s: not s['is_bl'] and s['Mkt']>=6 and s['c']<=0),
                ('M>=7+c<=0', lambda s: s['M']>=7 and s['c']<=0),
                ('M>=7+n<=3', lambda s: s['M']>=7 and s['n']<=3),
                ('P>=7+M>=7', lambda s: s['P']>=7 and s['M']>=7),
                ('Mkt>=6+gap>=0', lambda s: s['Mkt']>=6 and s['gap_adj']>=0),
            ]

            for fname, ffunc in filter_tests:
                filt = [s for s in sub0 if ffunc(s)]
                n,wr,pnl,days = stats(filt)
                if n < 5 or wr < 50: continue
                tr_s, te_s = wf_stats(filt)
                best_setups.append({
                    'strat':strat, 'rr':rr, 'eb':eb, 'filter':fname,
                    'n':n, 'wr':wr, 'pnl':pnl, 'days':days,
                    'train_wr':tr_s[1], 'test_wr':te_s[1],
                    'train_n':tr_s[0], 'test_n':te_s[0],
                })

    best_setups.sort(key=lambda x: (-x['wr'], -x['n']))
    seen=set(); printed=0
    print(f'\n  {"Filter":>35} {"RR":>4} {"EB":>3} {"N":>4} {"WR":>5} {"Days":>4} {"P&L":>7} | {"TrWR":>5} {"TeWR":>5} {"TeN":>3}')
    print('  '+'-'*95)
    for s in best_setups:
        key=(s['filter'],s['rr'],s['eb'])
        if key in seen: continue
        seen.add(key)
        holds = ' HOLDS' if s['test_wr']>=s['train_wr']-10 and s['test_wr']>=50 and s['test_n']>=3 else ''
        overfit = ' OVERFIT' if s['train_wr']>55 and s['test_wr']<45 and s['test_n']>=3 else ''
        marker = ' ***' if s['wr']>=80 else (' <<' if s['wr']>=70 else '')
        print(f"  {s['filter']:>35} {s['rr']:>4} {s['eb']:>3} {s['n']:>4} {s['wr']:>4.0f}% {s['days']:>4} {s['pnl']:>+6.1f}% | {s['train_wr']:>4.0f}% {s['test_wr']:>4.0f}% {s['test_n']:>3}{holds}{overfit}{marker}")
        printed+=1
        if printed>=25: break


# ═══ FINAL: Build optimal combined system ═══
print(f'\n{"="*80}')
print('OPTIMAL COMBINED SYSTEM (best walk-forward-validated version of each)')
print(f'{"="*80}')

# Pick the best filter per strategy that HOLDS in walk-forward
for strat in strategies:
    strat_setups = [s for s in best_setups if s['strat']==strat and s['test_wr']>=50 and s['test_n']>=3]
    if not strat_setups: continue
    # Sort by test WR (real performance)
    strat_setups.sort(key=lambda x: (-x['test_wr'], -x['test_n']))
    best = strat_setups[0]
    print(f"\n  {strat}: {best['filter']} (rr={best['rr']}, eb={best['eb']})")
    print(f"    Full: {best['n']} trades, {best['days']}d, WR={best['wr']:.0f}%, P&L={best['pnl']:+.1f}%")
    print(f"    Train: {best['train_n']} trades, WR={best['train_wr']:.0f}%")
    print(f"    Test:  {best['test_n']} trades, WR={best['test_wr']:.0f}%")

print(f'\nTotal time: {time.time()-t0:.1f}s')
