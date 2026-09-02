import os
import sys
import unittest

from flask import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main
import motor_controller


class AttackRoundAndFallbackTest(unittest.TestCase):
    def test_attacker_is_blocked_until_cycle_completes(self):
        main.save_attack_round({
            "used_attackers": ["watch2"],
            "seen_turns": ["watch1"],
            "last_turn": "watch1",
            "completed": False,
        })
        self.assertFalse(main.should_allow_attack("watch2", "watch2", {"watch1", "watch2", "watch3"}))
        self.assertTrue(main.should_allow_attack("watch3", "watch2", {"watch1", "watch2", "watch3"}))

    def test_watch1_does_not_reopen_attack_until_cycle_completes(self):
        main.save_attack_round({
            "used_attackers": ["watch2"],
            "seen_turns": ["watch1"],
            "last_turn": "watch3",
            "completed": False,
        })
        self.assertFalse(main.should_allow_attack("watch2", "watch1", {"watch1", "watch2", "watch3"}))
        self.assertTrue(main.should_allow_attack("watch3", "watch1", {"watch1", "watch2", "watch3"}))

    def test_challenge_condition_does_not_reset_cycle_on_first_watch(self):
        main.save_json_file(main.TURN_FILE, {"current_turn": "watch1"}, log=False)
        main.save_json_file(main.ASSIGNED_FILE, {"ip1": "watch1", "ip2": "watch2", "ip3": "watch3"}, log=False)
        main.save_json_file(main.ATTACK_CONDITION_FILE, {}, log=False)
        main.save_attack_round({
            "used_attackers": ["watch2"],
            "seen_turns": ["watch1"],
            "last_turn": "watch3",
            "completed": False,
        })

        original_reset = main.reset_attack_cycle_state
        reset_called = False

        def fake_reset():
            nonlocal reset_called
            reset_called = True

        main.reset_attack_cycle_state = fake_reset
        try:
            main.get_attack_challenge_condition()
        finally:
            main.reset_attack_cycle_state = original_reset

        self.assertFalse(reset_called)

    def test_set_turn_keeps_attack_round_history(self):
        main.save_json_file(main.GAME_STATUS_FILE, {"running": True, "game_over": False}, log=False)
        main.save_json_file(main.ASSIGNED_FILE, {"ip1": "watch1", "ip2": "watch2", "ip3": "watch3"}, log=False)
        main.save_attack_round({
            "used_attackers": ["watch2"],
            "seen_turns": ["watch1"],
            "last_turn": "watch1",
            "completed": False,
        })

        client = main.app.test_client()
        response = client.post('/set_turn', json={"current_turn": "watch2"})

        self.assertEqual(response.status_code, 200)
        state = main.load_attack_round()
        self.assertEqual(state.get("used_attackers"), ["watch2"])
        self.assertEqual(state.get("last_turn"), "watch1")

    def test_no_attack_fallback_rotation_is_fixed(self):
        motor_controller.no_attack_fallback_until = 0.0
        rpm, direction, active = motor_controller.get_no_attack_fallback_rotation(now=100.0)
        self.assertEqual((rpm, direction), (20, "c"))
        self.assertTrue(active)


if __name__ == "__main__":
    unittest.main()
