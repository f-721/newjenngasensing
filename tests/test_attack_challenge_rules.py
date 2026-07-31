import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main


def test_repeat_down_uses_turn_start_heartbeats_minus_three(monkeypatch, tmp_path):
    baseline_file = tmp_path / "baseline.json"
    monkeypatch.setattr(main, "BASELINE_FILE", str(baseline_file))
    main.save_json_file(main.BASELINE_FILE, {"watch1": 70.0})

    condition = {
        "direction": "down",
        "previous_direction": "down",
        "first_turn": False,
        "experienced_attackers": ["watch1"],
        "turn_start_heartbeats": {"watch1": 80.0},
    }

    assert main.attack_threshold("watch1", condition) == 77.0


def test_completed_attack_cycle_keeps_condition_for_next_turn(monkeypatch, tmp_path):
    paths = {
        "ATTACK_TARGETS_FILE": tmp_path / "attack_targets.json",
        "ATTACK_PENDING_FILE": tmp_path / "attack_pending.json",
        "ATTACK_SUCCESS_FILE": tmp_path / "attack_success.json",
        "ATTACK_ROUND_FILE": tmp_path / "attack_round.json",
        "ATTACK_CONDITION_FILE": tmp_path / "attack_condition.json",
    }
    for name, path in paths.items():
        monkeypatch.setattr(main, name, str(path))

    previous_condition = {
        "turn": "watch2",
        "direction": "up",
        "previous_direction": "down",
        "first_turn": False,
        "turn_start_heartbeats": {"watch1": 70.0, "watch2": 71.0},
    }
    main.save_json_file(main.ATTACK_CONDITION_FILE, previous_condition, log=False)
    main.save_attack_round({
        "used_attackers": ["watch2"],
        "seen_turns": ["watch1", "watch2"],
        "last_turn": "watch2",
        "completed": True,
    })

    main.update_attack_round_for_turn("watch1", {"watch1", "watch2"})

    assert main.load_json_file(main.ATTACK_CONDITION_FILE) == previous_condition


def test_repeat_up_uses_turn_start_heartbeat(monkeypatch, tmp_path):
    baseline_file = tmp_path / "baseline.json"
    monkeypatch.setattr(main, "BASELINE_FILE", str(baseline_file))
    main.save_json_file(main.BASELINE_FILE, {"watch2": 78.0})
    condition = {
        "direction": "up",
        "previous_direction": "up",
        "first_turn": False,
        "experienced_attackers": ["watch2"],
        "turn_start_heartbeats": {"watch2": 71.0},
    }

    assert main.attack_threshold("watch2", condition) == 81.0


def test_watch_first_attack_uses_baseline_even_after_first_game_turn(monkeypatch, tmp_path):
    baseline_file = tmp_path / "baseline.json"
    monkeypatch.setattr(main, "BASELINE_FILE", str(baseline_file))
    main.save_json_file(main.BASELINE_FILE, {"watch2": 78.0})
    condition = {
        "direction": "up",
        "previous_direction": "up",
        "first_turn": False,
        "experienced_attackers": ["watch1"],
        "turn_start_heartbeats": {"watch2": 71.0},
    }

    assert main.attack_threshold("watch2", condition) == 88.0


def test_attack_references_are_selected_per_watch(monkeypatch, tmp_path):
    baseline_file = tmp_path / "baseline.json"
    monkeypatch.setattr(main, "BASELINE_FILE", str(baseline_file))
    main.save_json_file(main.BASELINE_FILE, {"watch2": 78.0, "watch3": 76.0})
    condition = {
        "experienced_attackers": ["watch2"],
        "turn_start_heartbeats": {"watch2": 71.0, "watch3": 73.0},
    }

    assert main.attack_reference("watch2", condition) == (71.0, "turn_start")
    assert main.attack_reference("watch3", condition) == (76.0, "baseline")


def test_down_after_up_uses_turn_start_heartbeat(monkeypatch, tmp_path):
    baseline_file = tmp_path / "baseline.json"
    monkeypatch.setattr(main, "BASELINE_FILE", str(baseline_file))
    main.save_json_file(main.BASELINE_FILE, {"watch2": 78.0})
    condition = {
        "direction": "down",
        "previous_direction": "up",
        "experienced_attackers": ["watch2"],
        "turn_start_heartbeats": {"watch2": 71.0},
    }

    assert main.attack_threshold("watch2", condition) == 51.0
