import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main


def test_repeat_down_uses_turn_start_heartbeats_minus_five():
    main.save_json_file(main.BASELINE_FILE, {"watch1": 70.0})
    main.save_json_file(main.ATTACK_CONDITION_FILE, {
        "turn_start_heartbeats": {"watch1": 80.0}
    })

    condition = {
        "direction": "down",
        "previous_direction": "down",
        "first_turn": False,
        "turn_start_heartbeats": {"watch1": 80.0},
    }

    assert main.attack_threshold("watch1", condition) == 75.0
