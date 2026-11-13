"""Regression tests for human holdout, deletion and lifecycle boundaries."""
import unittest


class RetrainDemoTests(unittest.TestCase):
    def test_deleted_and_automatic_rows_cannot_reach_training(self):
        from contracts.datasets import IntentExample, LabelSource, Split, SplitKey
        from intent.retrain import training_rows

        def row(key, user, source):
            return IntentExample(key, key, 'silence', 'assertion', {}, source, Split.TRAIN,
                                 SplitKey(user, 'game', '2026-11-10'))

        rows = [row('gone', 'deleted', LabelSource.HUMAN_ANNOTATION),
                row('auto', 'present', LabelSource.AUTO_EXECUTED),
                row('edited', 'present', LabelSource.PLAYER_EDITED)]
        kept = training_rows(rows, {'deleted'}, set(), set())
        self.assertEqual([r.example_id for r in kept], ['edited'])
        with self.assertRaisesRegex(ValueError, 'overlap'):
            training_rows(rows, {'deleted'}, {'present'}, set())

    def test_canary_waits_for_both_groups_and_holds_for_win_drift(self):
        from ml.lifecycle_demo import canary_decision

        self.assertEqual(canary_decision(30, 29, .5, .5, 0, 99), 'wait')
        self.assertEqual(canary_decision(30, 30, .61, .5, 0, 99), 'hold')
        self.assertEqual(canary_decision(30, 30, .6, .5, .05, 100), 'promote')
        self.assertEqual(canary_decision(1, 1, .5, .5, .06, 99), 'rollback')

    def test_vote_parameter_is_selected_on_training_examples(self):
        from ml.suspicion.player_calibration import fit_vote_weight, vote_target

        samples = [('b', {'a': .4, 'b': .6}, {'a': 1., 'b': 0.})] * 3
        weight = fit_vote_weight(samples)
        self.assertEqual(vote_target(samples[0][1], samples[0][2], 0), 'a')
        self.assertEqual(vote_target(samples[0][1], samples[0][2], weight), 'b')

    def test_vote_calibration_excludes_incomplete_and_late_completed_games(self):
        from ml.suspicion.player_calibration import vote_samples

        def game(user, ended):
            events = [
                dict(event_id=user + '-0', seq=0, event_type='game_started',
                     occurred_at='2026-11-10T04:00:00Z', payload={}),
                dict(event_id=user + '-1', seq=1, event_type='notebook_updated',
                     occurred_at='2026-11-10T04:00:01Z', payload={'entries': {'a': 20, 'b': 80}}),
                dict(event_id=user + '-2', seq=2, event_type='votes_revealed',
                     occurred_at='2026-11-10T04:00:02Z',
                     payload={'round': 'main', 'votes': {'mudang': 'b', 'a': 'b', 'b': 'a'},
                              'runoff_candidates': []}),
            ]
            if ended:
                events.append(dict(event_id=user + '-3', seq=3, event_type='game_ended',
                                   occurred_at=ended, payload={}))
            return dict(player_pseudonym=user, source='human', events=events)

        games = [game('finished', '2026-11-10T04:00:03Z'), game('interrupted', None),
                 game('late', '2026-11-10T09:00:01Z')]
        samples = vote_samples(games, {'finished', 'interrupted', 'late'}, set())
        self.assertEqual(dict(samples), {'finished': [('b', {'a': .2, 'b': .8}, {})]})
