"""Cycle timing must not depend on watch/dashboard status polling."""
import copy
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import main
import turn_api


class AttackCycleTimingTest(unittest.TestCase):
    def setUp(self):
        self.state = {}
        def load(path):
            return copy.deepcopy(self.state.get(path, {}))
        def save(path, data, **kwargs):
            self.state[path] = copy.deepcopy(data)
        for module in (main, turn_api):
            for name, replacement in [('load_json_file', load), ('save_json_file', save)]:
                patcher = patch.object(module, name, replacement)
                patcher.start()
                self.addCleanup(patcher.stop)
        for module in (main, turn_api):
            patcher = patch.object(module, 'award_turn_scores')
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = main.app.test_client()

    def initialize(self, count, mode):
        self.state.clear()
        main.save_json_file(main.ASSIGNED_FILE, {f'ip{i}': f'watch{i}' for i in range(1, count + 1)})
        main.save_json_file(main.TURN_FILE, {'current_turn': 'watch1', 'turn_number': 1})
        main.save_json_file(main.GAME_STATUS_FILE, {'running': True})
        main.save_json_file(main.CONTROL_FILE, {'mode': mode})
        main.save_json_file(main.BASELINE_FILE, {f'watch{i}': 70 for i in range(1, count + 1)})
        main.reset_attack_cycle_state()

    def advance(self, endpoint, watch):
        response = self.client.post(endpoint, json={'current_turn': watch})
        self.assertEqual(response.status_code, 200)

    def attack(self):
        return self.client.post('/attack_signal', json={'attacker': 'watch2'})

    def test_reopens_at_first_turn_of_each_cycle_without_polling(self):
        for count in (2, 3):
            for endpoint in ('/set_turn', '/next_turn'):
                for mode in ('attack_challenge', 'attack_challenge_wait'):
                    with self.subTest(count=count, endpoint=endpoint, mode=mode):
                        self.initialize(count, mode)
                        for cycle in range(3):
                            self.assertEqual(self.attack().status_code, 200)
                            self.assertEqual(self.attack().status_code, 409)
                            for i in range(2, count + 1):
                                self.advance(endpoint, f'watch{i}')
                                # Exercise condition updates without resetting cycle history.
                                main.get_attack_challenge_condition()
                                self.assertIn('watch2', main.load_attack_round()['used_attackers'])
                                self.assertEqual(self.attack().status_code, 400 if i == 2 else 409)
                            self.advance(endpoint, 'watch1')
                            self.assertEqual(main.load_attack_round()['used_attackers'], [])
                            self.assertEqual(main.load_attack_round()['seen_turns'], ['watch1'])

    def test_no_polling_or_signal_on_first_turn_still_counts_it(self):
        for endpoint in ('/set_turn', '/next_turn'):
            with self.subTest(endpoint=endpoint):
                self.initialize(3, 'attack_challenge_wait')
                self.advance(endpoint, 'watch2')
                self.advance(endpoint, 'watch3')
                self.assertEqual(self.attack().status_code, 200)
                self.advance(endpoint, 'watch1')
                self.assertEqual(self.attack().status_code, 200)

    def test_repeated_status_reads_do_not_reopen_last_turn(self):
        self.initialize(3, 'attack_challenge_wait')
        self.assertEqual(self.attack().status_code, 200)
        for watch in ('watch2', 'watch3'):
            self.advance('/set_turn', watch)
        for _ in range(3):
            self.assertEqual(self.client.get('/attack_status').status_code, 200)
            self.assertEqual(self.attack().status_code, 409)


if __name__ == '__main__':
    unittest.main()
