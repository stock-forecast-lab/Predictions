"""SEC companyfacts + accession-level submission times, preserving as-filed facts."""
from collections import defaultdict
from datetime import datetime,timezone
import hashlib,json,os,re,math
from pathlib import Path
import pandas as pd
from .config import SEC_TAGS,DURATION_METRICS
from .http import Client,FetchError
from .storage import save_filing,digest,write_json_once

FORMS={'10-K','10-K/A','10-Q','10-Q/A','20-F','20-F/A','40-F','40-F/A'}

def submission_times(body):
    recent=body.get('filings',{}).get('recent',body)
    accessions=recent.get('accessionNumber',[])
    stamps=recent.get('acceptanceDateTime',[])
    return {a:stamps[i] for i,a in enumerate(accessions) if i<len(stamps) and stamps[i]}

def extract_facts(body,tickers,times,start='2018-01-01'):
    grouped=defaultdict(list)
    for taxonomy in ['us-gaap','ifrs-full','dei']:
        concepts=body.get('facts',{}).get(taxonomy,{})
        for metric,tags in SEC_TAGS.items():
            for priority,tag in enumerate(tags):
                if tag not in concepts: continue
                for unit,rows in concepts[tag].get('units',{}).items():
                    wanted='shares' if metric=='shares' else 'USD/shares' if metric=='eps' else 'USD'
                    if unit!=wanted: continue
                    for row in rows:
                        if row.get('form') not in FORMS or row.get('filed','')<start: continue
                        end=row.get('end');begin=row.get('start','')
                        if not end or not row.get('accn'): continue
                        if metric in DURATION_METRICS:
                            if not begin: continue
                            days=(pd.Timestamp(end)-pd.Timestamp(begin)).days+1
                            # Quarter/YTD/annual periods retain exact start/end dates.
                            # Feature construction explicitly annualizes the
                            # longest reported span ending at the latest period.
                            if not 60<=days<=400: continue
                        elif begin: continue
                        value=row.get('val')
                        if not isinstance(value,(int,float)) or not math.isfinite(value): continue
                        stamp=times.get(row['accn'])
                        if stamp:
                            published=pd.Timestamp(stamp)
                            if published.tzinfo is None: published=published.tz_localize('UTC')
                            available=published+pd.Timedelta(days=1)
                            basis='SEC_acceptance_plus_1_calendar_day'
                        else:
                            published=pd.Timestamp(row['filed'],tz='UTC')
                            available=published+pd.Timedelta(days=2)
                            basis='SEC_filed_date_plus_2_calendar_days'
                        if pd.Timestamp(end)>published.tz_convert(None).normalize(): continue
                        for ticker in tickers:
                            grouped[row['accn']].append(dict(ticker=ticker,metric=metric,
                                start=begin,end=end,value=float(value),unit=unit,scope='consolidated',
                                tag=f'{taxonomy}:{tag}',priority=priority,
                                filing_id=row['accn'],published_at_utc=published.isoformat(),
                                available_at_utc=available.isoformat(),availability_basis=basis,
                                provider='SEC',valuation_allowed=len(tickers)==1))
    output={}
    for accession,rows in grouped.items():
        chosen={}
        for row in sorted(rows,key=lambda r:(r['priority'],r['tag'])):
            key=tuple(row[k] for k in ['ticker','metric','start','end','unit','scope'])
            if key in chosen and chosen[key]['priority']==row['priority'] and chosen[key]['value']!=row['value']:
                raise RuntimeError(f'Ambiguous SEC fact {accession}: {row["metric"]}')
            chosen.setdefault(key,row)
        output[accession]=list(chosen.values())
    return output

def synchronize(root,tickers,start,client=None):
    user_agent=os.getenv('SEC_USER_AGENT','').strip()
    if client is None and ('@' not in user_agent or '\n' in user_agent):
        raise RuntimeError('Set SEC_USER_AGENT to your application name and real contact email in Actions Secrets')
    client=client or Client({'User-Agent':user_agent,'Accept':'application/json'},interval=.2)
    listing=client.get('https://www.sec.gov/files/company_tickers.json').json()
    entities=defaultdict(list)
    all_classes=defaultdict(set)
    requested=set(tickers)
    for item in listing.values():
        ticker=item['ticker'].replace('.','-')
        all_classes[str(item['cik_str']).zfill(10)].add(ticker)
        if ticker in requested: entities[str(item['cik_str']).zfill(10)].append(ticker)
    mapped={t for group in entities.values() for t in group}
    missing=sorted(requested-mapped)
    print(f'SEC mapping: {len(mapped)}/{len(requested)} tickers; unmapped={missing}',flush=True)
    count=0
    for i,(cik,group) in enumerate(sorted(entities.items()),1):
        print(f'SEC company {i}/{len(entities)}: {group}',flush=True)
        sub=client.get(f'https://data.sec.gov/submissions/CIK{cik}.json').json()
        recent=sub.get('filings',{}).get('recent',{})
        relevant=sorted(a for a,f,d in zip(recent.get('accessionNumber',[]),recent.get('form',[]),recent.get('filingDate',[])) if f in FORMS and d>=start)
        check_id=digest(json.dumps([cik,sorted(group),start,relevant]).encode())
        checkpoint=Path(root)/'data/fundamentals/v3/US/checks'/f'{check_id}.json'
        if checkpoint.exists():
            print('SEC: no new periodic filings; archived facts retained',flush=True)
            continue
        times=submission_times(sub)
        for page in sub.get('filings',{}).get('files',[]):
            if page.get('filingTo','9999')<start: continue
            name=page.get('name','')
            if not re.fullmatch(r'CIK\d+-submissions-\d+\.json',name):
                raise RuntimeError('Unexpected SEC submissions page name')
            times.update(submission_times(client.get('https://data.sec.gov/submissions/'+name).json()))
        response=client.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json')
        body=response.json()
        grouped=extract_facts(body,group,times,start)
        sha=digest(response.content)
        for accession,facts in grouped.items():
            if len(all_classes[cik])>1:
                for fact in facts:fact['valuation_allowed']=False
            save_filing(root,'US','SEC',cik,accession,facts,sha);count+=1
        if grouped and all(a in grouped for a in relevant):
            write_json_once(checkpoint,{'cik':cik,'tickers':sorted(group),'start':start,
                'periodic_accessions':relevant,'archived_accessions':sorted(grouped)})
        else:
            print('SEC periodic facts incomplete or unavailable; will check again next run',flush=True)
    return {'provider':'SEC','companies':len(entities),'filings':count,'unmapped_tickers':missing,'complete':True}
