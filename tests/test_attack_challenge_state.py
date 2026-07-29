import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main


class AttackChallengeStateTest(unittest.TestCase):
    def test_turn_change_resets_attack_state(self):
        main.save_json_file(main.ATTACK_CONDITION_FILE, {"turn": "watch1", "direction": "up"}, log=False)
        main.save_json_file(main.ATTACK_TARGETS_FILE, {"watch2": "watch1"}, log=False)
        main.save_json_file(main.ATTACK_PENDING_FILE, {"watch2": {"target": "watch1", "turn": "watch1"}}, log=False)
        main.save_json_file(main.TURN_FILE, {"current_turn": "watch2"}, log=False)

        condition = main.get_attack_challenge_condition()

        self.assertEqual(condition.get("turn"), "watch2")
        self.assertEqual(main.load_attack_targets(), {})
        self.assertEqual(main.load_attack_pending(), {})

    def test_load_json_file_returns_empty_for_invalid_json(self):
        with open(main.DATA_FILE, "w", encoding="utf-8") as handle:
            handle.write('{"broken": ')

        self.assertEqual(main.load_json_file(main.DATA_FILE), {})


if __name__ == "__main__":
    unittest.main()
