import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main
import id_api as id_api_module
import heart_api as heart_api_module


def configure_reset_files(monkeypatch, tmp_path):
    monkeypatch.setattr(heart_api_module, "DATA_FILE", str(tmp_path / "DATA_FILE.json"))
    monkeypatch.setattr(heart_api_module, "HISTORY_FILE", str(tmp_path / "heart_history.json"))
    for name in (
        "ASSIGNED_FILE",
        "DATA_FILE",
        "BASELINE_FILE",
        "CSV_HISTORY_FILE",
        "GAME_STATUS_FILE",
        "TURN_FILE",
        "ROTATION_SETTINGS_FILE",
        "ROTATION_STATUS_FILE",
        "SCORES_FILE",
        "JENGA_SERIES_FILE",
        "COOP_FILE",
        "ATTACK_TARGETS_FILE",
        "ATTACK_PENDING_FILE",
        "ATTACK_SUCCESS_FILE",
        "ATTACK_ROUND_FILE",
        "ATTACK_CONDITION_FILE",
        "CONTROL_FILE",
        "ATTACK_SCORING_FILE",
        "LIVE_CSV_FILE",
        "MANUAL_ROTATION_FILE",
    ):
        monkeypatch.setattr(main, name, str(tmp_path / f"{name}.json"))


def test_game_only_reset_preserves_watch_assignments(monkeypatch, tmp_path):
    configure_reset_files(monkeypatch, tmp_path)
    main.clients.clear()
    assigned_ids = {
        "ip1": "watch1",
        "ip2": "watch2",
        "ip3": "watch3",
        "ip4": "watch4",
    }
    main.save_json_file(main.ASSIGNED_FILE, assigned_ids, log=False)
    main.save_json_file(main.SCORES_FILE, {f"watch{number}": number for number in range(1, 5)}, log=False)

    client = main.app.test_client()
    response = client.post("/reset_game")

    assert response.status_code == 200
    assert main.load_json_file(main.ASSIGNED_FILE) == assigned_ids
    assert main.load_json_file(main.SCORES_FILE) == {f"watch{number}": 0 for number in range(1, 5)}
    assert client.post(
        "/reconnect",
        json={"reconnect_id": "watch4"},
        environ_base={"REMOTE_ADDR": "ip3"},
    ).get_json()["device_id"] == "watch3"
    assert client.get("/assign_id", environ_base={"REMOTE_ADDR": "ip1"}).get_json()["device_id"] == "watch1"


def test_full_reset_clears_assignments_for_fresh_connection_order(monkeypatch, tmp_path):
    configure_reset_files(monkeypatch, tmp_path)
    main.clients.clear()
    main.save_json_file(main.ASSIGNED_FILE, {
        "ip1": "watch1",
        "ip2": "watch2",
        "ip3": "watch3",
        "ip4": "watch4",
    }, log=False)

    client = main.app.test_client()
    response = client.post("/reset")

    assert response.status_code == 200
    assert main.load_json_file(main.ASSIGNED_FILE) == {}
    assert client.post(
        "/reconnect",
        json={"reconnect_id": "watch4"},
        environ_base={"REMOTE_ADDR": "ip3"},
    ).get_json()["device_id"] == "watch1"
    assert client.post(
        "/reconnect",
        json={"reconnect_id": "watch1"},
        environ_base={"REMOTE_ADDR": "ip1"},
    ).get_json()["device_id"] == "watch2"
    assert client.get("/assign_id", environ_base={"REMOTE_ADDR": "ip2"}).get_json()["device_id"] == "watch3"
    assert client.get("/assign_id", environ_base={"REMOTE_ADDR": "ip4"}).get_json()["device_id"] == "watch4"


def test_watch_id_assignment_reuses_ip_and_rejects_fifth_device(monkeypatch, tmp_path):
    assigned_file = tmp_path / "assigned_ids.json"
    monkeypatch.setattr(main, "ASSIGNED_FILE", str(assigned_file))
    main.clients.clear()
    client = main.app.test_client()

    first = client.get("/assign_id", environ_base={"REMOTE_ADDR": "ip1"})
    reconnect = client.post(
        "/reconnect",
        json={"reconnect_id": "watch4"},
        environ_base={"REMOTE_ADDR": "ip1"},
    )
    assert first.get_json()["device_id"] == "watch1"
    assert reconnect.get_json()["device_id"] == "watch1"

    for number in range(2, 5):
        response = client.get("/assign_id", environ_base={"REMOTE_ADDR": f"ip{number}"})
        assert response.get_json()["device_id"] == f"watch{number}"

    fifth = client.get("/assign_id", environ_base={"REMOTE_ADDR": "ip5"})
    assert fifth.status_code == 403
    assert main.load_json_file(str(assigned_file)) == {
        "ip1": "watch1",
        "ip2": "watch2",
        "ip3": "watch3",
        "ip4": "watch4",
    }


def test_register_assigns_first_free_id_and_enforces_four_watch_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(id_api_module, "ID_FILE", str(tmp_path / "assigned_ids.json"))
    main.clients.clear()
    client = main.app.test_client()

    assigned_ids = []
    for number in range(1, 5):
        response = client.post(
            "/register",
            environ_base={"REMOTE_ADDR": f"ip{number}"},
        )
        assert response.status_code == 200
        assigned_ids.append(response.get_json()["assigned_id"])

    fifth = client.post("/register", environ_base={"REMOTE_ADDR": "ip5"})
    assert assigned_ids == ["watch1", "watch2", "watch3", "watch4"]
    assert fifth.status_code == 403


def test_full_reset_clears_heart_history_and_cached_values(monkeypatch, tmp_path):
    configure_reset_files(monkeypatch, tmp_path)
    heart_api_module.save_json_file(heart_api_module.DATA_FILE, {"watch2": [{"heartbeat": 85}]})
    heart_api_module.save_json_file(heart_api_module.HISTORY_FILE, {"watch2": [{"bpm": 85}]})
    monkeypatch.setitem(heart_api_module.latest_timestamps, "watch2", 123)
    monkeypatch.setitem(heart_api_module.latest_heartbeats, "watch2", 85)
    main.save_json_file(main.ROTATION_STATUS_FILE, {"watch2": {"rpm": 40}}, log=False)
    main.save_manual_rotation({"enabled": True, "rpm": 40, "mode": "a"})
    Path(main.LIVE_CSV_FILE).write_text("old data")

    response = main.app.test_client().post("/reset")

    assert response.status_code == 200
    assert heart_api_module.load_json_file(heart_api_module.DATA_FILE) == {}
    assert heart_api_module.load_json_file(heart_api_module.HISTORY_FILE) == {}
    assert heart_api_module.latest_timestamps == {}
    assert heart_api_module.latest_heartbeats == {}
    assert main.load_json_file(main.ROTATION_STATUS_FILE) == {}
    assert main.load_rotation_settings() == {"direction": "auto", "hold": True}
    assert main.load_manual_rotation() == {"enabled": False, "rpm": 10, "mode": "c", "direction": "c"}
    assert Path(main.LIVE_CSV_FILE).read_text() == ""
