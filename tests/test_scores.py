import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main
import heart_api
from score_logic import challenge_successful_watch_ids, scoring_targets, turn_scoring_targets


def configure_files(monkeypatch, tmp_path):
    names = {
        "SCORES_FILE": "scores.json",
        "ROTATION_STATUS_FILE": "rotation_status.json",
        "TURN_FILE": "turn.json",
        "ASSIGNED_FILE": "assigned_ids.json",
        "GAME_STATUS_FILE": "game_status.json",
        "CSV_HISTORY_FILE": "csv_history.json",
        "CONTROL_FILE": "control_mode.json",
        "ATTACK_SUCCESS_FILE": "attack_success.json",
        "ATTACK_PENDING_FILE": "attack_pending.json",
        "ATTACK_TARGETS_FILE": "attack_targets.json",
        "ATTACK_CONDITION_FILE": "attack_condition.json",
        "BASELINE_FILE": "baseline.json",
        "DATA_FILE": "heart_rates.json",
    }
    for attribute, filename in names.items():
        monkeypatch.setattr(main, attribute, str(tmp_path / filename))


def test_only_up_down_modes_produce_unique_scoring_targets():
    assert scoring_targets({
        "motor1": {"mode": "highest_diff", "target_watch": "watch2"},
        "motor2": {"mode": "random_diff", "target_watch": "watch2"},
        "motor3": {"mode": "lowest_diff", "target_watch": "watch3"},
        "motor4": {"mode": "self_fast", "target_watch": "watch4"},
    }) == {"watch2", "watch3"}


def test_scoring_uses_only_the_ending_turn_motor_status():
    status = {
        "watch1": {"mode": "random_diff", "target_watch": "watch2"},
        "watch2": {"mode": "random_diff", "target_watch": "watch1"},
    }
    assert scoring_targets(status, "watch1") == {"watch2"}
    assert scoring_targets(status, "watch2") == {"watch1"}


def test_challenge_targets_only_include_successes_from_ending_turn():
    successes = {
        "watch2": {"turn": "watch1", "target": "watch1"},
        "watch3": {"turn": "watch4", "target": "watch4"},
    }
    assert challenge_successful_watch_ids(successes, "watch1") == {"watch2"}
    assert turn_scoring_targets("attack_challenge", {}, successes, "watch1") == {"watch2"}


def test_turn_change_adds_one_point_to_used_watch(monkeypatch, tmp_path):
    configure_files(monkeypatch, tmp_path)
    main.save_json_file(main.GAME_STATUS_FILE, {"running": True}, log=False)
    main.save_json_file(main.ASSIGNED_FILE, {"ip1": "watch1", "ip2": "watch2"}, log=False)
    main.save_json_file(main.TURN_FILE, {"current_turn": "watch1", "turn_number": 1}, log=False)
    main.save_json_file(main.SCORES_FILE, {"watch1": 0, "watch2": 0}, log=False)
    main.save_json_file(main.CONTROL_FILE, {"mode": "highest_diff"}, log=False)
    main.save_json_file(main.ROTATION_STATUS_FILE, {
        "motor1": {"mode": "highest_diff", "target_watch": "watch2"},
        "motor2": {"mode": "highest_diff", "target_watch": "watch2"},
    }, log=False)

    response = main.app.test_client().post("/set_turn", json={"current_turn": "watch2"})

    assert response.status_code == 200
    assert main.load_scores() == {"watch1": 0, "watch2": 1}


def test_turn_change_scores_successful_challenge_players(monkeypatch, tmp_path):
    configure_files(monkeypatch, tmp_path)
    main.save_json_file(main.GAME_STATUS_FILE, {"running": True}, log=False)
    main.save_json_file(main.ASSIGNED_FILE, {
        "ip1": "watch1", "ip2": "watch2", "ip3": "watch3"
    }, log=False)
    main.save_json_file(main.TURN_FILE, {"current_turn": "watch1", "turn_number": 1}, log=False)
    main.save_json_file(main.SCORES_FILE, {"watch1": 0, "watch2": 0, "watch3": 0}, log=False)
    main.save_json_file(main.CONTROL_FILE, {"mode": "attack_challenge"}, log=False)
    main.save_json_file(main.ATTACK_SUCCESS_FILE, {
        "watch2": {"turn": "watch1", "target": "watch1"},
        "watch3": {"turn": "watch3", "target": "watch3"},
    }, log=False)
    main.save_json_file(main.ATTACK_CONDITION_FILE, {
        "turn": "watch1", "direction": "up", "turn_start_heartbeats": {}
    }, log=False)

    response = main.app.test_client().post("/set_turn", json={"current_turn": "watch2"})

    assert response.status_code == 200
    assert main.load_scores() == {"watch1": 0, "watch2": 1, "watch3": 0}


def test_collapse_subtracts_100_from_current_turn(monkeypatch, tmp_path):
    configure_files(monkeypatch, tmp_path)
    main.save_json_file(main.GAME_STATUS_FILE, {"running": True}, log=False)
    main.save_json_file(main.TURN_FILE, {"current_turn": "watch2", "turn_number": 2}, log=False)
    main.save_json_file(main.SCORES_FILE, {"watch1": 1, "watch2": 1}, log=False)

    response = main.app.test_client().post("/collapse", json={"notes": "test"})

    assert response.status_code == 200
    assert response.get_json()["watch_id"] == "watch2"
    assert main.load_scores() == {"watch1": 1, "watch2": -99}


def test_heart_post_during_game_is_added_to_csv_history(monkeypatch, tmp_path):
    paths = {
        "GAME_FILE": "game_status.json",
        "TURN_FILE": "turn.json",
        "DATA_FILE": "heart_rates.json",
        "HISTORY_FILE": "heart_history.json",
        "BASELINE_FILE": "baseline.json",
        "CONTROL_FILE": "control_mode.json",
        "ROTATION_STATUS_FILE": "rotation_status.json",
        "CSV_HISTORY_FILE": "csv_history.json",
        "SCORES_FILE": "scores.json",
    }
    for attribute, filename in paths.items():
        monkeypatch.setattr(heart_api, attribute, str(tmp_path / filename))
    monkeypatch.setattr(heart_api, "BASE_DIR", str(tmp_path))
    heart_api.save_json_file(heart_api.GAME_FILE, {"running": True})
    heart_api.save_json_file(heart_api.TURN_FILE, {"current_turn": "watch1"})
    heart_api.save_json_file(heart_api.BASELINE_FILE, {"watch1": 70})
    heart_api.save_json_file(heart_api.CONTROL_FILE, {"mode": "random_diff"})
    heart_api.save_json_file(heart_api.SCORES_FILE, {"watch1": 2})

    response = main.app.test_client().post(
        "/heart", json={"device_id": "watch1", "data": {"heartbeat": 75}}
    )

    assert response.status_code == 200
    history = heart_api.load_json_file(heart_api.CSV_HISTORY_FILE)
    assert len(history) == 1
    assert history[0]["heartbeat"] == 75
    assert history[0]["diff"] == 5
    assert history[0]["score"] == 2
