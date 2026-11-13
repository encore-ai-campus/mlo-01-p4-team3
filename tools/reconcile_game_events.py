"""Mongo next_seq·고유 Kafka 이벤트·Delta bronze의 판별 순번을 대조한다."""
import argparse
import json
from pathlib import Path


def reconcile(mongo,published,bronze):
    """누락·초과·잘못된 ID는 실패시키고 원본 중복 수는 별도로 보고한다."""
    results=[]
    for source in mongo:
        game=source['game_id'];next_seq=source['next_seq']
        if type(next_seq) is not int or next_seq<0:raise ValueError('invalid_next_seq')
        wanted=set(range(next_seq));item={'game_id':game,'next_seq':next_seq}
        for name,rows in [('published',published),('bronze',bronze)]:
            scoped=[r for r in rows if r['game_id']==game]
            seqs={r['seq'] for r in scoped}
            malformed=[r['seq'] for r in scoped if type(r['seq']) is not int or r['event_id']!=f'{game}:{r["seq"]:05d}']
            item[name+'_unique']=len(seqs);item[name+'_raw_duplicates']=len(scoped)-len(seqs)
            item[name+'_missing_seq']=sorted(wanted-seqs);item[name+'_unexpected_seq']=sorted(seqs-wanted)
            item[name+'_invalid_id_seq']=malformed
            expected_hashes=source.get('event_hashes',{})
            item[name+'_hash_mismatch_seq']=[r['seq'] for r in scoped if expected_hashes and r.get('envelope_hash')!=expected_hashes.get(r['event_id'])]
        item['ok']=all(not item[k] for k in item if k.endswith(('_missing_seq','_unexpected_seq','_invalid_id_seq','_hash_mismatch_seq')))
        # bronze itself must already be deduplicated; Kafka may legitimately replay.
        item['ok']=item['ok'] and item['bronze_raw_duplicates']==0
        results.append(item)
    return {'ok':bool(results) and all(r['ok'] for r in results),'games':results}


def main(argv=None):
    """비밀 없는 JSON 대조 자료를 읽고 실패 시 판 ID와 seq 차이를 출력한다."""
    parser=argparse.ArgumentParser()
    for name in ('mongo','published','bronze','out'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args(argv)
    result=reconcile(*[json.loads(getattr(args,name).read_text()) for name in ('mongo','published','bronze')])
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result)+'\n')
    print(json.dumps(result))
    if not result['ok']:raise SystemExit(1)


if __name__=='__main__':main()
