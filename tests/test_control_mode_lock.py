import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main


def test_control_mode_cannot_be_changed_while_game_is_running(monkeypatch, tmp_path):
    game_file = tmp_path / "game_status.json"
    control_file = tmp_path / "control_mode.json"
    assigned_file = tmp_path / "assigned_ids.json"
    monkeypatch.setattr(main, "GAME_STATUS_FILE", str(game_file))
    monkeypatch.setattr(main, "CONTROL_FILE", str(control_file))
    monkeypatch.setattr(main, "ASSIGNED_FILE", str(assigned_file))
    main.save_json_file(str(game_file), {"running": True}, log=False)
    main.save_json_file(str(control_file), {"mode": "self_fast"}, log=False)
    main.save_json_file(str(assigned_file), {"ip1": "watch1", "ip2": "watch2"}, log=False)

    response = main.app.test_client().post("/set_control_mode", json={"mode": "random_diff"})

    assert response.status_code == 409
    assert main.load_json_file(str(control_file)) == {"mode": "self_fast"}


def test_control_mode_can_be_changed_before_game(monkeypatch, tmp_path):
    game_file = tmp_path / "game_status.json"
    control_file = tmp_path / "control_mode.json"
    assigned_file = tmp_path / "assigned_ids.json"
    monkeypatch.setattr(main, "GAME_STATUS_FILE", str(game_file))
    monkeypatch.setattr(main, "CONTROL_FILE", str(control_file))
    monkeypatch.setattr(main, "ASSIGNED_FILE", str(assigned_file))
    main.save_json_file(str(game_file), {"running": False}, log=False)
    main.save_json_file(str(assigned_file), {"ip1": "watch1", "ip2": "watch2"}, log=False)

    response = main.app.test_client().post("/set_control_mode", json={"mode": "random_diff"})

    assert response.status_code == 200
    assert main.load_json_file(str(control_file)) == {"mode": "random_diff"}


def test_manual_test_mode_can_be_selected_before_game(monkeypatch, tmp_path):
    game_file = tmp_path / "game_status.json"
    control_file = tmp_path / "control_mode.json"
    assigned_file = tmp_path / "assigned_ids.json"
    monkeypatch.setattr(main, "GAME_STATUS_FILE", str(game_file))
    monkeypatch.setattr(main, "CONTROL_FILE", str(control_file))
    monkeypatch.setattr(main, "ASSIGNED_FILE", str(assigned_file))
    main.save_json_file(str(game_file), {"running": False}, log=False)
    main.save_json_file(str(assigned_file), {"ip1": "watch1", "ip2": "watch2"}, log=False)

    response = main.app.test_client().post("/set_control_mode", json={"mode": "manual_test"})

    assert response.status_code == 200
    assert main.load_json_file(str(control_file)) == {"mode": "manual_test"}
