"""P-16 reward, cap, event attribution, retry and frozen engine mix checks."""
import copy
import unittest
from datetime import datetime, timedelta, timezone

from ml import bandit
from dataclasses import asdict
from engine.domain.npc.policy_possessed import StrategyMix


def uniform_state():
    return bandit.initial_state({'uniform': asdict(StrategyMix())})


def games(n=20):
    start = datetime(2026, 11, 9, tzinfo=timezone.utc)
    return [dict(game_id=f'g{i:03}', player_pseudonym=f'u{i}', ended_at=(start + timedelta(seconds=i)).isoformat(),
                 arm='uniform', village_win=i % 2 == 0,
                 survey=dict(skipped=False, fun=5, unfair=1, npc_plausible=5)) for i in range(n)]


class BanditTests(unittest.TestCase):
    def test_default_portfolio_covers_all_three_strategy_dimensions(self):
        state = bandit.initial_state()
        profiles = list(state['arms'].values())
        self.assertEqual(len(profiles), 18)
        self.assertEqual({tuple(p['lie_type']) for p in profiles}, {('unrefuted',), ('routine',)})
        self.assertEqual({tuple(p['answer_intent']) for p in profiles}, {('explain',), ('evade',), ('counter',)})
        self.assertEqual({tuple(p['frame_target']) for p in profiles}, {('contradicted',), ('suspected',), ('mudang',)})
    def test_balance_reward_endpoints_and_linear_segments(self):
        for p, expected in ((0, 0), (.2, 0), (.3, .5), (.4, 1), (.5, 1), (.6, 1), (.7, .5), (.8, 0), (1, 0)):
            self.assertAlmostEqual(bandit.balance(p), expected)
        self.assertEqual(bandit.reward(.5, dict(skipped=False, fun=5, unfair=1, npc_plausible=5)), 1)
        self.assertEqual(bandit.reward(.2, dict(skipped=False, fun=1, unfair=5, npc_plausible=1)), 0)
        self.assertIsNone(bandit.reward(.5, dict(skipped=True, fun=None, unfair=None, npc_plausible=None)))
        for value in (0, 6, True, float('nan')):
            with self.assertRaises(ValueError):
                bandit.reward(.5, dict(skipped=False, fun=value, unfair=1, npc_plausible=5))

    def test_19_no_update_20_update_and_retry_is_unchanged(self):
        state = uniform_state()
        self.assertIsNone(bandit.propose_update(state, games(19)))
        updated = bandit.propose_update(state, games())
        self.assertEqual(updated['revision'], 1)
        self.assertEqual(updated['posterior']['uniform'], dict(alpha=21., beta=1.))
        self.assertEqual(len(updated['versions'][0]['reward_game_ids']), 20)
        self.assertEqual(updated['versions'][0]['ratios'], {'uniform': 1.})
        self.assertIsNone(bandit.propose_update(updated, games() * 2))
        self.assertEqual(state['revision'], 0)

    def test_first_five_ended_not_input_order_and_skipped_counts_only_in_p(self):
        rows = games(21)
        for row in rows[:6]:
            row['player_pseudonym'] = 'repeat-user'
        # First five survive; sixth response cannot make the 20-response minimum.
        rows[0]['survey'] = dict(skipped=True, fun=None, unfair=None, npc_plausible=None)
        self.assertIsNone(bandit.propose_update(uniform_state(), list(reversed(rows))))
        rows += games(22)[21:]
        updated = bandit.propose_update(uniform_state(), list(reversed(rows)))
        version = updated['versions'][-1]
        self.assertEqual(len(version['game_ids']), 21)
        self.assertEqual(len(version['reward_game_ids']), 20)
        self.assertNotIn('g005', version['game_ids'])
        self.assertIn('g000', version['game_ids'])
        self.assertNotIn('g000', version['reward_game_ids'])
        self.assertAlmostEqual(version['village_win_rates']['uniform'], 11 / 21)
        self.assertNotIn('repeat-user', str(updated))

    def test_late_history_cannot_replace_already_counted_game(self):
        rows = games()
        state = bandit.propose_update(uniform_state(), rows)
        with self.assertRaises(ValueError):
            bandit.propose_update(state, rows[1:])

    def test_committed_attribution_and_reward_are_immutable_across_batches(self):
        initial = bandit.propose_update(uniform_state(), games())
        for key, value in (('player_pseudonym', 'other'), ('ended_at', '2026-11-09T12:00:00Z'),
                           ('village_win', False), ('bandit_version', 'unknown')):
            rows = games(40)
            rows[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                bandit.propose_update(initial, rows)
        rows = games(40)
        rows[0]['survey']['fun'] = 1
        with self.assertRaises(ValueError):
            bandit.propose_update(initial, rows)

    def test_late_survey_can_join_already_counted_game_once(self):
        rows = games(21)
        rows[0]['survey'] = None
        initial = bandit.propose_update(uniform_state(), rows)
        updated = bandit.propose_update(initial, games(40))
        self.assertEqual(len(updated['versions'][-1]['reward_game_ids']), 20)
        self.assertIn('g000', updated['versions'][-1]['reward_game_ids'])
        self.assertIsNone(bandit.propose_update(updated, games(40)))

    def test_privacy_purge_removes_audit_ids_and_reward_contributions_for_future_updates(self):
        initial = bandit.propose_update(uniform_state(), games())
        purged = bandit.forget_games(initial, {'g000'})
        self.assertNotIn('g000', str(purged))
        self.assertEqual(purged['posterior']['uniform'], {'alpha': 20., 'beta': 1.})
        self.assertEqual(purged['versions'][0]['ratios'], initial['versions'][0]['ratios'])
        self.assertEqual(bandit.forget_games(purged, {'g000'}), purged)
        updated = bandit.propose_update(purged, games(40)[1:])
        self.assertEqual(updated['versions'][-1]['version'], 'bandit-2')
        self.assertEqual(updated['posterior']['uniform'], {'alpha': 40., 'beta': 1.})

    def test_thompson_ratios_and_version_source_are_reproducible(self):
        from engine.domain.npc.policy_possessed import StrategyMix, PossessedPolicy
        from dataclasses import asdict
        state = bandit.initial_state({'uniform': asdict(StrategyMix()),
                                     'routine': asdict(StrategyMix(lie_type={'routine': 1}, mix_id='routine'))})
        updated = bandit.propose_update(state, games())
        self.assertEqual(updated, bandit.propose_update(state, list(reversed(games()))))
        snapshot = updated['versions'][-1]
        self.assertAlmostEqual(sum(snapshot['ratios'].values()), 1.)
        source = bandit.VersionedMixSource(updated, snapshot['version'])
        a = source.mix_for('new-game')
        self.assertEqual(a, source.mix_for('new-game'))
        policy = PossessedPolicy(mix_source=source)
        self.assertEqual(policy.mix('new-game'), a)
        updated['arms'].clear()
        self.assertEqual(source.mix_for('new-game'), a)

    def test_events_join_only_consented_human_completed_nonload_games(self):
        events = []
        for row in games():
            common = dict(game_id=row['game_id'], player_pseudonym=row['player_pseudonym'], source='human',
                          load_test=False, model_versions={'bandit': 'bandit-0', 'bandit_arm': 'uniform'})
            events += [dict(common, event_id=row['game_id']+':1', topic='paldo.game', event_type='game_ended',
                            phase='ended', occurred_at=row['ended_at'], payload={'winner': 'village'}),
                       dict(common, event_id=row['game_id']+':2', topic='paldo.player', event_type='survey_submitted',
                            occurred_at=row['ended_at'], payload=row['survey'])]
        consent = {r['player_pseudonym'] for r in games()}
        for i in (0, 2):
            events[i]['load_test'] = True
        events[4]['source'] = 'scripted'
        events[6]['phase'] = 'round1'
        consent.remove('u4')
        joined = bandit.games_from_events(events + events, consent)
        self.assertEqual(len(joined), 15)
        self.assertNotIn('g000', {g['game_id'] for g in joined})
        conflict = copy.deepcopy(events[-1])
        conflict['payload']['fun'] = 1
        with self.assertRaises(ValueError):
            bandit.games_from_events(events + [conflict], consent)


    def test_aborted_games_are_not_rewarded(self):
        """새로 시작으로 중단한 판(completed=false, winner=null)은 오류 없이 보상 조인에서 빠진다(11/10 사용자 결정)."""
        common = dict(game_id='g-aborted', player_pseudonym='u1', source='human', load_test=False,
                      model_versions={'bandit': 'bandit-0', 'bandit_arm': 'uniform'}, occurred_at='2026-11-10T05:00:00+00:00')
        events = [dict(common, event_id='g-aborted:1', topic='paldo.game', event_type='game_ended', phase='ended',
                       payload={'winner': None, 'reason': 'aborted', 'completed': False}),
                  dict(common, event_id='g-aborted:2', topic='paldo.player', event_type='survey_submitted',
                       payload={'skipped': True, 'fun': None, 'unfair': None, 'npc_plausible': None})]
        self.assertEqual(bandit.games_from_events(events, {'u1'}), [])

if __name__ == '__main__':
    unittest.main()
