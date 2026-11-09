"""시뮬레이터 EventSink: 공식 bot 봉투를 검증하고 브로커 ACK를 기다린다."""
import hashlib
from engine.domain.model import DataSource
from engine.domain.serde import dumps
from pipeline.spark.bronze_contract import normalize


class KafkaEventSink:
    """같은 ID·seq·본문을 재시도하며 ACK 실패를 성공으로 숨기지 않는다."""

    def __init__(self, config, producer_factory=None):
        if config.get('security.protocol') != 'SSL' or config.get('ssl.endpoint.identification.algorithm','https') != 'https':
            raise ValueError('verified_tls_required')
        if producer_factory is None:
            from confluent_kafka import Producer
            producer_factory=Producer
        self.producer=producer_factory(config|{'acks':'all','enable.idempotence':True,'delivery.timeout.ms':15000})
        self.games={}

    def append(self,envelopes):
        """배치 전량을 검증한 뒤 전송하고 판별 순서 해시를 기록한다."""
        prepared=[];next_seq={identity:value['events'] for identity,value in self.games.items()}
        for event in envelopes:
            if event.source is not DataSource.BOT:raise ValueError('bot_only')
            raw=dumps(event);row=normalize(raw,topic=event.topic,key=event.game_id,partition=0,offset=0)
            if event.seq!=next_seq.get(event.game_id,0):raise ValueError('noncontiguous_seq')
            next_seq[event.game_id]=event.seq+1
            prepared.append((event,raw,row['envelope_hash']))
        errors=[];acks=[]
        for event,raw,digest in prepared:
            self.producer.produce(event.topic,key=event.game_id,value=raw.encode(),on_delivery=lambda error,message:(errors.append(error) if error else acks.append(1)))
            self.producer.poll(0)
        remaining=self.producer.flush(20)
        if remaining or errors or len(acks)!=len(prepared):raise RuntimeError('kafka_ack_failed')
        for event,raw,digest in prepared:
            game=self.games.setdefault(event.game_id,{'events':0,'hash':hashlib.sha256()})
            if event.seq!=game['events']:raise ValueError('noncontiguous_seq')
            game['hash'].update(digest.encode());game['events']+=1

    def manifest(self):
        """이벤트 본문·진실·인증정보 없는 대사 자료를 반환한다."""
        return {identity:{'events':value['events'],'sha256':value['hash'].hexdigest()} for identity,value in self.games.items()}
