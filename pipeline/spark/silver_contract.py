"""진실 없이 관찰자별 학습 행을 만들고, 종료 뒤에만 라벨을 조인한다."""
from dataclasses import asdict
import json
from contracts.datasets import SplitKey,TrainingRow
from engine.domain.model import ObservationSnapshot,PRESETS
from engine.domain.serde import from_jsonable

FEATURE_VERSION='public-snapshot-v1'


def observation_rows(envelope):
    """전체 snapshot만 엄격 변환한다. 특징은 공개 기록과 관찰자 믿음에서만 계산한다."""
    if envelope['event_type']!='observation':return []
    snapshot=from_jsonable(ObservationSnapshot,envelope['payload']['snapshot'])
    if snapshot.game_id!=envelope['game_id'] or snapshot.snapshot_id!=envelope['payload']['snapshot_id']:
        raise ValueError('snapshot_identity_mismatch')
    preset=PRESETS[snapshot.preset_id]
    removed=sum(lock.locked is not None for lock in snapshot.public.locks)
    # 0일째 피해자는 처음 활동 인원에 포함되지 않는다.
    removed+=sum(item.night>0 for item in snapshot.public.disappearances)
    active=max(1,preset.n_active-removed)
    result=[]
    for candidate in snapshot.candidates:
        features={'prior_suspicion':float(snapshot.beliefs.suspicion.get(candidate,0)),
            'day':float(snapshot.day),'n_candidates':float(len(snapshot.candidates)),
            'claims_public':float(len(snapshot.public.claims)),
            'speeches_public':float(len(snapshot.public.speeches)),
            'tells_public':float(len(snapshot.public.tells))}
        row=TrainingRow(snapshot.snapshot_id+':'+candidate,snapshot.game_id,snapshot.snapshot_id,
            snapshot.observer,candidate,snapshot.day,snapshot.phase.value,snapshot.preset_id,active,
            preset.n_possessed,snapshot.n_possessed_remaining,len(snapshot.candidates),FEATURE_VERSION,
            features,envelope['source'],SplitKey(envelope.get('player_pseudonym'),snapshot.game_id,envelope['occurred_at'][:10]))
        record=asdict(row)
        record.update(load_test=envelope.get('load_test',False),observer_role=snapshot.private.role.value,
            snapshot_json=json.dumps(envelope['payload']['snapshot'],ensure_ascii=False,sort_keys=True))
        result.append(record)
    return result


def completed_truths(envelopes):
    """배열/단위 검증용 라벨 기준. 정상 종료 뒤 truth만 사용하고 중단·미종료는 제외한다."""
    ended={};truths={}
    for e in envelopes:
        if e['event_type']=='game_ended':
            p=e['payload']
            if p.get('completed',True) and p.get('winner') in ('village','changgwi') and p.get('reason')!='aborted':ended[e['game_id']]=e['seq']
        elif e['event_type']=='truth_revealed' and e['phase']=='ended':truths[e['game_id']]=e
    return {g:t for g,t in truths.items() if g in ended and t['seq']>ended[g]}


def label_rows(rows,envelopes):
    """행의 특징을 변경하지 않고 완료된 판의 후보 역할만 정답으로 붙인다."""
    truths=completed_truths(envelopes);result=[]
    for row in rows:
        item=dict(row);truth=truths.get(row['game_id'])
        if truth:
            role=truth['payload']['roles'][row['candidate']]
            item.update(label=int(role=='possessed'),label_joined_at=truth['occurred_at'])
        result.append(item)
    return result
