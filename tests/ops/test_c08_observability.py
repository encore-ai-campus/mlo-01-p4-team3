"""관측 실패 경계·민감 정보 배제와 실제 commit offset을 검증한다."""
import json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
from ops.prometheus.relay import validate
from ops.prometheus.pipeline_exporter import collect
from ops.prometheus.lifecycle_exporter import summarize


class ObservabilityTests(unittest.TestCase):
    """수집기가 임의 외부 주소를 요청하거나 미완료 offset을 사용하면 실패한다."""
    def test_local_relay_rejects_external_and_credentials(self):
        """고정 자기 주소·loopback만 허용하고 외부·userinfo는 막는다."""
        base={'bind':'192.0.2.10','allowed_clients':['192.0.2.20'],'upstreams':{'/metrics':'http://127.0.0.1:8000/metrics'}}
        self.assertIs(validate(base),base)
        for url in ('http://192.0.2.11/metrics','https://127.0.0.1/metrics','http://user:secret@127.0.0.1/metrics'):
            with self.assertRaises(ValueError):validate(base|{'upstreams':{'/metrics':url}})

    def test_lag_uses_committed_not_latest_offset_file(self):
        """commit=3인데 offsets/4가 앞서 있어도 batch3의 100 대비 high120을 쓴다."""
        class Consumer:
            """읽기 조회만 허용하고 commit·subscribe 대역을 제공하지 않는다."""
            def __init__(self,config):pass
            def get_watermark_offsets(self,*args,**kwargs):return 0,120
            def close(self):pass
        module=types.SimpleNamespace(Consumer=Consumer,TopicPartition=lambda topic,partition:(topic,partition))
        with tempfile.TemporaryDirectory() as directory,patch.dict(sys.modules,{'confluent_kafka':module}):
            root=Path(directory);(root/'commits').mkdir();(root/'offsets').mkdir();(root/'audit').mkdir()
            (root/'commits/3').write_text('v1\n{}');(root/'offsets/3').write_text('v1\n{}\n{"paldo.game":{"0":100}}')
            (root/'offsets/4').write_text('v1\n{}\n{"paldo.game":{"0":120}}');(root/'client.json').write_text('{}')
            body=collect({'checkpoint':str(root),'audit':str(root/'audit'),'kafka_client':str(root/'client.json')})
            self.assertIn('partition="0"} 20',body)

    def test_lifecycle_excludes_load_and_unfinished_and_deduplicates(self):
        """미완료·부하 판과 반응 재전송이 카나리아 승률을 오염시키지 않는다."""
        event={'event_id':'intent:1','event_type':'intent_attempt','payload':{'reaction':'edit'}}
        document={'state':{'meta':{'canary':True},'result':{'winner':'village','completed':True}},'outbox':[{'envelope':event},{'envelope':event}]}
        load={'state':document['state'],'outbox':[{'envelope':event|{'load_test':True}}]}
        unfinished={'state':{'meta':{'canary':True},'result':{'winner':'changgwi','completed':False}}}
        body=summarize([document,load,unfinished])
        self.assertIn('paldo_completed_games{canary="true"} 1',body)
        self.assertIn('paldo_village_win_ratio{canary="true"} 1.0',body)
        self.assertIn('paldo_intent_edit_cancel_ratio{canary="true"} 1.0',body)
        self.assertNotIn('intent:1',body)
