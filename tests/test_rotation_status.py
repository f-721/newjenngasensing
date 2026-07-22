import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main


def test_get_rotation_status_returns_current_target(monkeypatch, tmp_path):
    status_path = tmp_path / "rotation_status.json"
    monkeypatch.setattr(main, "ROTATION_STATUS_FILE", str(status_path))

    main.save_rotation_status({
        "watch2": {
            "target_watch": "watch1",
            "mode": "self_fast",
            "bpm": 81.0,
            "reference_bpm": 78.0,
            "source": "baseline",
            "rpm": 20,
            "direction": "c",
        }
    })

    client = main.app.test_client()
    response = client.get("/get_rotation_status")

    assert response.status_code == 200
    data = response.get_json()
    assert data["watch2"]["target_watch"] == "watch1"
    assert data["watch2"]["mode"] == "self_fast"


def test_rotation_settings_can_be_saved_and_loaded(monkeypatch, tmp_path):
    settings_path = tmp_path / "rotation_settings.json"
    monkeypatch.setattr(main, "ROTATION_SETTINGS_FILE", str(settings_path))

    client = main.app.test_client()

    assert client.get("/get_rotation_direction").get_json() == {"direction": "auto"}
    assert client.get("/get_rotation_hold").get_json() == {"hold": True}

    direction_response = client.post("/set_rotation_direction", json={"direction": "a"})
    hold_response = client.post("/set_rotation_hold", json={"hold": False})

    assert direction_response.status_code == 200
    assert hold_response.status_code == 200
    assert client.get("/get_rotation_settings").get_json() == {"direction": "a", "hold": False}


def test_attack_target_api_validates_and_persists(monkeypatch, tmp_path):
    targets_path = tmp_path / "attack_targets.json"
    monkeypatch.setattr(main, "ATTACK_TARGETS_FILE", str(targets_path))

    client = main.app.test_client()
    assert client.post("/set_attack_target", json={"attacker": "watch1", "target": "watch1"}).status_code == 400
    assert client.post("/set_attack_target", json={"attacker": "watch5", "target": "watch1"}).status_code == 400

    response = client.post("/set_attack_target", json={"attacker": "watch1", "target": "watch3"})
    assert response.status_code == 200
    assert client.get("/attack_targets").get_json()["watch1"] == "watch3"

    unset_response = client.post("/set_attack_target", json={"attacker": "watch1", "target": None})
    assert unset_response.status_code == 200
    assert client.get("/attack_targets").get_json()["watch1"] is None


def test_current_attackers_returns_players_targeting_current_turn(monkeypatch, tmp_path):
    targets_path = tmp_path / "attack_targets.json"
    turn_path = tmp_path / "turn.json"
    monkeypatch.setattr(main, "ATTACK_TARGETS_FILE", str(targets_path))
    monkeypatch.setattr(main, "TURN_FILE", str(turn_path))
    main.save_attack_targets({"watch1": "watch3", "watch2": "watch3", "watch3": None, "watch4": "watch1"})
    main.save_json_file(str(turn_path), {"current_turn": "watch3"})

    response = main.app.test_client().get("/current_attackers")
    assert response.status_code == 200
    assert response.get_json() == {"current_turn": "watch3", "attackers": ["watch1", "watch2"], "attack_count": 2}


def test_attack_status_returns_current_turn_and_attackers(monkeypatch, tmp_path):
    targets_path = tmp_path / "attack_targets.json"
    turn_path = tmp_path / "turn.json"
    game_status_path = tmp_path / "game_status.json"
    monkeypatch.setattr(main, "ATTACK_TARGETS_FILE", str(targets_path))
    monkeypatch.setattr(main, "TURN_FILE", str(turn_path))
    monkeypatch.setattr(main, "GAME_STATUS_FILE", str(game_status_path))
    main.save_attack_targets({"watch1": "watch3", "watch2": "watch3", "watch3": None, "watch4": None})
    main.save_json_file(str(turn_path), {"current_turn": "watch3"})
    main.save_json_file(str(game_status_path), {"round": 2})

    response = main.app.test_client().get("/attack_status")

    assert response.status_code == 200
    assert response.get_json() == {"round": 2, "current_turn": "watch3", "attackers": ["watch1", "watch2"]}
