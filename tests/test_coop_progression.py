import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import main
from heart_api import trim_heart_samples


class CoopProgressionTests(unittest.TestCase):
    def test_stop_next_set_and_final_stop_preserve_team_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            names = ('GAME_STATUS_FILE', 'CONTROL_FILE', 'COOP_FILE', 'ASSIGNED_FILE',
                     'BASELINE_FILE', 'TURN_FILE', 'ROTATION_STATUS_FILE', 'CSV_HISTORY_FILE')
            paths = {name: os.path.join(directory, name + '.json') for name in names}
            with patch.multiple(main, **paths), patch.object(main, 'reset_attack_cycle_state'):
                watches = [f'watch{i}' for i in range(1, 5)]
                def save(name, value):
                    main.save_json_file(paths[name], value, log=False)
                save('CONTROL_FILE', {'mode': 'team_coop'})
                save('GAME_STATUS_FILE', {'running': True})
                save('ASSIGNED_FILE', dict(zip(watches, watches)))
                save('BASELINE_FILE', dict.fromkeys(watches, 70))
                state = main.initialize_coop_game(watches, 2)
                state['team_scores']['team_a'] = 1
                main.save_coop_state(state)
                client = main.app.test_client()
                self.assertTrue(client.post('/stop').get_json()['set_finished'])
                self.assertFalse(client.post('/stop').get_json()['set_finished'])
                response = client.post('/next_jenga_game')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()['game_number'], 2)
                self.assertEqual(client.get('/coop_status').get_json()['game_number'], 2)
                self.assertTrue(client.post('/stop').get_json()['series_complete'])
                self.assertEqual(client.post('/next_jenga_game').status_code, 409)
                state = main.load_coop_state()
                self.assertEqual(state['team_scores'], {'team_a': 1, 'team_b': 0})
                self.assertEqual(state['winner'], 'team_a')
                self.assertEqual(len(state['set_history']), 2)

    def test_support_phases_are_drawn_per_turn_and_latch_success(self):
        state = {
            'settings': dict(main.COOP_DEFAULTS),
            'teams': {'team_a': ['watch1', 'watch2'], 'team_b': ['watch3', 'watch4']},
            'turn_state': {},
        }
        turn = {'current_turn': 'watch1', 'turn_number': 1}
        def load(path):
            return turn if path == main.TURN_FILE else {'watch2': 70}
        with patch.object(main, 'load_json_file', side_effect=load), \
             patch.object(main, 'save_coop_state') as save, \
             patch.object(main.random, 'choice', side_effect=['up', 'down', 'up']) as choose, \
             patch.object(main, 'get_latest_heartbeats', return_value={'watch2': 79}) as hearts:
            main.update_coop_turn_state(state)
            self.assertEqual(state['turn_state']['phase'], 'up')
            self.assertEqual(state['turn_state']['threshold'], 80)
            self.assertFalse(state['turn_state']['success'])
            hearts.return_value = {'watch2': 80}
            main.update_coop_turn_state(state)
            self.assertTrue(state['turn_state']['success'])
            turn['turn_number'] = 2
            hearts.return_value = {'watch2': 68}
            main.update_coop_turn_state(state)
            self.assertEqual(state['turn_state']['phase'], 'down')
            self.assertEqual(state['turn_state']['threshold'], 67)
            self.assertFalse(state['turn_state']['success'])
            hearts.return_value = {'watch2': 67}
            main.update_coop_turn_state(state)
            self.assertTrue(state['turn_state']['success'])
            self.assertEqual(state['turn_state']['rpm'], 10)
            hearts.return_value = {'watch2': 75}
            main.update_coop_turn_state(state)
            self.assertTrue(state['turn_state']['success'])
            save.reset_mock()
            main.update_coop_turn_state(state)
            save.assert_not_called()
            self.assertEqual(choose.call_count, 2)
            turn['turn_number'] = 3
            main.update_coop_turn_state(state)
            self.assertEqual(state['turn_state']['phase'], 'up')
            self.assertFalse(state['turn_state']['success'])
            self.assertEqual(choose.call_count, 3)

    def test_live_samples_are_bounded_without_losing_baseline_window(self):
        samples = [{'timestamp': i * 1000, 'heartbeat': 70} for i in range(301)]
        result = trim_heart_samples({'watch1': samples}, 300_000)['watch1']
        self.assertEqual(result[0]['timestamp'], 180_000)
        self.assertEqual(result[-1]['timestamp'], 300_000)
        self.assertEqual(len([r for r in result if r['timestamp'] >= 290_000]), 11)


if __name__ == '__main__':
    unittest.main()
