"""Mongo의 공개 종료 결과·최종 의도 반응만 읽어 카나리아 비교 신호를 집계한다."""
import argparse,json,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path


def summarize(documents):
    """부하 판·미완료 판을 승률에서 빼고 원문·진실·개인 식별자를 반환하지 않는다."""
    stats={key:{'games':0,'village':0,'intents':0,'changed':0} for key in ('true','false')}
    for document in documents:
        events=[x.get('envelope',{}) for x in document.get('outbox',[])]
        if any(e.get('load_test',False) for e in events):continue
        state=document.get('state',{});key=str(bool(state.get('meta',{}).get('canary',False))).lower();item=stats[key]
        result=state.get('result') or {}
        if result.get('completed',False):item['games']+=1;item['village']+=int(result.get('winner')=='village')
        seen=set()
        for event in events:
            identity=event.get('event_id')
            if identity in seen:continue
            seen.add(identity);reaction=event.get('payload',{}).get('reaction')
            if event.get('event_type')=='intent_attempt' and reaction is not None:
                item['intents']+=1;item['changed']+=int(reaction in ('edit','cancel'))
    lines=[]
    for canary,item in stats.items():
        labels='canary="'+canary+'"';lines.append(f'paldo_completed_games{{{labels}}} {item["games"]}')
        if item['games']:lines.append(f'paldo_village_win_ratio{{{labels}}} {item["village"]/item["games"]}')
        if item['intents']:lines.append(f'paldo_intent_edit_cancel_ratio{{{labels}}} {item["changed"]/item["intents"]}')
    return '\n'.join(lines)+'\n'


def main(argv=None):
    """Mongo에 aggregate용 SELECT만 하고 localhost에서 식별자 없는 수치를 제공한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--profile',type=Path,required=True);args=parser.parse_args(argv)
    from pymongo import MongoClient
    config=json.loads(args.profile.read_text());client=MongoClient(config['environment']['PALDO_MONGO_URI'],timeoutMS=5000)
    collection=client.get_default_database()['games']
    projection={'_id':0,'state.meta.canary':1,'state.result.winner':1,'state.result.completed':1,
        'outbox.envelope.event_type':1,'outbox.envelope.event_id':1,'outbox.envelope.payload.reaction':1,'outbox.envelope.load_test':1}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            """크기 상한이나 DB 오류를 조용한 0 대신 수집 실패로 표시한다."""
            if self.path!='/metrics':self.send_error(404);return
            try:
                documents=list(collection.find({},projection).limit(10001))
                if len(documents)>10000:raise ValueError('lifecycle_snapshot_bound')
                body=summarize(documents)+'paldo_lifecycle_collection_success 1\n'
            except Exception:body='paldo_lifecycle_collection_success 0\n'
            self.send_response(200);self.end_headers();self.wfile.write(body.encode())
        def log_message(self,*args):
            """지표 요청의 반복 로그를 생략한다."""
    ThreadingHTTPServer(('127.0.0.1',9115),Handler).serve_forever()


if __name__=='__main__':main()
