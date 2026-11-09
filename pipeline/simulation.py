"""engine.sim.runner → KafkaEventSink를 잇는 k3s 배치 진입점."""
import argparse
from dataclasses import asdict,replace
import json
from pathlib import Path
import re
from engine.domain import params
from engine.domain.engine import BaselinePolicy,PaldoEngine
from engine.domain.npc import RuleV0Policy
from engine.domain.model import ENGINE_VERSION,PRESETS,DataSource,GameMeta
from engine.ports import EngineDeps
from engine.sim.bots import LogicBot,RandomBot
from engine.sim.runner import run_game
from engine.testing.fakes import FakeSuspicionModel
from pipeline.sinks.kafka_sink import KafkaEventSink
from tools.check_content import load_bundle
from tools.run_engine_games import EnvelopeBuilder,with_policy_versions


class RecordingEngine:
    """포트의 엔진 전이는 그대로 반환하고 각 전이 봉투만 EventSink에 보낸다."""

    def __init__(self,sink,index,policy=None,policy_version='baseline-0'):
        self.engine=PaldoEngine(policy or BaselinePolicy());self.sink=sink;self.index=index;self.builder=None;self.policy_version=policy_version

    def new_game(self,meta,deps):
        """판 시작 시 NPC 정책 버전을 고정하고 시작 이벤트를 발행한다."""
        transition=self.engine.new_game(meta,deps)
        if transition.accepted:
            state=with_policy_versions(transition.state,self.policy_version)
            transition=replace(transition,state=state)
            self.builder=EnvelopeBuilder(state.meta,self.index)
            self.sink.append(self.builder.wrap(transition.events,state.state_version,None))
        return transition

    def apply(self,state,truth,command,deps):
        """수락된 명령의 원본 ID와 전이 순서를 보존한다."""
        transition=self.engine.apply(state,truth,command,deps)
        if transition.accepted:self.sink.append(self.builder.wrap(transition.events,transition.state.state_version,command.command_id))
        return transition

    def player_view(self,state,content):
        """봇에는 기존 포트의 PlayerView만 제공한다."""
        return self.engine.player_view(state,content)


def main(argv=None):
    """명시한 판 수를 끝까지 진행하고 ACK된 이벤트의 판별 해시를 출력한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--kafka-config',type=Path,required=True)
    parser.add_argument('--bundle',type=Path,default=Path('content/bundle_v1.json'))
    parser.add_argument('--run-id',required=True);parser.add_argument('--preset',choices=sorted(PRESETS),required=True)
    parser.add_argument('--games',type=int,default=10);parser.add_argument('--seed',type=int,default=1)
    parser.add_argument('--bot',choices=['logic','random'],default='logic');parser.add_argument('--policy',choices=['min-legal','rule-v0'],default='min-legal');args=parser.parse_args(argv)
    if not re.fullmatch('[a-zA-Z0-9_]{1,40}',args.run_id) or not 1<=args.games<=500:raise ValueError('bounded_simulation_required')
    bundle=load_bundle(args.bundle);sink=KafkaEventSink(json.loads(args.kafka_config.read_text()))
    deps=EngineDeps(content=bundle,suspicion=FakeSuspicionModel());results=[]
    for index in range(args.games):
        meta=GameMeta(f'sim_{args.run_id}_{args.preset}_{index:05d}',PRESETS[args.preset],args.seed*1000000+index,
            DataSource.BOT,params.RULE_PARAMS_VERSION,ENGINE_VERSION,bundle.content_version,
            model_versions={'suspicion':FakeSuspicionModel.model_version})
        policy=RuleV0Policy(bundle) if args.policy=='rule-v0' else BaselinePolicy()
        version='rule-v0' if args.policy=='rule-v0' else 'baseline-0'
        results.append(asdict(run_game(RecordingEngine(sink,index,policy,version),deps,meta,LogicBot() if args.bot=='logic' else RandomBot())))
    print(json.dumps({'component':'k3s_simulation','run_id':args.run_id,'source':'bot','games':sink.manifest(),
        'completed':len(results),'illegal_commands':sum(r['illegal_commands'] for r in results),
        'model_version':FakeSuspicionModel.model_version,'results':results},sort_keys=True),flush=True)


if __name__=='__main__':main()

