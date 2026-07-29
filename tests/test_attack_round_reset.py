import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main


class AttackRoundResetTest(unittest.TestCase):
    def test_reset_attack_cycle_clears_round_history(self):
        main.save_attack_round({"used_attackers": ["watch2"], "seen_turns": ["watch1"], "last_turn": "watch1", "completed": True})
        main.reset_attack_cycle_state()
        state = main.load_attack_round()
        self.assertEqual(state.get("used_attackers"), [])
        self.assertEqual(state.get("seen_turns"), [])
        self.assertFalse(state.get("completed", True))

    def test_game_state_clear_accepts_reset_round_state(self):
        main.save_json_file(main.GAME_STATUS_FILE, {"running": False, "game_over": False}, log=False)
        main.save_json_file(main.CSV_HISTORY_FILE, [], log=False)
        main.save_json_file(main.ATTACK_TARGETS_FILE, {}, log=False)
        main.save_json_file(main.ATTACK_PENDING_FILE, {}, log=False)
        main.save_json_file(main.ATTACK_SUCCESS_FILE, {}, log=False)
        main.save_json_file(main.ATTACK_CONDITION_FILE, {}, log=False)
        main.save_attack_round({"used_attackers": [], "seen_turns": [], "last_turn": None, "completed": False})
        self.assertTrue(main.is_game_state_clear())


if __name__ == "__main__":
    unittest.main()
