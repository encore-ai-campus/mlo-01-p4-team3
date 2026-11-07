"""C-06-01: 실제 Replica Set에서 전체 게임 상태 CAS 지연과 histogram을 측정한다."""
import argparse
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import time
import uuid
from engine.domain.engine import PaldoEngine
from engine.domain.model import DataSource, GameMeta, PRESETS
from engine.ports import EngineDeps, StoredCommand
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from server.infra.mongo_repo import MongoGameRepository
from tools.jsonlog import log


def run(uri, samples=50):
    """실제 엔진 초기 상태를 저장하고 순차 CAS의 nearest-rank p50/p95를 반환한다."""
    from pymongo import MongoClient
    from prometheus_client import CollectorRegistry, Histogram, generate_latest
    client=MongoClient(uri,serverSelectionTimeoutMS=5000,timeoutMS=15000)
    try:
        hello=client.admin.command('hello')
        if not hello.get('setName'):raise ValueError('replica_set_required')
        registry=CollectorRegistry()
        histogram=Histogram('paldo_repo_commit_duration_seconds','commit duration',['status'],registry=registry)
        game='bench_'+uuid.uuid4().hex
        content=FixtureContentSource().load('*')
        meta=GameMeta(game,PRESETS['base6'],7,DataSource.SCRIPTED,'rule-0.1','bench',content.content_version)
        initial=PaldoEngine().new_game(meta,EngineDeps(content,FakeSuspicionModel()))
        repo=MongoGameRepository(client.get_default_database()['paldo_c06_benchmark_games'],histogram)
        repo.create(initial.state,initial.truth,[])
        durations=[]
        for index in range(samples):
            state=replace(initial.state,state_version=index+1)
            started=time.perf_counter()
            repo.commit(game,index,state,initial.truth,[],StoredCommand(str(index),'{}'))
            durations.append((time.perf_counter()-started)*1000)
        durations.sort()
        metric_text=generate_latest(registry).decode()
        if f'paldo_repo_commit_duration_seconds_count{{status="ok"}} {samples}.0' not in metric_text:
            raise RuntimeError('histogram_count_mismatch')
        return {'samples':samples,'commit_p50_ms':durations[math.ceil(samples*.5)-1],
                'commit_p95_ms':durations[math.ceil(samples*.95)-1],'histogram_exposed':1,
                'replica_set':hello['setName'],'python':__import__('platform').python_version()}
    finally:client.close()


def main(argv=None):
    """외부 URI로 VM 측정 후 out/perf JSON을 남긴다. raw URI는 출력하지 않는다."""
    parser=argparse.ArgumentParser();parser.add_argument('--samples',type=int,default=50)
    parser.add_argument('--out',type=Path,default=Path('out/perf/mongo_repo.json'))
    args=parser.parse_args(argv)
    if args.samples<2:parser.error('samples must be >= 2')
    log('mongo_repo_bench','info','benchmark_started',samples=args.samples)
    try:
        result=run(os.environ['PALDO_TEST_MONGO_URI'],args.samples)
        args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result)+'\n')
        log('mongo_repo_bench','info','benchmark_finished',**result)
    except Exception as error:
        log('mongo_repo_bench','error','benchmark_failed',error_type=type(error).__name__)
        raise SystemExit(1) from None


if __name__=='__main__':main()
