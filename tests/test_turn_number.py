import os
import sys

from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import turn_api


def test_next_turn_increments_turn_number(monkeypatch, tmp_path):
    turn_file = tmp_path / "turn.json"
    assigned_file = tmp_path / "assigned_ids.json"
    monkeypatch.setattr(turn_api, "TURN_FILE", str(turn_file))
    monkeypatch.setattr(turn_api, "ASSIGNED_FILE", str(assigned_file))
    turn_api.save_json_file(str(turn_file), {
        "current_turn": "watch1",
        "turn_number": 1,
    })
    turn_api.save_json_file(str(assigned_file), {
        "ip1": "watch1",
        "ip2": "watch2",
    })

    app = Flask(__name__)
    app.register_blueprint(turn_api.turn_api)
    response = app.test_client().post("/next_turn")

    assert response.get_json()["next_turn"] == "watch2"
    assert response.get_json()["turn_number"] == 2
    assert turn_api.load_json_file(str(turn_file)) == {
        "current_turn": "watch2",
        "turn_number": 2,
    }
