"""D-07-02: 실제 엔진 관측을 누수 없는 TrainingRow로 변환하는 경계.

실행: python -m ml.suspicion.features --events .../events.jsonl --out out/suspicion/data
진실은 완료 판의 관찰자 필터와 라벨에만 쓰며 특징 추출 함수에 전달하지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from contracts.datasets import SplitKey, TrainingRow
from engine.domain import serde
from engine.domain.events import EventEnvelope
from engine.domain.model import PLAYER_ID, PRESETS, EvidenceGroup, ObservationSnapshot, Role
from engine.domain.npc.beliefs import vote_conflicts
from tools.perf.npc_rule_check import legal_problem
from tools.gitrepo import GitRepo
from tools.jsonlog import log

FEATURE_VERSION = 'suspicion-1'
FEATURE_NAMES = ('rule_score', 'day', 'n_candidates', 'trust_in_mudang') + tuple(
    'reason_' + g.value for g in EvidenceGroup)


def candidate_features(obs: ObservationSnapshot, candidate: str) -> dict[str, float]:
    """허용된 자기 믿음만 수치화한다. 역할·진실·ID는 특징에 넣지 않는다."""
    if obs.private.role is not Role.VILLAGER or candidate not in obs.candidates:
        raise ValueError('마을 NPC 관찰자와 스냅샷 후보만 허용')
    if candidate in (PLAYER_ID, obs.observer):
        raise ValueError('무당·관찰자는 후보가 아님')
    f = dict(rule_score=obs.beliefs.suspicion.get(candidate, 50.0) / 25.0,
             day=float(obs.day), n_candidates=float(len(obs.candidates)),
             trust_in_mudang=obs.beliefs.trust_in_mudang / 100.0)
    for group in EvidenceGroup:
        f['reason_' + group.value] = sum(r.delta for r in obs.beliefs.reasons
                                       if r.target == candidate and r.group is group) / 25.0
    return f


def policy_signature(event: dict) -> str:
    """주민 수·ID와 무관하게 판의 정책 버전 집합을 식별한다."""
    versions = set(event['npc_policy_versions'].values())
    if not versions or len(versions) != 1:
        raise ValueError('단일 NPC 정책 버전만 허용')
    return next(iter(versions))


def engine_rows(events: list[dict]) -> tuple[list[TrainingRow], dict]:
    """완료 판만 변환한다. 정책 혼합·미래 진실은 거부하고 위반을 별도 집계한다."""
    games = defaultdict(list)
    policies = set()
    for raw in events:
        env = serde.from_jsonable(EventEnvelope, raw)
        if env.source.value != 'bot' or env.load_test:
            raise ValueError('로컬 성능 개발에는 실제 bot 판만 허용')
        policies.add(policy_signature(raw))
        games[env.game_id].append(raw)
    if len(policies) != 1:
        raise ValueError('정책 버전이 다른 판을 섞을 수 없음')
    audit = _new_audit(len(games))
    rows = []
    for gid, es in games.items():
        rows.extend(_game_rows(gid, es, audit, 'bot', None, contiguous=True))
    if rows:
        validate_rows(rows)
    return rows, audit


def _new_audit(games: int) -> dict:
    return dict(games=games, completed_games=0, excluded_incomplete_games=0,
                observations=0, town_observations=0, decisions_checked=0,
                town_decisions_checked=0, town_votes_checked=0,
                legal_action_violations=0, town_inference_violations=0, knowledge_boundary_violations=0)


def _game_rows(gid: str, es: list[dict], audit: dict, source: str, user: str | None,
               contiguous: bool) -> list[TrainingRow]:
    """한 판의 이벤트를 학습 행으로 바꾼다(봇·사람 공용 검사).

    contiguous=False는 학습용 내보내기(관측·결정·종료·진실만)라 seq 빈틈을 허용하되,
    증가 순서와 '결정 바로 앞 seq가 그 관측'이라는 조건은 그대로 요구한다.
    """
    rows = []
    seqs = [e['seq'] for e in es]
    if contiguous and seqs != list(range(len(es))):
        raise ValueError('판별 seq 중복·누락·역순')
    if not contiguous and (len(set(seqs)) != len(seqs) or seqs != sorted(seqs)):
        raise ValueError('판별 seq 중복·역순')
    previous = {e['seq']: e for e in es}
    if True:
        times = [datetime.fromisoformat(e['occurred_at'].replace('Z', '+00:00')) for e in es]
        if any(t.tzinfo is None for t in times) or times != sorted(times):
            raise ValueError('관측·종료·진실 시각 역순/시간대 누락')
        ends = [e for e in es if e['event_type'] == 'game_ended']
        truths = [e for e in es if e['event_type'] == 'truth_revealed']
        if not truths:
            audit['excluded_incomplete_games'] += 1
            return rows
        if (len(ends) != 1 or len(truths) != 1 or truths[0] is not es[-1]
                or truths[0]['seq'] != ends[0]['seq'] + 1
                or truths[0]['phase'] != 'ended' or truths[0]['topic'] != 'paldo.truth'):
            raise ValueError('진실은 종료 직후 마지막 이벤트여야 함')
        # 현 엔진 finish는 completed 필드를 발행하지 않는다. 종료+진실 쌍이 완료 증거다.
        if ends[0]['payload'].get('completed', True) is not True:
            audit['excluded_incomplete_games'] += 1
            return rows
        roles = truths[0]['payload']['roles']
        if (set(roles) != set(es[0]['npc_policy_versions']) | {PLAYER_ID}
                or roles[PLAYER_ID] != Role.MUDANG.value
                or not set(roles.values()) <= {r.value for r in Role}):
            raise ValueError('종료 역할 매핑 불일치')
        audit['completed_games'] += 1
        observations = {}
        roster = set(es[0]['npc_policy_versions'])
        for e in es:
            if e['event_type'] == 'observation':
                obs = serde.from_jsonable(ObservationSnapshot, e['payload']['snapshot'])
                if (obs.game_id != gid or obs.observer != e['payload']['observer']
                        or obs.snapshot_id != e['payload']['snapshot_id']
                        or obs.preset_id != e['preset_id'] or obs.snapshot_id in observations
                        or e['seq'] >= ends[0]['seq']):
                    raise ValueError('관측 식별/시점 불일치')
                if roles.get(obs.observer) != obs.private.role.value:
                    raise ValueError('관측 역할과 종료 역할 불일치')
                observations[obs.snapshot_id] = obs
                audit['observations'] += 1
                if obs.private.role is not Role.VILLAGER:
                    continue
                audit['town_observations'] += 1
                preset = PRESETS[obs.preset_id]
                if preset.n_possessed != 1 or obs.n_possessed_remaining != 1:
                    raise ValueError('D-07-02는 창귀 1명 판만 지원')
                inactive = {r.locked for r in obs.public.locks} | {r.victim for r in obs.public.disappearances}
                excluded = inactive | {r.npc_id for r in obs.public.confessions} | {obs.observer, PLAYER_ID}
                expected_candidates = sorted(roster - excluded)
                if (not obs.candidates or obs.candidates != expected_candidates
                        or obs.candidates != sorted(set(obs.candidates))
                        or set(obs.candidates) & {obs.observer, PLAYER_ID}
                        or not set(obs.candidates) <= set(roles)):
                    raise ValueError('불법 후보 집합')
                for c in obs.candidates:
                    f = candidate_features(obs, c)
                    rows.append(TrainingRow(
                        f'{obs.snapshot_id}:{c}', gid, obs.snapshot_id, obs.observer, c,
                        obs.day, obs.phase.value, obs.preset_id, preset.n_active,
                        preset.n_possessed, obs.n_possessed_remaining, len(obs.candidates),
                        FEATURE_VERSION, f, source, SplitKey(user, gid, e['occurred_at'][:10]),
                        int(roles[c] == Role.POSSESSED.value), truths[0]['occurred_at']))
            elif e['event_type'] == 'decision':
                p = e['payload']
                obs = observations.get(p['snapshot_id'])
                before = previous.get(e['seq'] - 1)
                if (obs is None or obs.observer != p['actor'] or e['seq'] == 0 or before is None
                        or before['event_type'] != 'observation'
                        or before['payload']['snapshot_id'] != p['snapshot_id']):
                    raise ValueError('결정의 앞 관측 누락')
                version = e['npc_policy_versions'][p['actor']]
                if p['policy_version'] not in (version, version + '-fallback'):
                    raise ValueError('결정 정책과 봉투 정책 불일치')
                audit['decisions_checked'] += 1
                active = (roster | {PLAYER_ID}) - {r.locked for r in obs.public.locks} - {r.victim for r in obs.public.disappearances}
                abstained = p['kind'] == 'vote' and p['target'] is None   # 기권(rule-0.9~)
                invalid_target = p['kind'] in ('vote', 'suspect', 'defend') and not abstained and p['target'] not in active
                audit['legal_action_violations'] += int(invalid_target or legal_problem(obs, p['kind'], p['target'], p['actor']) is not None)
                if obs.private.role is Role.VILLAGER:
                    audit['town_decisions_checked'] += 1
                    codes = p['reason_codes']
                    # 좁은 정적 감사: 씐 자 전용 이유 코드·개인 지식 오염을 분리 집계한다.
                    forbidden = any(c == 'avoid_partner' or c.startswith(('frame_', 'lie_', 'night_')) for c in codes)
                    # 비밀을 숨기는 마을 NPC도 자기 거짓 주장 ID는 안다.
                    polluted = bool(obs.private.partner_id or obs.private.night_plans)
                    audit['knowledge_boundary_violations'] += int(forbidden or polluted)
                    if p['kind'] == 'vote':
                        audit['town_votes_checked'] += 1
                        audit['town_inference_violations'] += int(vote_conflicts(obs.beliefs, p['target']))
    return rows


def human_rows(games: list[dict], consent: set[str], deleted: set[str]) -> tuple[list[TrainingRow], dict]:
    """학습용 내보내기(`ut_export training`)의 사람 판을 학습 행으로 바꾼다.

    동의한 가명만 쓰고 삭제 요청 가명·부하 시험·봇 판은 뺀다. 운영 자료라 한 판이 검사에 걸려도
    밤 재학습 전체를 멈추지 않고 그 판만 사유별로 뺀다. 정책 버전은 판마다 하나여야 하고 판끼리는 달라도 된다.
    `results`는 쓴 판의 실제 승자(시험 세트 마을 승률 계산용).
    """
    audit = dict(games=len(games), used_games=0, excluded={}, policy_versions={}, results={})
    rows = []

    def skip(reason):
        audit['excluded'][reason] = audit['excluded'].get(reason, 0) + 1

    for game in games:
        user, es = game.get('player_pseudonym'), game.get('events') or []
        if not isinstance(user, str) or not user:
            skip('no_pseudonym')
            continue
        if user in deleted:
            skip('deletion_request')
            continue
        if user not in consent:
            skip('no_consent')
            continue
        if any(e.get('source') != 'human' for e in es):
            skip('not_human')
            continue
        if any(e.get('load_test') for e in es):
            skip('load_test')
            continue
        try:
            if not es or any(e.get('game_id') != game.get('game_id') for e in es):
                raise ValueError('판 ID 불일치')
            for raw in es:
                serde.from_jsonable(EventEnvelope, raw)
            version = policy_signature(es[0])
            if any(policy_signature(e) != version for e in es):
                raise ValueError('판 안 정책 버전 혼합')
            game_audit = _new_audit(1)
            converted = _game_rows(game['game_id'], es, game_audit, 'human', user, contiguous=False)
            if converted:
                validate_rows(converted)
        except (ValueError, KeyError, TypeError, AttributeError):
            skip('invalid')
            continue
        if not converted:
            skip('incomplete' if game_audit['excluded_incomplete_games'] else 'no_town_rows')
            continue
        winner = next(e['payload'].get('winner') for e in es if e['event_type'] == 'game_ended')
        audit['used_games'] += 1
        audit['policy_versions'][version] = audit['policy_versions'].get(version, 0) + 1
        audit['results'][game['game_id']] = winner
        rows.extend(converted)
    if rows:
        validate_rows(rows)
    return rows, audit


def validate_rows(rows: list[TrainingRow]) -> None:
    """C export에도 적용하는 특징·라벨·후보 완전성 검사. 봇 행과 사람 행(가명 필수)만 받고 합성 입력은 거부."""
    if not rows:
        raise ValueError('학습 행 없음')
    groups = defaultdict(list)
    ids = set()
    for r in rows:
        if (r.row_id in ids or not all((r.game_id, r.snapshot_id, r.observer, r.candidate))
                or r.source not in ('bot', 'human') or r.split_key.game_id != r.game_id
                or (r.source == 'bot') != (r.split_key.user is None)
                or (r.source == 'human' and not (isinstance(r.split_key.user, str) and r.split_key.user))
                or r.feature_version != FEATURE_VERSION or set(r.features) != set(FEATURE_NAMES)
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                           for v in r.features.values())
                or r.candidate in (PLAYER_ID, r.observer) or r.observer == PLAYER_ID
                or r.label not in (0, 1) or not r.label_joined_at
                or r.n_possessed != 1 or r.n_possessed_remaining != 1):
            raise ValueError('TrainingRow 경계 위반')
        ids.add(r.row_id)
        groups[(r.game_id, r.snapshot_id)].append(r)
    for group in groups.values():
        first = group[0]
        if (len(group) != first.n_candidates or len({r.candidate for r in group}) != len(group)
                or sum(r.label for r in group) != 1
                or any((r.observer, r.n_candidates, r.preset_id, r.day, r.phase) !=
                       (first.observer, first.n_candidates, first.preset_id, first.day, first.phase) for r in group)):
            raise ValueError('스냅샷 후보 누락/중복 또는 라벨 합 불일치')


def sha256(path: Path) -> str:
    """파일의 SHA256(대용량 JSONL도 스트리밍)."""
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def load_rows(path: Path) -> list[TrainingRow]:
    """JSONL을 계약 타입으로 엄격하게 읽고 검증한다."""
    with path.open(encoding='utf-8') as f:
        rows = [serde.from_jsonable(TrainingRow, json.loads(line)) for line in f if line.strip()]
    validate_rows(rows)
    return rows


def delta_rows(observed: list[TrainingRow], exported: list[dict]) -> list[TrainingRow]:
    """C export의 후보·라벨을 원본 이벤트와 대조하고 D 특징만 다시 계산한다."""
    by_id = {row.row_id: row for row in observed}
    rows = []
    for raw in exported:
        raw = {**raw, 'split_key': {'user': None, **raw['split_key']}}
        row = serde.from_jsonable(TrainingRow, raw)
        expected = by_id.pop(row.row_id, None)
        if expected is None or row.feature_version != 'public-snapshot-v1':
            raise ValueError('Delta 행 ID 중복·불일치 또는 특징 버전 불일치')
        aligned = replace(row, feature_version=FEATURE_VERSION, features=expected.features)
        if aligned != replace(expected, n_active=row.n_active):
            raise ValueError('Delta 행의 원본 관측·라벨 불일치')
        rows.append(aligned)
    if by_id:
        raise ValueError('Delta export 행 누락')
    validate_rows(rows)
    return rows


def main(argv=None) -> int:
    """실제 엔진 JSONL 또는 원본과 대조한 Delta 행을 학습 입력으로 내보낸다."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--events', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--delta-export', type=Path)
    ap.add_argument('--delta-version', type=int)
    a = ap.parse_args(argv)
    if (a.delta_export is None) != (a.delta_version is None) or (a.delta_version is not None and a.delta_version < 0):
        ap.error('--delta-export와 0 이상의 --delta-version을 함께 지정하세요')
    if a.out.exists():
        raise ValueError('새 출력 경로를 사용하세요')
    log('suspicion_features', 'info', '변환 시작', input=str(a.events))
    with a.events.open(encoding='utf-8') as f:
        events = [json.loads(line) for line in f if line.strip()]
    rows, audit = engine_rows(events)
    input_hash = sha256(a.events)
    provenance = {}
    if a.delta_export is not None:
        parts = sorted(a.delta_export.glob('part-*.json'))
        if not parts or not (a.delta_export / '_SUCCESS').is_file():
            raise ValueError('완료된 Spark JSON export가 필요합니다')
        exported = []
        hashes = {}
        for part in parts:
            data = part.read_bytes()
            hashes[part.name] = hashlib.sha256(data).hexdigest()
            exported.extend(json.loads(line) for line in data.splitlines() if line.strip())
        rows = delta_rows(rows, exported)
        provenance = dict(delta_version=a.delta_version, delta_export_hashes=hashes,
                          source_feature_version='public-snapshot-v1')
    validate_rows(rows)
    a.out.mkdir(parents=True)
    path = a.out / 'rows.jsonl'
    with path.open('w', encoding='utf-8', newline='\n') as f:
        for row in rows:
            f.write(serde.dumps(row) + '\n')
    data_version = ('engine-jsonl:' + input_hash if a.delta_version is None
                    else f'delta:c07-01-silver@{a.delta_version}')
    manifest = dict(data_version=data_version, input_sha256=input_hash,
                    rows_sha256=sha256(path), feature_version=FEATURE_VERSION,
                    code_commit=GitRepo(Path(__file__).resolve().parents[2]).head(),
                    npc_policy_version=policy_signature(events[0]),
                    seeds=sorted({e['seed'] for e in events}),
                    rule_versions=sorted({e['rule_version'] for e in events}),
                    content_versions=sorted({e['content_version'] for e in events}),
                    observer_filter='completed_truth_villager', candidate_filter='snapshot_excludes_mudang',
                    heldout_strategy=False, audit=audit, **provenance)
    (a.out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    log('suspicion_features', 'info', '변환 완료', rows=len(rows), **audit)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
