import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import main
import turn_api


class CollapseWatchSelectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        paths = {name: os.path.join(directory.name, name + '.json')
                 for name in vars(main) if name.isupper() and name.endswith('_FILE')}
        patcher = patch.multiple(main, **paths)
        patcher.start()
        self.addCleanup(patcher.stop)
        resolver = patch.object(main, 'resolve_attack_challenge')
        resolver.start()
        self.addCleanup(resolver.stop)
        turn_paths = {name: paths[name] for name in vars(turn_api)
                      if name.isupper() and name.endswith('_FILE') and name in paths}
        turn_patcher = patch.multiple(turn_api, **turn_paths)
        turn_patcher.start()
        self.addCleanup(turn_patcher.stop)
        self.client = main.app.test_client()
        self.watches = ['watch1', 'watch2', 'watch3', 'watch4']
        self.save('GAME_STATUS_FILE', {'running': True})
        self.save('TURN_FILE', {'current_turn': 'watch2', 'turn_number': 2})
        self.save('CONTROL_FILE', {'mode': 'self_fast'})
        self.save('ASSIGNED_FILE', dict(zip(self.watches, self.watches)))

    def save(self, name, value):
        main.save_json_file(getattr(main, name), value, log=False)

    def test_previous_watch_receives_penalty_and_csv_keeps_actual_turn(self):
        response = self.client.post('/collapse', json={'watch_id': 'watch1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['watch_id'], 'watch1')
        self.assertEqual(main.load_scores(), {'watch1': -3})
        self.assertFalse(main.load_json_file(main.GAME_STATUS_FILE)['running'])
        self.assertEqual(self.client.post('/next_turn').status_code, 409)
        row = main.load_csv_history()[-1]
        self.assertEqual(row['device_id'], 'watch1')
        self.assertEqual(row['current_turn'], 'watch2')
        self.assertEqual(main.load_json_file(main.TURN_FILE)['current_turn'], 'watch2')

    def test_default_uses_current_turn(self):
        response = self.client.post('/collapse', json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['watch_id'], 'watch2')

    def test_invalid_watch_cannot_change_scores_or_finish_game(self):
        for watch in ['watch99', ['watch1'], 123]:
            with self.subTest(watch=watch):
                response = self.client.post('/collapse', json={'watch_id': watch})
                self.assertEqual(response.status_code, 400)
                self.assertTrue(main.load_json_file(main.GAME_STATUS_FILE)['running'])
                self.assertEqual(main.load_scores(), {})
                self.assertEqual(main.load_csv_history(), [])

    def test_series_records_selected_watch_and_awards_other_survivors(self):
        main.initialize_jenga_series(self.watches, 2, 'success')
        response = self.client.post('/collapse', json={'watch_id': 'watch1'})
        self.assertEqual(response.status_code, 200)
        result = response.get_json()['set_result']
        self.assertEqual(result['collapsed_player'], 'watch1')
        scores = response.get_json()['scores']
        self.assertEqual(scores['watch1']['survival_score'], 0)
        self.assertEqual(scores['watch2']['survival_score'], 1)

    def test_coop_awards_opposite_team_of_selected_watch(self):
        self.save('CONTROL_FILE', {'mode': 'team_coop'})
        self.save('COOP_FILE', {
            'active': True, 'game_number': 1, 'total_sets': 2,
            'teams': {'team_a': ['watch1', 'watch3'], 'team_b': ['watch2', 'watch4']},
            'team_scores': {'team_a': 0, 'team_b': 0}, 'set_history': []
        })
        response = self.client.post('/collapse', json={'watch_id': 'watch1'})
        self.assertEqual(response.status_code, 200)
        coop = response.get_json()['coop']
        self.assertEqual(coop['team_scores'], {'team_a': 0, 'team_b': 1})
        self.assertEqual(coop['set_history'][-1]['collapsed_player'], 'watch1')

    def test_watch_signals_after_collapse_cannot_change_turn_scores_or_attack_state(self):
        for mode in ['self_fast', 'attack_challenge', 'team_coop']:
            with self.subTest(mode=mode):
                self.save('GAME_STATUS_FILE', {'running': True})
                self.save('CONTROL_FILE', {'mode': mode})
                if mode == 'team_coop':
                    main.initialize_coop_game(self.watches, 2)
                else:
                    main.initialize_jenga_series(self.watches, 2, 'success')
                self.assertEqual(self.client.post('/collapse', json={'watch_id': 'watch1'}).status_code, 200)
                paths = [getattr(main, name) for name in vars(main)
                         if name.isupper() and name.endswith('_FILE')]
                def snapshot():
                    from pathlib import Path
                    return {path: Path(path).read_bytes() if os.path.exists(path) else None for path in paths}
                before = snapshot()
                for endpoint, payload in [('/next_turn', {}),
                                          ('/set_turn', {'current_turn': 'watch3'}),
                                          ('/attack_signal', {'attacker': 'watch1'}),
                                          ('/set_attack_target', {'attacker': 'watch1', 'target': 'watch2'})]:
                    response = self.client.post(endpoint, json=payload)
                    self.assertIn(response.status_code, (400, 409))
                    self.assertEqual(snapshot(), before)

    def test_next_turn_resumes_when_game_is_running(self):
        self.save('GAME_STATUS_FILE', {'running': False})
        self.assertEqual(self.client.post('/next_turn').status_code, 409)
        self.save('GAME_STATUS_FILE', {'running': True})
        response = self.client.post('/next_turn')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['next_turn'], 'watch3')
        self.assertEqual(response.get_json()['turn_number'], 3)


    def test_state_time_snapshot_updates_on_turn_change_and_bonus_is_per_set(self):
        self.save('CONTROL_FILE', {'mode': 'attack_challenge'})
        main.initialize_jenga_series(self.watches, 2, 'state')
        series = main.load_jenga_series()
        series['current_set_events'] = [
            {'attacker': 'watch1', 'turn_number': 1, 'quota_keep_ms': 4000},
            {'attacker': 'watch3', 'turn_number': 2, 'quota_keep_ms': 7000},
        ]
        series['attack_events'] = list(series['current_set_events'])
        main.save_jenga_series(series)
        ranking = self.client.get('/jenga_series').get_json()['state_ranking']
        self.assertEqual({r['watch_id']: r['quota_keep_ms'] for r in ranking}['watch3'], 0)
        self.save('TURN_FILE', {'current_turn': 'watch3', 'turn_number': 3})
        ranking = self.client.get('/jenga_series').get_json()['state_ranking']
        self.assertEqual(ranking[0]['watch_id'], 'watch3')
        self.assertEqual(ranking[0]['quota_keep_ms'], 7000)
        response = self.client.post('/collapse', json={'watch_id': 'watch1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['scores']['watch3']['ranking_bonus'], 1)
        self.assertFalse(response.get_json()['series_complete'])
        result = response.get_json()['set_result']
        self.assertEqual(result['state_bonus_winners'], ['watch3'])
        self.assertEqual(result['set_points']['watch3']['ranking'], 1)



if __name__ == '__main__':
    unittest.main()
