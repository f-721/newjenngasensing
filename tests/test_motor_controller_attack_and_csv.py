import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main
import motor_controller


class MotorControllerAttackAndCsvTest(unittest.TestCase):
    def test_apply_attack_effect_uses_challenge_details(self):
        attack_status = {
            "attack_mode": True,
            "challenge_direction": "up",
            "pending_attackers": ["watch2", "watch3"],
            "attackers": ["watch2"],
        }
        rpm, direction, attackers = motor_controller.apply_attack_effect(
            "watch1", 20, "a", attack_status=attack_status
        )
        self.assertGreater(rpm, 20)
        self.assertEqual(direction, "c")
        self.assertEqual(attackers, ["watch2"])

    def test_record_csv_snapshot_writes_live_csv_and_attack_details(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            main.CSV_HISTORY_FILE = os.path.join(tmpdir, "csv_history.json")
            main.LIVE_CSV_FILE = os.path.join(tmpdir, "live.csv")
            main.DATA_FILE = os.path.join(tmpdir, "heart_rates.json")
            main.BASELINE_FILE = os.path.join(tmpdir, "baseline.json")
            main.TURN_FILE = os.path.join(tmpdir, "turn.json")
            main.GAME_STATUS_FILE = os.path.join(tmpdir, "game_status.json")

            main.save_json_file(main.GAME_STATUS_FILE, {"running": True})
            main.save_json_file(main.DATA_FILE, {"watch1": [{"heartbeat": 80, "timestamp": 1}]})
            main.save_json_file(main.BASELINE_FILE, {"watch1": 70})
            main.save_json_file(main.TURN_FILE, {"current_turn": "watch1"})

            main.record_csv_snapshot(
                "watch1",
                "attack_challenge",
                25,
                "c",
                extreme="up",
                attackers=["watch2"],
                attack_context={
                    "attack_mode": True,
                    "challenge_direction": "up",
                    "pending_attackers": ["watch2"],
                    "attack_count": 1,
                },
            )

            history = main.load_csv_history()
            self.assertTrue(history)
            row = history[-1]
            self.assertEqual(row["attackers"], "watch2")
            self.assertEqual(row["attack_count"], 1)
            self.assertEqual(row["attack_mode"], "attack_challenge")
            self.assertEqual(row["challenge_direction"], "up")

            with open(main.LIVE_CSV_FILE, encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("attackers", content)
            self.assertIn("watch2", content)


if __name__ == "__main__":
    unittest.main()
