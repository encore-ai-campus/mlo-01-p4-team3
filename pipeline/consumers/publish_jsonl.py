"""검증한 엔진 인수 JSONL을 공식 Kafka 토픽으로 전송한다. 웹 Outbox 경로와 별개다."""
import argparse
import hashlib
import json
from pathlib import Path
from pipeline.spark.bronze_contract import normalize


def publish(path,config):
    """전량 계약 검증 뒤 전송하고 모든 broker ACK·실제 offset 범위를 확인한다."""
    from confluent_kafka import Producer
    if config.get('security.protocol')!='SSL' or config.get('ssl.endpoint.identification.algorithm','https')!='https':
        raise ValueError('verified_tls_required')
    total=0;games=set()
    with Path(path).open(encoding='utf-8') as stream:
        for index,line in enumerate(stream):
            value=json.loads(line);normalize(line,topic=value['topic'],key=value['game_id'],partition=0,offset=index)
            total+=1;games.add(value['game_id'])
    producer=Producer({**config,'enable.idempotence':True,'acks':'all','delivery.timeout.ms':60000,'client.id':'paldo-engine-handoff'})
    ranges={};errors=[];acked=0
    def delivered(error,message):
        nonlocal acked
        if error:errors.append(type(error).__name__);return
        acked+=1;k=f'{message.topic()}:{message.partition()}';offset=message.offset()
        item=ranges.setdefault(k,{'first':offset,'last':offset,'count':0})
        item['first']=min(item['first'],offset);item['last']=max(item['last'],offset);item['count']+=1
    with Path(path).open(encoding='utf-8') as stream:
        for index,line in enumerate(stream):
            value=json.loads(line)
            while True:
                try:
                    producer.produce(value['topic'],key=value['game_id'],value=line.encode(),on_delivery=delivered);break
                except BufferError:producer.poll(.1)
            producer.poll(0)
    remaining=producer.flush(90)
    if remaining or errors or acked!=total:raise RuntimeError('not_all_events_acknowledged')
    return {'games':len(games),'sent':total,'acked':acked,'offsets':ranges,
            'source_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def main(argv=None):
    """보호 TLS 설정을 받아 명시 파일만 발행하고 비밀 없는 결과를 저장한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--events',type=Path,required=True)
    parser.add_argument('--client-config',type=Path,required=True);parser.add_argument('--result',type=Path,required=True)
    args=parser.parse_args(argv)
    result=publish(args.events,json.loads(args.client_config.read_text()))
    args.result.parent.mkdir(parents=True,exist_ok=True);args.result.write_text(json.dumps(result)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
