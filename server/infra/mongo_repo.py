"""게임 상태·진실·Outbox·명령을 한 Mongo 문서에 majority/CAS로 저장한다."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import time

from engine.domain.events import EventEnvelope
from engine.domain.model import GameState, GameTruth
from engine.domain.serde import from_jsonable, to_jsonable
from engine.ports import StoredCommand, VersionConflict

_COMMIT_DURATION = None


def commit_histogram():
    """웹·발행기 registry가 함께 수집할 기본 commit Histogram을 한 번 등록한다."""
    global _COMMIT_DURATION
    if _COMMIT_DURATION is None:
        from prometheus_client import Histogram
        _COMMIT_DURATION = Histogram('paldo_repo_commit_duration_seconds', 'Mongo game commit duration',
                                     ['status'], buckets=(.001,.0025,.005,.01,.025,.05,.1,.25,.5,1,2.5))
    return _COMMIT_DURATION


def majority(collection):
    """기존 connection을 바꾸지 않고 이 저장소의 read/write concern을 고정한다."""
    from pymongo.read_concern import ReadConcern
    from pymongo.write_concern import WriteConcern
    return collection.with_options(write_concern=WriteConcern(w='majority'), read_concern=ReadConcern('majority'))


def _pending(envelopes, start):
    now = datetime.now(timezone.utc)
    return [{'envelope':to_jsonable(replace(e, seq=start+i, event_id=f'{e.game_id}:{start+i:05d}')),
             'published':False,'attempts':0,'committed_at':now} for i,e in enumerate(envelopes)]


def append_expression(envelopes):
    """Mongo 현재 next_seq에서 번호를 배정하는 atomic update 표현식이다."""
    values = [to_jsonable(envelope) for envelope in envelopes]
    sequence = {'$add':[{'$ifNull':['$next_seq',0]}, '$$index']}
    number = {'$toString':'$$sequence'}
    padded = {'$cond':[{'$lt':['$$sequence',100000]},
                       {'$substrCP':[{'$concat':['00000',number]},{'$strLenCP':number},5]},number]}
    return {'$map':{'input':{'$range':[0,len(values)]},'as':'index','in':{
        '$let':{'vars':{'sequence':sequence,'event':{'$arrayElemAt':[{'$literal':values},'$$index']}},'in':{
            'envelope':{'$mergeObjects':['$$event',{'seq':'$$sequence','event_id':{'$concat':['$$event.game_id',':',padded]}}]},
            'published':False,'attempts':0,'committed_at':'$$NOW'}}}}}


class MongoGameRepository:
    """GameRepository 포트. append_events와 commit은 동일 next_seq를 원자적으로 쓴다."""

    def __init__(self, collection, histogram=None):
        self.collection = majority(collection)
        self.histogram = histogram if histogram is not None else commit_histogram()

    def create(self, state, truth, outbox):
        """기존 판은 덮어쓰지 않고 최초 상태와 Outbox를 한 번에 삽입한다."""
        from pymongo.errors import DuplicateKeyError
        document={'_id':state.meta.game_id,'state':to_jsonable(state),'truth':to_jsonable(truth),
                  'outbox':_pending(outbox,0),'commands':[],'dedup':[],'next_seq':len(outbox)}
        try:
            self.collection.insert_one(document)
        except DuplicateKeyError:
            raise ValueError('game_already_exists') from None

    def load(self, game_id):
        """엔진 타입으로 복원한다. 진실은 저장소 밖 응답에 자동 포함하지 않는다."""
        document=self.collection.find_one({'_id':game_id},{'state':1,'truth':1})
        if document is None: raise KeyError(game_id)
        return from_jsonable(GameState,document['state']),from_jsonable(GameTruth,document['truth'])

    def find_command(self, game_id, command_id):
        """명령 ID에 대응하는 저장 응답을 찾는다. ID를 Mongo 필드명으로 쓰지 않는다."""
        document=self.collection.find_one({'_id':game_id},{'commands':{'$elemMatch':{'command_id':command_id}}})
        if not document or not document.get('commands'): return None
        return from_jsonable(StoredCommand,document['commands'][0])

    def commit(self, game_id, expected_version, state, truth, outbox, command):
        """상태·진실·Outbox·명령을 하나의 CAS update pipeline으로 반영한다."""
        if state.state_version != expected_version+1:
            raise ValueError('state_version_must_increment_once')
        if state.meta.game_id != game_id or truth.game_id != game_id:
            raise ValueError('game_id_mismatch')
        started=time.monotonic();status='error'
        try:
            update=[{'$set':{'state':{'$literal':to_jsonable(state)},'truth':{'$literal':to_jsonable(truth)},
                            'outbox':{'$concatArrays':['$outbox',append_expression(outbox)]},
                            'next_seq':{'$add':['$next_seq',len(outbox)]},
                            'commands':{'$concatArrays':['$commands',{'$literal':[to_jsonable(command)]}]}}}]
            result=self.collection.update_one({'_id':game_id,'state.state_version':expected_version,
                                               'commands.command_id':{'$ne':command.command_id}},update)
            if result.matched_count!=1:
                document=self.collection.find_one({'_id':game_id},{'state.state_version':1})
                if document is None: raise KeyError(game_id)
                status='conflict'
                raise VersionConflict(expected_version,document['state']['state_version'])
            status='ok'
        finally:
            self.histogram.labels(status=status).observe(time.monotonic()-started)

    def append_events(self, game_id, outbox, dedup_key):
        """상태 버전과 별개로 공통 순번을 배정하고 dedup_key를 원자 기록한다."""
        update=[{'$set':{'outbox':{'$concatArrays':['$outbox',append_expression(outbox)]},
                         'next_seq':{'$add':['$next_seq',len(outbox)]},
                         'dedup':{'$concatArrays':['$dedup',{'$literal':[dedup_key]}]}}}]
        result=self.collection.update_one({'_id':game_id,'dedup':{'$ne':dedup_key}},update)
        if result.matched_count: return True
        if not self.collection.find_one({'_id':game_id},{'_id':1}): raise KeyError(game_id)
        return False

    def unpublished(self, game_id):
        """테스트·진척 복구를 위한 미발행 봉투 snapshot을 순번대로 반환한다."""
        document=self.collection.find_one({'_id':game_id},{'outbox':1})
        if document is None: raise KeyError(game_id)
        return [from_jsonable(EventEnvelope,row['envelope']) for row in document['outbox'] if not row['published']]

    def events(self, game_id):
        """복기 review_trace용으로 아웃박스 전체(발행 여부 무관)를 seq 순으로 읽는다. 판 문서를 바꾸지 않는다."""
        document=self.collection.find_one({'_id':game_id},{'outbox':1})
        if document is None: raise KeyError(game_id)
        rows=[from_jsonable(EventEnvelope,row['envelope']) for row in document['outbox']]
        return sorted(rows,key=lambda envelope:envelope.seq)
