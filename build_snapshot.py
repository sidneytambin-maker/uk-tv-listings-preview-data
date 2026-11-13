"""Build the public Preview guide. No listener data or Amazon credentials."""
import argparse,datetime as dt,gzip,hashlib,json,pathlib,re,time,urllib.parse,urllib.request
import xml.etree.ElementTree as ET
import sys
from collections import defaultdict
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'src'))

BASE='https://www.ontvnow.co.uk/api/v1/'
MAX_XML=50_000_000

_last_fetch=0.0

def fetch(path,params=None):
    global _last_fetch
    url=BASE+path+('?' + urllib.parse.urlencode(params) if params else '')
    request=urllib.request.Request(url,headers={'User-Agent':'UKTVListingsPreviewBackground/1.1'})
    # At most 40 source requests/minute, including retries. Cloud work never
    # occupies an Alexa request, and a failure never replaces the published guide.
    for attempt in range(3):
        time.sleep(max(0,1.5-(time.monotonic()-_last_fetch)))
        _last_fetch=time.monotonic();began=_last_fetch
        try:
            with urllib.request.urlopen(request,timeout=90) as response:
                data=response.read(MAX_XML+1)
            if len(data)>MAX_XML:raise ValueError('Source exceeds size limit')
            print(json.dumps({'feed':path,'bytes':len(data),'seconds':round(time.monotonic()-began,2)}),flush=True)
            return data
        except (HTTPError,URLError,TimeoutError) as error:
            if isinstance(error,HTTPError) and error.code not in (429,500,502,503,504):raise
            if attempt==2:raise
            time.sleep(5*(attempt+1))


def conflicted_rows(rows):
    active=[];conflicted=set()
    for i,p in sorted(enumerate(rows),key=lambda item:(item[1][2],item[1][0],item[1][1])):
        active=[(j,q) for j,q in active if q[2]==p[2] and q[1]>p[0]]
        if active:conflicted.add(i);conflicted.update(j for j,q in active)
        active.append((i,p))
    return conflicted


def reconcile_conflicts(rows,fetcher,now):
    """Resolve XMLTV overlaps only using matching JSON source revision evidence."""
    conflicts=conflicted_rows(rows)
    groups=set()
    for i in conflicts:
        p=rows[i]
        # A source day query includes a broadcast that crosses its boundary.
        groups.add((p[2],dt.datetime.fromtimestamp(p[0],ZoneInfo('Europe/London')).date().isoformat()))
    revisions={};requests=0
    by_url={p[5]:p for p in rows if p[5]}
    for cid,day in sorted(groups):
        page=1
        while True:
            body=json.loads(fetcher('listings',dict(channel_id=cid,date=day,period='day',limit=100,page=page)))
            requests+=1;meta=body['meta'];stamp=instant(meta['generated_at'])
            if not now-3600<=stamp<=int(time.time())+300:raise ValueError('Stale revision metadata')
            if int(meta['last_page'])>10 or int(meta['page'])!=page:raise ValueError('Invalid revision pagination')
            for item in body['data']:
                url=item.get('url');p=by_url.get(url)
                if str(item['channel']['id'])!=cid:raise ValueError('Revision channel mismatch')
                if not p:continue
                # Never apply a timestamp to different programme content.
                identity=[instant(item['starts_at']),instant(item['ends_at']),cid,clean_title(item['title'],item.get('description') or '')]
                if identity!=p[:4]:raise ValueError('Source changed during import; retry a complete update')
                updated=item.get('updated_at')
                if updated is not None:
                    stamp=instant(updated)
                    if stamp>int(time.time())+300:raise ValueError('Future revision')
                    revisions[url]=stamp
            if page>=int(meta['last_page']):break
            page+=1
    kept=[];per_channel=defaultdict(list)
    for p in sorted(rows,key=lambda p:revisions.get(p[5],-1),reverse=True):
        overlaps=[q for q in per_channel[p[2]] if p[0]<q[1] and q[0]<p[1]]
        if overlaps and p[5] in revisions and all(q[5] in revisions and revisions[q[5]]>revisions[p[5]] for q in overlaps):continue
        kept.append(p);per_channel[p[2]].append(p)
    return kept,revisions,requests


def iso(timestamp):return dt.datetime.fromtimestamp(timestamp,dt.timezone.utc).isoformat()
def instant(value):
    value=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None:raise ValueError('Missing source timezone')
    return int(value.timestamp())

def clean_title(title,description):
    title=title.strip()
    if re.search(r'(?:\.{3}|\u2026)$',title) and re.match(r'^(?:\.{3}|\u2026)',description.strip()):
        rest=re.sub(r'^(?:\.{3}|\u2026)\s*','',description.strip())
        sentence=re.match(r'^(.{1,160}?[.!?])(?:\s|$)',rest)
        if sentence:title=re.sub(r'(?:\.{3}|\u2026)$','',title).rstrip()+' '+sentence.group(1)
    return title

def off_air(title,description,name):
    key=' '.join(re.sub(r'[^a-z0-9 ]',' ',title.lower()).split())
    channel=' '.join(re.sub(r'[^a-z0-9 ]',' ',name.lower()).split())
    return (key in ('off air','closedown','close down','no programmes') or
        bool(re.fullmatch(r'programmes (?:start|resume|begin) at \d.*',key)) or
        (key in ('this is '+channel,'this is '+re.sub(r' (?:hd|sd)$','',channel)) and
         bool(re.search(r'programmes\s+(?:start|resume|begin)|tune\s+(?:in\s+)?every\s+day\s+from',description,re.I))))

def parse_export(raw,metadata,start,end,known,now):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('Unsafe XML')
    generated=instant(metadata['generated_at'])
    if not 0<=now-generated+300<=3900:raise ValueError('Stale or future metadata')
    root=ET.fromstring(raw)
    if root.tag!='tv':raise ValueError('Unexpected export')
    elements=root.findall('programme');total=int(metadata['total'])
    if total>=100000 or len(elements)!=total:raise ValueError('Incomplete export or changed source count')
    names={c.get('id'):c.findtext('display-name') or '' for c in root.findall('channel')}
    rows=[];seen=set();unknown=set()
    for element in elements:
        xmlid=element.get('channel')
        if xmlid not in known:unknown.add(xmlid);continue
        a=int(dt.datetime.strptime(element.get('start',''),'%Y%m%d%H%M%S %z').timestamp())
        b=int(dt.datetime.strptime(element.get('stop',''),'%Y%m%d%H%M%S %z').timestamp())
        title=element.findtext('title');description=element.findtext('desc') or '';url=element.findtext('url') or ''
        if not title or len(title)>1000 or len(description)>20000 or b<=a or b-a>3*86400 or a>=end or b<=start:raise ValueError('Invalid programme')
        if url and not re.fullmatch(r'https://(?:www\.)?ontvnow\.co\.uk/programmes/\d+',url):raise ValueError('Invalid source link')
        key=(xmlid,a,b,title)
        if key in seen:continue
        seen.add(key)
        rows.append([a,b,known[xmlid],clean_title(title,description),description,url,bool(off_air(title,description,names.get(xmlid,''))),False])
    return rows,sorted(unknown),generated

def pack(rows,channels,now,start,end,generated,previous=None):
    unique={tuple(row[:4]):row for row in rows}
    rows=sorted(unique.values(),key=lambda p:(p[2],p[0],p[1]))
    # Keep genuine conflicts visible to day lookups, and out of search answers.
    active=[]
    for row in rows:
        active=[p for p in active if p[2]==row[2] and p[1]>row[0]]
        if active:
            row[7]=True
            for p in active:p[7]=True
        active.append(row)
    if previous:
        old={};new={}
        common_end=min(end,previous['window'][1]);common_start=max(now,previous['window'][0])
        for p in previous['rows']:
            if p[0]<common_end and p[1]>common_start:old[p[2]]=old.get(p[2],0)+1
        for p in rows:
            if p[0]<common_end and p[1]>common_start:new[p[2]]=new.get(p[2],0)+1
        if sum(new.values())<sum(old.values())*.75:raise ValueError('Large coverage regression')
        if any(n>=4 and not new.get(cid) for cid,n in old.items()):raise ValueError('Previously covered channel disappeared')
    titles={};descriptions={};packed=[]
    for p in rows:
        packed.append(p[:3]+[titles.setdefault(p[3],len(titles)),descriptions.setdefault(p[4],len(descriptions))]+p[5:])
    return dict(schema=1,checked_at=now,source='ontvnow',source_generated_at=generated,window=[start,end],channels=channels,
                titles=list(titles),descriptions=list(descriptions),rows=packed)

def build(catalog,output,previous=None):
    now=int(time.time());local=dt.datetime.fromtimestamp(now,ZoneInfo('Europe/London'))
    start=int(local.replace(hour=0,minute=0,second=0,microsecond=0).timestamp());end=now+7*86400
    known={c['source_channel_id']:str(c['id']) for c in catalog['channels'] if c.get('source_channel_id') and not c.get('superseded_by')}
    directory=json.loads(fetch('channels'))['data']
    channels={str(c['id']):{'xmltv_id':c['xmltv_id'],'name':c['name']} for c in directory if c['xmltv_id'] in known and known[c['xmltv_id']]==str(c['id'])}
    if len(channels)<len(known)*.9:raise ValueError('Channel identity regression')
    rows=[];unknown=set();generated=[];requests=1
    for a,b in [(start,start+7*86400),(start+7*86400,end)]:
        params={'from':iso(a),'to':iso(b)}
        metadata=json.loads(fetch('listings',dict(params,limit=1)))['meta'];requests+=1
        total=int(metadata['total'])
        if not total:
            stamp=instant(metadata['generated_at'])
            if not now-3600<=stamp<=int(time.time())+300:raise ValueError('Stale empty export metadata')
            generated.append(stamp);continue
        raw=fetch('listings.xml',params);requests+=1
        batch,missing,stamp=parse_export(raw,metadata,a,b,known,int(time.time()));rows.extend(batch);unknown.update(missing);generated.append(stamp)
    if len(rows)<1000 or len({p[2] for p in rows})<len(channels)*.8:raise ValueError('Guide unexpectedly small')
    rows,revisions,revision_requests=reconcile_conflicts(rows,fetch,now)
    requests+=revision_requests
    result=pack(rows,channels,now,start,end,min(generated),previous)
    result['revisions']={p[5]:revisions[p[5]] for p in rows if p[5] in revisions}
    encoded=json.dumps(result,separators=(',',':'),ensure_ascii=False).encode('utf8')
    if len(encoded)>12_000_000:raise ValueError('Packed guide exceeds runtime memory budget')
    compressed=gzip.compress(encoded,mtime=0)
    digest=hashlib.sha256(compressed).hexdigest();filename='guide-'+digest+'.json.gz'
    output.mkdir(parents=True,exist_ok=True)
    if previous and previous.get('_previous_manifest'):
        (output/previous['_previous_manifest']['file']).write_bytes(previous['_previous_bytes'])
    (output/filename).write_bytes(compressed)
    manifest=dict(schema=1,checked_at=now,window=[start,end],file=filename,sha256=digest,bytes=len(compressed),json_bytes=len(encoded),programmes=len(result['rows']),channels=len(channels))
    from snapshot_reader import validate_data
    validate_data(result,manifest,now)
    (output/'current.json').write_text(json.dumps(manifest,separators=(',',':')),encoding='utf8')
    report=dict(manifest,source_requests=requests,unknown_source_channels=sorted(unknown),missing_supported_channels=sorted(set(known.values())-{p[2] for p in result['rows']}),conflicted_programmes=sum(bool(p[7]) for p in result['rows']))
    (output/'status.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    (output/'.nojekyll').write_text('')
    (output/'index.html').write_text('<!doctype html><html lang="en-GB"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>UK TV Listings Preview updates</title><main><h1>UK TV Listings Preview updates</h1><p>This free, personal accessibility project by Sidney Tambin prepares a shared programme guide for the private Alexa Preview. It is separate from Inclusophy Ltd.</p><p>Listings are supplied by <a href="https://www.ontvnow.co.uk/data-access">onTVnow</a>. Coverage and schedules can change. Missing listings do not prove a channel is off air. Programme links in the data refer to the source.</p><p>The guide is scheduled to refresh about every 30 minutes. Updates can be delayed. Failed checks retain the previous published guide. No listener information is collected by this update process.</p><p><a href="status.json">Latest update status (JSON)</a>. <a href="https://tv-listings-by-sidney.luckyledge3.chatgpt.site">Skill help and privacy</a>.</p></main></html>',encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('file','sha256','unknown_source_channels')}))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--catalog',default='catalog.json');parser.add_argument('--output',default='site');parser.add_argument('--previous')
    args=parser.parse_args();previous=None
    if args.previous:
        # Read only a known static snapshot, with bounded download and decoding.
        from snapshot_reader import download_previous
        previous=download_previous(args.previous)
    build(json.loads(pathlib.Path(args.catalog).read_text(encoding='utf8')),pathlib.Path(args.output),previous)
