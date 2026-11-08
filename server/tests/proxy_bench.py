"""동일한 실제 GET을 직접 HTTP와 HTTPS 프록시로 1분씩 측정한다."""
import argparse
import hashlib
import json
from pathlib import Path
import ssl
import statistics
import time
import urllib.request
from urllib.parse import urlsplit


def measure(url,headers,context,seconds):
    """초당 한 번 새 연결로 읽고 상태·본문 해시·p50/p95를 집계한다."""
    values=[];hashes=set();errors=0;deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        tick=time.monotonic()
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers=headers),context=context,timeout=10) as response:
                if response.status!=200:raise ValueError('http_status')
                hashes.add(hashlib.sha256(response.read()).hexdigest())
            values.append((time.monotonic()-tick)*1000)
        except Exception:errors+=1
        time.sleep(max(0,min(tick+1,deadline)-time.monotonic()))
    values.sort()
    return {'requests':len(values)+errors,'errors':errors,'p50_ms':round(statistics.median(values),3) if values else None,
            'p95_ms':round(values[max(0,(95*len(values)+99)//100-1)],3) if values else None,'body_hashes':sorted(hashes)}


def main(argv=None):
    """호스트 내부 보호 파일을 읽으며 쿠키·주소를 출력하지 않는다."""
    parser=argparse.ArgumentParser();parser.add_argument('--proxy-profile',required=True)
    parser.add_argument('--seconds',type=int,default=60);parser.add_argument('--out',required=True)
    args=parser.parse_args(argv)
    if not 60<=args.seconds<=300:parser.error('seconds must be 60..300')
    profile=json.loads(Path(args.proxy_profile).read_text())
    actor=profile['actors']['broker'];runtime=json.loads(Path('/etc/pj4-secrets/runtime.json').read_text())
    origin=runtime['environment']['PALDO_PUBLIC_ORIGIN'];host=urlsplit(origin).netloc
    game=json.loads(Path('/opt/pj4/live-check/broker.json').read_text())['view']['game_id']
    headers={'Accept':'application/json','Cookie':'='.join(actor['cookie']),'Host':host,'X-Forwarded-Proto':'https'}
    context=ssl.create_default_context(cafile='/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt')
    result={}
    for name,url in [('direct','http://127.0.0.1:8000'),('proxy',origin)]:
        result[name]=measure(url+'/games/'+game,headers,context,args.seconds)
    result['same_response']=result['direct']['body_hashes']==result['proxy']['body_hashes'] and len(result['direct']['body_hashes'])==1
    for value in (result['direct'],result['proxy']):value.pop('body_hashes')
    result['ok']=result['same_response'] and all(result[n]['errors']==0 for n in ('direct','proxy'))
    Path(args.out).write_text(json.dumps(result)+'\n');print(json.dumps(result))
    if not result['ok']:raise SystemExit(1)


if __name__=='__main__':main()
