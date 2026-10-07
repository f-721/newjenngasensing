import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import main


class ChallengePhaseLimitTests(unittest.TestCase):
    def test_both_directions_switch_after_three(self):
        for direction, opposite in [('up', 'down'), ('down', 'up')]:
            with patch.object(main.random, 'choice', return_value=direction):
                previous, count = None, 0
                phases = []
                for _ in range(8):
                    previous, count = main.choose_challenge_phase(previous, count)
                    phases.append(previous)
                self.assertEqual(phases, [direction] * 3 + [opposite] + [direction] * 3 + [opposite])

    def test_direction_change_resets_streak(self):
        with patch.object(main.random, 'choice', return_value='down'):
            self.assertEqual(main.choose_challenge_phase('up', 2), ('down', 1))

    def test_attack_polling_does_not_increment_streak(self):
        state = {main.TURN_FILE: {'current_turn': 'watch1', 'turn_number': 1},
                 main.ATTACK_CONDITION_FILE: {}}
        def load(path):
            return state.get(path, {}).copy()
        def save(path, data, **kwargs):
            state[path] = data.copy()
        with patch.object(main, 'load_json_file', side_effect=load), \
             patch.object(main, 'save_json_file', side_effect=save), \
             patch.object(main, 'reset_attack_cycle_state'), \
             patch.object(main, 'get_latest_heartbeats', return_value={}), \
             patch.object(main.random, 'choice', return_value='up'):
            for number in range(1, 5):
                state[main.TURN_FILE] = {'current_turn': f'watch{number}', 'turn_number': number}
                first = main.get_attack_challenge_condition()
                for _ in range(5):
                    self.assertEqual(main.get_attack_challenge_condition(), first)
                self.assertEqual(first['direction'], 'up' if number < 4 else 'down')
                self.assertEqual(first['phase_streak'], number if number < 4 else 1)

    def test_coop_polling_does_not_increment_streak(self):
        state = {'teams': {'team_a': ['watch1', 'watch2']}, 'settings': {}, 'turn_state': {}}
        with patch.object(main, 'load_json_file', return_value={'current_turn': 'watch1', 'turn_number': 1}) as load, \
             patch.object(main, 'get_latest_heartbeats', return_value={}), \
             patch.object(main, 'save_coop_state'), \
             patch.object(main.random, 'choice', return_value='down'):
            for number in range(1, 5):
                load.return_value = {'current_turn': 'watch1', 'turn_number': number}
                main.update_coop_turn_state(state)
                first = state['turn_state'].copy()
                main.update_coop_turn_state(state)
                self.assertEqual(state['turn_state'], first)
                self.assertEqual(first['phase'], 'down' if number < 4 else 'up')


if __name__ == '__main__':
    unittest.main()
