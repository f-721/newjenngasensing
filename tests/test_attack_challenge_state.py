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

    def test_attack_signal_wait_mode_uses_attack_challenge_flow(self):
        main.reset_attack_cycle_state()
        main.save_json_file(main.ASSIGNED_FILE, {"ip1": "watch1", "ip2": "watch2"}, log=False)
        main.save_json_file(main.TURN_FILE, {"current_turn": "watch1", "turn_number": 1}, log=False)
        main.save_json_file(main.CONTROL_FILE, {"mode": "attack_challenge_wait"}, log=False)
        main.save_json_file(main.BASELINE_FILE, {"watch1": 70.0, "watch2": 60.0}, log=False)
        main.save_json_file(main.ATTACK_CONDITION_FILE, {
            "turn": "watch1",
            "direction": "up",
            "previous_direction": None,
            "first_turn": True,
            "experienced_attackers": [],
            "attackers_this_turn": [],
            "turn_start_heartbeats": {"watch1": 70.0, "watch2": 60.0},
        }, log=False)
        main.save_json_file(main.DATA_FILE, {"watch1": 65.0, "watch2": 90.0}, log=False)
        main.save_attack_round({"used_attackers": [], "seen_turns": [], "last_turn": None, "completed": False})

        response = main.app.test_client().post("/attack_signal", json={"attacker": "watch2", "target": "watch1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "pending")


if __name__ == "__main__":
    unittest.main()
