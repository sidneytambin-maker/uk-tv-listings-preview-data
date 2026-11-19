"""Bounded validation/decoding shared by the background builder and skill."""
import gzip,hashlib,io,json,re,time,urllib.request,urllib.error,http.client

MAX_JSON=12_000_000
MAX_GZIP=4_000_000

def validate_manifest(value,now):
    if not isinstance(value,dict) or value.get('schema')!=1:raise ValueError('Invalid manifest')
    stamp=value.get('checked_at')
    if not isinstance(stamp,int) or stamp>now+300 or now-stamp>=36*3600:raise ValueError('Expired snapshot')
    digest=value.get('sha256','')
    if not re.fullmatch(r'[a-f0-9]{64}',digest) or value.get('file')!='guide-'+digest+'.json.gz':raise ValueError('Invalid snapshot path')
    if not 0<value.get('bytes',0)<=MAX_GZIP or not 0<value.get('json_bytes',0)<=MAX_JSON:raise ValueError('Invalid snapshot size')
    return value

def validate_data(value,manifest,now):
    validate_manifest(manifest,now)
    if not isinstance(value,dict) or value.get('schema')!=1 or value.get('source')!='ontvnow' or value.get('checked_at')!=manifest['checked_at']:raise ValueError('Snapshot identity mismatch')
    window=value.get('window')
    if window!=manifest.get('window') or not isinstance(window,list) or len(window)!=2 or not all(isinstance(n,int) for n in window) or not 0<window[1]-window[0]<=9*86400:raise ValueError('Invalid snapshot window')
    generated=value.get('source_generated_at')
    if not isinstance(generated,int) or not value['checked_at']-3600<=generated<=value['checked_at']+300:raise ValueError('Stale source generation')
    titles=value.get('titles');descriptions=value.get('descriptions');channels=value.get('channels');rows=value.get('rows')
    if not isinstance(titles,list) or not isinstance(descriptions,list) or not isinstance(channels,dict) or not isinstance(rows,list) or len(rows)!=manifest.get('programmes') or len(rows)>100000:raise ValueError('Invalid tables')
    for cid,channel in channels.items():
        if not isinstance(cid,str) or not cid.isdigit() or not isinstance(channel,dict) or not isinstance(channel.get('name'),str) or not isinstance(channel.get('xmltv_id'),str):raise ValueError('Invalid channel identity')
    if any(not isinstance(s,str) or len(s)>1000 or not s for s in titles):raise ValueError('Invalid title')
    if any(not isinstance(s,str) or len(s)>20000 for s in descriptions):raise ValueError('Invalid description')
    revisions=value.get('revisions',{})
    if not isinstance(revisions,dict) or len(revisions)>100000:raise ValueError('Invalid revision table')
    for url,stamp in revisions.items():
        if (not isinstance(url,str) or not re.fullmatch(r'https://(?:www\.)?ontvnow\.co\.uk/programmes/\d+',url)
                or not isinstance(stamp,int) or isinstance(stamp,bool) or stamp>now+300):raise ValueError('Invalid revision evidence')
    for p in rows:
        if not isinstance(p,list) or len(p)!=8 or not all(isinstance(p[n],int) and not isinstance(p[n],bool) for n in (0,1,3,4)):raise ValueError('Invalid row')
        a,b,c,ti,di,url,off,conflict=p
        if b<=a or b-a>3*86400 or a>=window[1] or b<=window[0] or c not in channels or not 0<=ti<len(titles) or not 0<=di<len(descriptions):raise ValueError('Invalid row values')
        if not isinstance(off,bool) or not isinstance(conflict,bool) or not isinstance(url,str) or (url and not re.fullmatch(r'https://(?:www\.)?ontvnow\.co\.uk/programmes/\d+',url)):raise ValueError('Invalid row evidence')
    return value

def decode(raw,manifest,now):
    validate_manifest(manifest,now)
    if len(raw)!=manifest['bytes'] or hashlib.sha256(raw).hexdigest()!=manifest['sha256']:raise ValueError('Snapshot checksum mismatch')
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:body=stream.read(MAX_JSON+1)
    if len(body)!=manifest['json_bytes'] or len(body)>MAX_JSON:raise ValueError('Snapshot expanded size mismatch')
    return validate_data(json.loads(body),manifest,now)

def download(url,limit,timeout):
    if not url.startswith('https://'):raise ValueError('HTTPS required')
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'UKTVListingsPreviewSnapshot/1.0'}),timeout=timeout) as response:
        body=response.read(limit+1)
    if len(body)>limit:raise ValueError('Response exceeds limit')
    return body

def _download_previous(base):
    now=int(time.time())
    try:
        candidate=json.loads(download(base+'/current.json',16000,15))
        # An old, valid build remains useful for coverage comparison after an
        # extended outage, even though the skill will not serve expired data.
        check_time=min(now,candidate.get('checked_at',now)+35*3600)
        manifest=validate_manifest(candidate,check_time)
        raw=download(base+'/'+manifest['file'],MAX_GZIP,30)
        result=decode(raw,manifest,check_time)
        result.update(_previous_manifest=manifest,_previous_bytes=raw)
        return result
    except urllib.error.HTTPError:
        # A temporarily missing file must not silently disable comparison with
        # the last good guide. This is an established publication, not bootstrap.
        raise

def download_previous(base):
    """Retry the manifest/file pair, retaining all integrity/coverage checks."""
    for attempt in range(3):
        try:
            return _download_previous(base)
        except (urllib.error.URLError,TimeoutError,http.client.IncompleteRead) as error:
            if isinstance(error,urllib.error.HTTPError) and error.code not in (404,408,429,500,502,503,504):raise
            if attempt==2:raise
            print(json.dumps({'event':'previous_guide_retry','attempt':attempt+1}),flush=True)
            time.sleep((5,15)[attempt])
