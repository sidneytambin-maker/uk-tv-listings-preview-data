"""Bounded publication readiness and public read-back checks; no secret logging."""
import argparse,json,os,pathlib,time,urllib.request,urllib.error,http.client
from snapshot_reader import download,decode,MAX_GZIP

TRANSIENT=(408,429,500,502,503,504)

def wait_for_artifact(get_artifacts,artifact_id,sleep=time.sleep,attempts=12):
    for attempt in range(attempts):
        try:
            data=get_artifacts()
            if any(str(a.get('id'))==str(artifact_id) and a.get('name')=='github-pages'
                   and not a.get('expired') and a.get('size_in_bytes',0)>0
                   for a in data['artifacts']):
                print(json.dumps({'event':'publication_artifact_ready','attempts':attempt+1}),flush=True)
                return
        except (urllib.error.URLError,TimeoutError,http.client.IncompleteRead) as error:
            if isinstance(error,urllib.error.HTTPError) and error.code not in TRANSIENT:raise
        if attempt+1<attempts:sleep(5)
    raise RuntimeError('Uploaded guide is not yet visible to the publication API')

def github_artifacts():
    # GitHub-provided scoped job token stays only in memory and the HTTPS header.
    repo=os.environ['GITHUB_REPOSITORY'];run=os.environ['GITHUB_RUN_ID']
    request=urllib.request.Request(
        f'https://api.github.com/repos/{repo}/actions/runs/{run}/artifacts?per_page=100',
        headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],
                 'Accept':'application/vnd.github+json','User-Agent':'UKTVListingsPublication/1.0'})
    with urllib.request.urlopen(request,timeout=15) as response:
        return json.load(response)

def verify_publication(base,expected,get=download,sleep=time.sleep,attempts=12):
    for attempt in range(attempts):
        try:
            # Distinct URL avoids an old current.json response from a CDN edge.
            manifest=json.loads(get(base+'/current.json?v='+expected['sha256'],16000,15))
            if manifest==expected:
                raw=get(base+'/'+manifest['file'],MAX_GZIP,30)
                decode(raw,manifest,int(time.time()))
                print(json.dumps({'event':'publication_verified','checked_at':manifest['checked_at'],
                                  'programmes':manifest['programmes'],'channels':manifest['channels']}),flush=True)
                return
        except (urllib.error.URLError,TimeoutError,http.client.IncompleteRead) as error:
            if isinstance(error,urllib.error.HTTPError) and error.code not in (404,)+TRANSIENT:raise
        if attempt+1<attempts:sleep(5)
    raise RuntimeError('Published guide has not passed public read-back verification')

if __name__=='__main__':
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='mode',required=True)
    ready=sub.add_parser('artifact');ready.add_argument('artifact_id')
    verify=sub.add_parser('published');verify.add_argument('base');verify.add_argument('manifest')
    args=parser.parse_args()
    if args.mode=='artifact':wait_for_artifact(github_artifacts,args.artifact_id)
    else:verify_publication(args.base,json.loads(pathlib.Path(args.manifest).read_text(encoding='utf8')))
