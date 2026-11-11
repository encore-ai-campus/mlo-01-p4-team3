"""P-16 Thompson batch, using consented human completed games only.

r = .5*B + .5*S. B(p)=1 on [.4,.6], 0 at p<=.2 or p>=.8,
linear between. S=((fun+(6-unfair)+npc_plausible)/3-1)/4.
Skipped surveys contribute to arm village-win p, never to reward.
alpha += r; beta += 1-r. First five finishes per pseudonym count;
at least 20 NEW response games are required for a published update.
Persist arm aggregates and game IDs only, never a personal model/profile.
"""
import copy
import hashlib
import json
import math
import random
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from itertools import product

from engine.domain.npc.policy_possessed import StrategyMix, LIE_TYPES, FRAME_TARGETS, ANSWER_INTENTS


def balance(p):
    """P-16 village-win balance component, including both flat boundaries."""
    if type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError('invalid village win rate')
    return max(0., min(1., (p - .2) / .2, (.8 - p) / .2))


def reward(p, survey):
    """Return normalized reward, or None for a missing/skipped survey."""
    b = balance(p)
    if survey is None:
        return None
    if type(survey.get('skipped')) is not bool:
        raise ValueError('survey skipped must be boolean')
    scores = [survey.get(k) for k in ('fun', 'unfair', 'npc_plausible')]
    if survey['skipped']:
        if any(v is not None for v in scores):
            raise ValueError('skipped survey scores must be null')
        return None
    if any(type(v) is not int or not 1 <= v <= 5 for v in scores):
        raise ValueError('survey scores must be integers 1..5')
    fun, unfair, plausible = scores
    satisfaction = ((fun + (6 - unfair) + plausible) / 3 - 1) / 4
    return .5 * b + .5 * satisfaction


def _mix(arm, values):
    if not isinstance(arm, str) or not arm or len(arm) > 100:
        raise ValueError('invalid arm ID')
    mix = StrategyMix(**{**values, 'mix_id': arm})
    for name in ('lie_type', 'frame_target', 'answer_intent', 'night_target'):
        if any(type(w) not in (int, float) or not math.isfinite(w) for w in getattr(mix, name).values()):
            raise ValueError('nonfinite strategy weight')
    return asdict(mix)


def initial_state(arms=None):
    """Create uniform priors over named StrategyMix profiles; no user state."""
    if arms is None:
        arms = {f'{lie}:{frame}:{answer}': asdict(StrategyMix(
                    lie_type={lie: 1.}, frame_target={frame: 1.}, answer_intent={answer: 1.}))
                for lie, frame, answer in product(LIE_TYPES, FRAME_TARGETS, ANSWER_INTENTS)}
    if not arms:
        raise ValueError('at least one arm required')
    return dict(_id='bandit', revision=0, arms={a: _mix(a, m) for a, m in sorted(arms.items())},
                posterior={a: dict(alpha=1., beta=1.) for a in sorted(arms)}, versions=[],
                game_fingerprints={}, reward_fingerprints={}, reward_values={})


def _ended(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('timezone required')
    return result


def _eligible(games, arms):
    unique = {}
    for g in games:
        if (not isinstance(g['game_id'], str) or not g['game_id'] or
                not isinstance(g['player_pseudonym'], str) or not g['player_pseudonym'] or
                g['arm'] not in arms or type(g['village_win']) is not bool):
            raise ValueError('invalid game attribution')
        _ended(g['ended_at'])
        reward(.5, g['survey'])
        if g['game_id'] in unique and unique[g['game_id']] != g:
            raise ValueError('conflicting game replay')
        unique[g['game_id']] = g
    counts, selected = Counter(), []
    for g in sorted(unique.values(), key=lambda g: (_ended(g['ended_at']), g['game_id'])):
        user = g['player_pseudonym']
        if counts[user] < 5:
            counts[user] += 1
            selected.append(g)
    return selected


def propose_update(state, games, *, seed=7):
    """Compute one immutable version, or None below 20 new responses.

    Input is a complete, deduplicated, consent-filtered history snapshot, not a
    stream delta. Reject missing/reordered past eligibility rather than silently
    giving a user extra weight. Mongo CAS publishes the whole result atomically.
    """
    selected = _eligible(games, state['arms'])
    known_versions = {'bandit-0'} | {v['version'] for v in state['versions']}
    for g in selected:
        if g.get('bandit_version', 'bandit-0') not in known_versions:
            raise ValueError('unknown pinned bandit version')
        if (g['game_id'] in state['game_fingerprints'] and
                state['game_fingerprints'][g['game_id']] != _fingerprint(g)):
            raise ValueError('committed game attribution changed')
        if (g['game_id'] in state['reward_fingerprints'] and
                state['reward_fingerprints'][g['game_id']] != _survey_fingerprint(g)):
            raise ValueError('committed survey changed')
    selected_ids = {g['game_id'] for g in selected}
    counted = {gid for v in state['versions'] for gid in v['game_ids']}
    rewarded = {gid for v in state['versions'] for gid in v['reward_game_ids']}
    if not counted <= selected_ids:
        raise ValueError('historical eligibility changed; full snapshot required')
    responses = [g for g in selected if g['game_id'] not in rewarded and reward(.5, g['survey']) is not None]
    if len(responses) < 20:
        return None
    updated = copy.deepcopy(state)
    total, wins = Counter(), Counter()
    for g in selected:
        total[g['arm']] += 1
        wins[g['arm']] += g['village_win']
    rates = {a: wins[a] / total[a] for a in sorted(total)}
    for g in responses:
        r = reward(rates[g['arm']], g['survey'])
        post = updated['posterior'][g['arm']]
        post['alpha'] += r
        post['beta'] += 1 - r
        updated['reward_values'][g['game_id']] = dict(arm=g['arm'], reward=r)
        updated['reward_fingerprints'][g['game_id']] = _survey_fingerprint(g)
    updated['game_fingerprints'].update({g['game_id']: _fingerprint(g) for g in selected})
    updated['revision'] += 1
    rng = random.Random(f'{seed}:{updated["revision"]}')
    draws = {a: rng.betavariate(p['alpha'], p['beta']) for a, p in sorted(updated['posterior'].items())}
    total_draw = sum(draws.values())
    version = dict(version=f'bandit-{len(updated["versions"]) + 1}',
                   ratios={a: w / total_draw for a, w in draws.items()},
                   village_win_rates=rates, game_ids=sorted(selected_ids),
                   reward_game_ids=sorted(g['game_id'] for g in responses))
    updated['versions'].append(version)
    return updated


def _fingerprint(game):
    # Keep an integrity digest per game, never a pseudonym-keyed model/profile.
    fields = {k: game[k] for k in ('game_id', 'player_pseudonym', 'ended_at', 'arm', 'village_win')}
    fields['bandit_version'] = game.get('bandit_version', 'bandit-0')
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def _survey_fingerprint(game):
    return hashlib.sha256(json.dumps(game['survey'], sort_keys=True).encode()).hexdigest()


def forget_games(state, game_ids):
    """C-only deletion maintenance: purge IDs/digests and future posterior contributions.

    Published numeric ratios remain pinned for ongoing games, as already trained
    models do under privacy §2. The next 20-response update uses cleaned priors.
    Caller must first block re-ingestion of the deleted participant's events.
    """
    game_ids = set(game_ids)
    if not game_ids.intersection(state['game_fingerprints']):
        return copy.deepcopy(state)
    cleaned = copy.deepcopy(state)
    for gid in game_ids:
        contribution = cleaned['reward_values'].pop(gid, None)
        if contribution:
            posterior = cleaned['posterior'][contribution['arm']]
            posterior['alpha'] = max(1., posterior['alpha'] - contribution['reward'])
            posterior['beta'] = max(1., posterior['beta'] - (1 - contribution['reward']))
        cleaned['game_fingerprints'].pop(gid, None)
        cleaned['reward_fingerprints'].pop(gid, None)
    for version in cleaned['versions']:
        for key in ('game_ids', 'reward_game_ids'):
            version[key] = [gid for gid in version[key] if gid not in game_ids]
    cleaned['revision'] += 1
    return cleaned


def games_from_events(events, consented_users):
    """Join paldo.game/game_ended and paldo.player/survey_submitted by game ID.

    Consent is an explicit pseudonym allowlist from the session owner. Arm is
    model_versions.bandit_arm, stamped at start alongside model_versions.bandit.
    Free text is discarded. Invalid/missing attribution fails closed.
    """
    seen, ends, surveys = {}, {}, {}
    for e in events:
        kind = e.get('event_type')
        if (kind not in ('game_ended', 'survey_submitted') or e.get('source') != 'human' or
                e.get('load_test') is not False or e.get('player_pseudonym') not in consented_users):
            continue
        expected = 'paldo.game' if kind == 'game_ended' else 'paldo.player'
        if e.get('topic') != expected or (kind == 'game_ended' and e.get('phase') != 'ended'):
            continue
        # Only fields needed for reward; comments and private/free text never persist.
        payload = e['payload']
        if kind == 'game_ended' and payload.get('completed', True) is False:
            continue  # 중단 판(11/10 사용자 결정)은 승패가 없어 보상 계산에 쓰지 않는다.
        g = dict(game_id=e['game_id'], player_pseudonym=e['player_pseudonym'],
                 ended_at=e['occurred_at'], arm=e['model_versions']['bandit_arm'],
                 bandit_version=e['model_versions']['bandit'])
        if kind == 'game_ended':
            # 엔진 game_ended의 승자 값은 Faction 값 'village'·'changgwi'다(pipeline/spark와 같은 집합).
            if payload['winner'] not in ('village', 'changgwi'):
                raise ValueError('invalid winner')
            g['village_win'] = payload['winner'] == 'village'
        else:
            g['survey'] = {k: payload[k] for k in ('skipped', 'fun', 'unfair', 'npc_plausible')}
            reward(.5, g['survey'])
        event_id = e['event_id']
        if event_id in seen and seen[event_id] != g:
            raise ValueError('conflicting event replay')
        seen[event_id] = g
        target = ends if kind == 'game_ended' else surveys
        previous = target.get(g['game_id'])
        if previous is not None and previous != g:
            raise ValueError('conflicting game event')
        target[g['game_id']] = g
    result = []
    for gid, end in sorted(ends.items()):
        survey = surveys.get(gid)
        if survey and any(survey[k] != end[k] for k in ('player_pseudonym', 'arm', 'bandit_version')):
            raise ValueError('survey attribution mismatch')
        result.append(dict(end, survey=survey['survey'] if survey else None))
    return result


class VersionedMixSource:
    """Read one pinned snapshot, choose a profile deterministically per game ID."""
    def __init__(self, state, version):
        if version == 'bandit-0':
            ratios = {a: 1 / len(state['arms']) for a in state['arms']}
        else:
            ratios = next(v['ratios'] for v in state['versions'] if v['version'] == version)
        self.version, self.ratios = version, copy.deepcopy(ratios)
        self.arms = copy.deepcopy(state['arms'])

    def mix_for(self, game_id):
        """Return a fresh StrategyMix; policy caches it for the duration of the game."""
        digest = hashlib.sha256(f'{self.version}:{game_id}'.encode()).digest()
        arm = random.Random(digest).choices(sorted(self.ratios),
                  weights=[self.ratios[a] for a in sorted(self.ratios)])[0]
        return StrategyMix(**copy.deepcopy(self.arms[arm]))
