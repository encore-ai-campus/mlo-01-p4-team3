"""실제 runner 전이·Kafka ACK 경계와 재현성을 검증한다."""
from dataclasses import replace
from pathlib import Path
import unittest
from engine.domain import params
from engine.domain.model import DataSource,GameMeta,PRESETS,ENGINE_VERSION
from engine.ports import EngineDeps
from engine.sim.bots import LogicBot
from engine.sim.runner import run_game
from engine.testing.fakes import FakeSuspicionModel
from pipeline.simulation import RecordingEngine
from pipeline.sinks.kafka_sink import KafkaEventSink
from tools.check_content import load_bundle


class AckProducer:
    """전송한 실제 봉투와 ACK를 메모리 브로커 대역에 남긴다."""
    def __init__(self,config):
        self.rows=[];self.callbacks=[];self.fail=False
    def produce(self,topic,*,key,value,on_delivery):
        """토픽·원본 바이트와 콜백을 보존한다."""
        self.rows.append((topic,key,value));self.callbacks.append(on_delivery)
    def poll(self,timeout):
        """flush에서만 ACK를 전달한다."""
    def flush(self,timeout):
        """성공 또는 실패 ACK를 전송 순서대로 돌려준다."""
        for callback in self.callbacks:callback(RuntimeError('broker_unavailable') if self.fail else None,None)
        self.callbacks=[];return 0


class KafkaSimulationTests(unittest.TestCase):
    """웹 없이 실제 runner가 완주하고 ACK 실패·잘못된 seq는 성공하지 않아야 한다."""
    def test_real_runner_reproducible_and_complete(self):
        """같은 시드·ID로 runner를 두 번 실행하면 Kafka 바이트도 같다."""
        bundle=load_bundle(Path('content/bundle_v1.json'));outputs=[]
        for _ in range(2):
            sink=KafkaEventSink({'security.protocol':'SSL'},AckProducer)
            meta=GameMeta('sim_test_base6_00000',PRESETS['base6'],808,DataSource.BOT,params.RULE_PARAMS_VERSION,ENGINE_VERSION,bundle.content_version)
            result=run_game(RecordingEngine(sink,0),EngineDeps(bundle,FakeSuspicionModel()),meta,LogicBot())
            self.assertEqual(result.illegal_commands,0);self.assertGreater(sink.manifest()[meta.game_id]['events'],20)
            self.assertTrue(any(topic=='paldo.truth' for topic,key,value in sink.producer.rows));outputs.append(sink.producer.rows)
        self.assertEqual(outputs[0],outputs[1])

    def test_ack_failure_never_updates_manifest(self):
        """ACK 실패 시 전송됐을 가능성이 있어도 완료 manifest를 만들지 않는다."""
        bundle=load_bundle(Path('content/bundle_v1.json'));sink=KafkaEventSink({'security.protocol':'SSL'},AckProducer);sink.producer.fail=True
        meta=GameMeta('sim_ack_base6_00000',PRESETS['base6'],1,DataSource.BOT,params.RULE_PARAMS_VERSION,ENGINE_VERSION,bundle.content_version)
        with self.assertRaisesRegex(RuntimeError,'kafka_ack_failed'):RecordingEngine(sink,0).new_game(meta,EngineDeps(bundle,FakeSuspicionModel()))
        self.assertEqual(sink.manifest(),{})

    def test_unverified_tls_rejected(self):
        """실수로 평문 또는 호스트 검증 해제를 넣어도 생산자를 만들지 않는다."""
        for config in ({'security.protocol':'PLAINTEXT'},{'security.protocol':'SSL','ssl.endpoint.identification.algorithm':'none'}):
            with self.assertRaises(ValueError):KafkaEventSink(config,AckProducer)
