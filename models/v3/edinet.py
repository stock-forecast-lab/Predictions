"""EDINET annual/interim filings; parse XBRL contexts, never PDF guesses."""
from datetime import date,timedelta
from io import BytesIO
from pathlib import Path
import hashlib,json,math,os
import zipfile
import pandas as pd
from defusedxml import ElementTree as ET
from .config import EDINET_TAGS,DURATION_METRICS
from .http import Client,FetchError
from .storage import digest,save_filing,write_json_once

NS={'x':'http://www.xbrl.org/2003/instance','d':'http://xbrl.org/2006/xbrldi'}
DOCUMENTS={'120','130','140','150','160','170'}

def local(tag): return tag.rsplit('}',1)[-1]

def contexts(tree):
    result={}
    for c in tree.findall('.//x:context',NS):
        members=c.findall('.//d:explicitMember',NS)
        if c.findall('.//d:typedMember',NS): continue
        names=[(m.text or '').split(':')[-1] for m in members]
        if any(m!='NonConsolidatedMember' for m in names): continue
        instant=c.findtext('x:period/x:instant',namespaces=NS)
        begin=c.findtext('x:period/x:startDate',namespaces=NS)
        end=c.findtext('x:period/x:endDate',namespaces=NS)
        if not instant and not(begin and end): continue
        result[c.get('id')]={'start':begin or '', 'end':instant or end,
            'scope':'standalone' if names else 'consolidated'}
    return result

def parse_package(content,document,ticker):
    published=pd.Timestamp(document['submitDateTime'],tz='Asia/Tokyo').tz_convert('UTC')
    available=published+pd.Timedelta(days=1)
    candidates=[]
    with zipfile.ZipFile(BytesIO(content)) as z:
        if len(z.infolist())>10000 or sum(i.file_size for i in z.infolist())>300_000_000:
            raise RuntimeError('EDINET archive exceeds size limits')
        for item in z.infolist():
            if not item.filename.lower().endswith('.xbrl') or 'publicdoc/' not in item.filename.lower(): continue
            tree=ET.fromstring(z.read(item)); ctx=contexts(tree)
            units={u.get('id'):(u.findtext('x:measure',namespaces=NS) or '').split(':')[-1]
                   for u in tree.findall('.//x:unit',NS)}
            for e in tree.iter():
                context=ctx.get(e.get('contextRef'));tag=local(e.tag)
                namespace=e.tag.rsplit('}',1)[0].lower()
                if not any(n in namespace for n in ['jpcrp','jppfs','jpigp','xbrl.ifrs']):continue
                if context is None or not e.text or e.get('unitRef') not in units: continue
                unit=units[e.get('unitRef')]
                try: value=float(e.text.strip().replace(',',''))
                except ValueError: continue
                if not math.isfinite(value): continue
                if pd.Timestamp(context['end'])>published.tz_convert('Asia/Tokyo').tz_localize(None).normalize(): continue
                for metric,tags in EDINET_TAGS.items():
                    if tag not in tags: continue
                    # Monetary/per-share numerators are in JPY; dimensions and
                    # duration are checked before an alias can supply a value.
                    wanted='shares' if metric in {'issued_shares','treasury_shares'} else 'JPY'
                    # EPS uses a divide unit (JPY/shares), parsed below.
                    if metric=='eps':
                        u=next((u for u in tree.findall('.//x:unit',NS) if u.get('id')==e.get('unitRef')),None)
                        numerator=u.findtext('.//x:unitNumerator/x:measure',namespaces=NS) if u is not None else None
                        denominator=u.findtext('.//x:unitDenominator/x:measure',namespaces=NS) if u is not None else None
                        if not numerator or not denominator or not numerator.endswith(':JPY') or not denominator.endswith(':shares'): continue
                        unit='JPY/shares'
                    elif unit!=wanted: continue
                    if metric in DURATION_METRICS:
                        if not context['start']: continue
                        days=(pd.Timestamp(context['end'])-pd.Timestamp(context['start'])).days+1
                        if not 60<=days<=400: continue
                    elif context['start']: continue
                    candidates.append(dict(ticker=ticker,metric=metric,**context,value=value,unit=unit,
                        tag=tag,priority=tags.index(tag),filing_id=document['docID'],provider='EDINET',
                        published_at_utc=published.isoformat(),available_at_utc=available.isoformat(),
                        availability_basis='EDINET_submission_plus_1_calendar_day',valuation_allowed=True))
    chosen={}
    for row in sorted(candidates,key=lambda r:(r['priority'],r['tag'])):
        key=tuple(row[k] for k in ['ticker','metric','start','end','unit','scope'])
        if key in chosen and chosen[key]['priority']==row['priority'] and chosen[key]['value']!=row['value']:
            raise RuntimeError(f'Ambiguous EDINET fact {document["docID"]}: {row["metric"]}')
        chosen.setdefault(key,row)
    return list(chosen.values())

def synchronize(root,tickers,start,end,max_days=120,client=None):
    key=os.getenv('EDINET_API_KEY','').strip()
    if client is None and not key:
        raise RuntimeError('Set EDINET_API_KEY in repository Actions Secrets; do not paste the key into chat')
    client=client or Client(interval=.25)
    tickers=set(tickers)
    universe_hash=digest('|'.join(sorted(tickers)).encode())[:16]
    base=Path(root)/'data/fundamentals/v3/JP'
    scans=base/'scans'/universe_hash
    last=date.fromisoformat(end);first=date.fromisoformat(start)
    dates=[first+timedelta(days=i) for i in range((last-first).days+1)]
    recent=last-timedelta(days=7)
    # Recent filings first. Completed historical days are skipped on the next run.
    pending=[d for d in reversed(dates) if d>=recent or not(scans/f'{d}.json').exists()]
    processed=0;filings=0
    for day in pending[:max_days]:
        print(f'EDINET scan {day}: {processed+1}/{min(max_days,len(pending))}',flush=True)
        response=client.get('https://api.edinet-fsa.go.jp/api/v2/documents.json',
            {'date':day.isoformat(),'type':2,'Subscription-Key':key})
        body=response.json();meta=body.get('metadata',{})
        if str(meta.get('status',''))!='200' or 'results' not in body:
            raise FetchError('EDINET document list rejected; check API key/status (response omitted)')
        selected=[]
        for doc in body['results']:
            code=str(doc.get('secCode') or '')
            ticker=code[:4]+'.T'
            if ticker not in tickers or doc.get('docTypeCode') not in DOCUMENTS: continue
            if doc.get('withdrawalStatus','0')!='0' or doc.get('disclosureStatus','0')!='0': continue
            if doc.get('xbrlFlag')!='1': continue
            ident=digest(f'EDINET|{doc["edinetCode"]}|{doc["docID"]}'.encode())
            path=base/'filings'/f'{ident}.json'
            if not path.exists():
                package=client.get(f'https://api.edinet-fsa.go.jp/api/v2/documents/{doc["docID"]}',
                    {'type':1,'Subscription-Key':key})
                if not package.content.startswith(b'PK'):
                    raise FetchError('EDINET document download did not return a ZIP; retry later')
                facts=parse_package(package.content,doc,ticker)
                save_filing(root,'JP','EDINET',doc['edinetCode'],doc['docID'],facts,digest(package.content))
            selected.append(doc['docID']);filings+=1
        # Mark the date complete only after every selected download/parse succeeded.
        marker=scans/f'{day}.json'
        if not marker.exists():
            write_json_once(marker,{'date':day.isoformat(),'selected_doc_ids':selected,
                                   'source_sha256':digest(response.content)})
        processed+=1
    remaining=sum(not(scans/f'{d}.json').exists() for d in dates)
    return {'provider':'EDINET','scanned_days':processed,'filings_seen':filings,
            'pending_historical_days':remaining,'complete':remaining==0,
            'history_start':start,'history_end':end}
