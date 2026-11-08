"""공식 EventEnvelope를 bronze 행으로 검증한다. 엔진 계약을 다시 정의하지 않는다."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from typing import Mapping

from engine.domain.events import EventEnvelope, EventType, topic_of
from engine.domain.model import CONTRACT_VERSION, DataSource
from engine.domain.serde import SchemaError, from_jsonable, to_jsonable

GAME_TOPICS = ('paldo.game', 'paldo.observations', 'paldo.intent', 'paldo.llm',
               'paldo.player', 'paldo.truth')
AD_TOPICS=('ad.serve','ad.impression','ad.click','ad.rejudge')
ALL_TOPICS=GAME_TOPICS+AD_TOPICS


class BronzeContractError(ValueError):
    """원문을 로그에 노출하지 않는 수신 계약 오류 코드."""


def canonical(value):
    """키 순서와 무관한 JSON을 만든다. 비유한 숫자는 허용하지 않는다."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BronzeContractError('duplicate_json_key')
        result[key] = value
    return result


def _constant(_value):
    raise BronzeContractError('nonfinite_json')


def normalize(raw: str, *, topic: str, key: str | None, partition: int, offset: int) -> dict:
    """Kafka 전송 정보와 공식 봉투를 검증해 멱등 MERGE 행을 반환한다.

    모르는 이벤트 유형도 공식 게임 토픽의 원본으로 보존한다. 모르는 계약
    버전·잘못된 라우팅·종료 전 진실은 실패시켜 offset을 조용히 건너뛰지 않는다.
    """
    if topic in AD_TOPICS:
        return normalize_ad(raw,topic=topic,key=key,partition=partition,offset=offset)
    try:
        if len(raw.encode('utf-8')) > 1024 * 1024:
            raise BronzeContractError('event_too_large')
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
        envelope = from_jsonable(EventEnvelope, value)
        if envelope.contract_version != CONTRACT_VERSION:
            raise BronzeContractError('contract_version')
        if not envelope.game_id or envelope.seq < 0 or envelope.event_id != f'{envelope.game_id}:{envelope.seq:05d}':
            raise BronzeContractError('event_id')
        if envelope.state_version < 0 or envelope.day < 0 or not envelope.event_type:
            raise BronzeContractError('event_metadata')
        if topic not in GAME_TOPICS or envelope.topic != topic or key != envelope.game_id:
            raise BronzeContractError('topic_or_key')
        try:
            known_type = EventType(envelope.event_type)
        except ValueError:
            known_type = None
        if known_type is not None and topic_of(known_type) != topic:
            raise BronzeContractError('event_topic')
        if topic == 'paldo.truth' and envelope.phase != 'ended':
            raise BronzeContractError('truth_before_end')
        timestamp = datetime.fromisoformat(envelope.occurred_at.replace('Z', '+00:00'))
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise BronzeContractError('occurred_at_utc')
        if envelope.source is DataSource.HUMAN:
            if not envelope.player_pseudonym or not envelope.session_id:
                raise BronzeContractError('human_identity_missing')
        elif envelope.player_pseudonym is not None or envelope.session_id is not None:
            raise BronzeContractError('nonhuman_identity')
        if type(partition) is not int or partition < 0 or type(offset) is not int or offset < 0:
            raise BronzeContractError('transport_position')
        body = canonical(to_jsonable(envelope))
        return {'event_id': envelope.event_id, 'game_id': envelope.game_id, 'seq': envelope.seq,
                'event_type': envelope.event_type, 'topic': topic, 'source': envelope.source.value,
                'load_test': envelope.load_test, 'contract_version': envelope.contract_version,
                'occurred_at': envelope.occurred_at, 'envelope_json': body,
                'envelope_hash': hashlib.sha256(body.encode('utf-8')).hexdigest(),
                'partition': partition, 'offset': offset}
    except BronzeContractError:
        raise
    except (SchemaError, TypeError, ValueError, AttributeError, RecursionError, UnicodeError):
        raise BronzeContractError('invalid_envelope') from None


def normalize_ad(raw,*,topic,key,partition,offset):
    """광고 고유 DTO를 검증해 같은 bronze 스키마에 원본을 보존한다."""
    from datetime import timezone
    from ads.domain.events import AdServeEvent,AdImpressionEvent,AdClickEvent,AdRejudgeEvent
    from ads.domain.model import AD_CONTRACT_VERSION
    classes=dict(zip(AD_TOPICS,(AdServeEvent,AdImpressionEvent,AdClickEvent,AdRejudgeEvent)))
    try:
        if len(raw.encode('utf-8'))>1024*1024:raise ValueError('event_too_large')
        value=json.loads(raw,object_pairs_hook=_object,parse_constant=_constant)
        event=from_jsonable(classes[topic],value)
        expected=event.charge_id if topic=='ad.rejudge' else event.serve_id
        if event.contract_version!=AD_CONTRACT_VERSION or not event.event_id or key!=expected:
            raise ValueError('ad_contract_or_key')
        if event.source not in ('human','synthetic') or event.occurred_at_ms<0:raise ValueError('ad_metadata')
        if type(partition) is not int or partition<0 or type(offset) is not int or offset<0:raise ValueError('position')
        body=canonical(to_jsonable(event))
        return {'event_id':event.event_id,'game_id':None,'seq':None,'event_type':topic,
            'topic':topic,'source':event.source,'load_test':getattr(event,'load_test',False),'contract_version':event.contract_version,
            'occurred_at':datetime.fromtimestamp(event.occurred_at_ms/1000,timezone.utc).isoformat(),
            'envelope_json':body,'envelope_hash':hashlib.sha256(body.encode()).hexdigest(),'partition':partition,'offset':offset}
    except (SchemaError,ValueError,TypeError,KeyError,OverflowError):
        raise BronzeContractError('invalid_ad_envelope') from None


def unique_rows(rows: list[dict], existing: Mapping[str, str] | None = None) -> list[dict]:
    """같은 ID의 내용 충돌은 실패시키고, 동일 재전송·기적재 행은 제거한다."""
    existing = existing or {}
    selected = {}
    for row in sorted(rows, key=lambda r: (r['topic'], r['partition'], r['offset'])):
        identity, digest = row['event_id'], row['envelope_hash']
        previous = selected.get(identity)
        if previous and previous['envelope_hash'] != digest:
            raise BronzeContractError('event_id_conflict_in_batch')
        if identity in existing:
            if existing[identity] != digest:
                raise BronzeContractError('event_id_conflict_existing')
            continue
        selected.setdefault(identity, row)
    return list(selected.values())
