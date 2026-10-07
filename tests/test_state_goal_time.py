import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import main


class StateGoalTimeTests(unittest.TestCase):
    def test_directional_threshold_time_and_boundary(self):
        for direction, heartbeat, expected in [
            ('up', 95, 1000), ('up', 80, 1000), ('up', 79, 0),
            ('down', 65, 1000), ('down', 80, 1000), ('down', 81, 0),
        ]:
            with self.subTest(direction=direction, heartbeat=heartbeat):
                success = {'watch1': {'turn': 'watch2', 'direction': direction,
                                     'threshold': 80, 'quota_keep_ms': 0,
                                     'last_state_sample_timestamp': 99000}}
                with patch.object(main, 'load_json_file', return_value={'running': True}), \
                     patch.object(main, 'get_attack_challenge_condition', return_value={'turn': 'watch2'}), \
                     patch.object(main, 'load_attack_pending', return_value={}), \
                     patch.object(main, 'load_attack_targets', return_value={}), \
                     patch.object(main, 'load_attack_success', return_value=success), \
                     patch.object(main, 'get_latest_heartbeats', return_value={'watch1': heartbeat}), \
                     patch.object(main, 'update_attack_state_event'), \
                     patch.object(main, 'save_attack_success'), \
                     patch.object(main.time, 'time', return_value=100):
                    main.resolve_attack_challenge()
                    self.assertEqual(success['watch1']['quota_keep_ms'], expected)
                    main.resolve_attack_challenge()
                    self.assertEqual(success['watch1']['quota_keep_ms'], expected)

    def test_stopped_game_does_not_accumulate_time(self):
        with patch.object(main, 'load_json_file', return_value={}), \
             patch.object(main, 'load_attack_pending', return_value={}), \
             patch.object(main, 'load_attack_targets', return_value={}), \
             patch.object(main, 'update_attack_state_event') as update, \
             patch.object(main, 'get_attack_challenge_condition') as condition:
            main.resolve_attack_challenge()
            update.assert_not_called()
            condition.assert_not_called()


if __name__ == '__main__':
    unittest.main()
