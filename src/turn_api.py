from flask import Blueprint, jsonify
import json
import os
import threading
from score_logic import (
    ATTACK_SCORING_MODES,
    apply_points,
    attack_challenge_score_awards,
    normalize_scores,
    turn_scoring_targets,
)

turn_api = Blueprint('turn_api', __name__)

TURN_FILE = 'turn.json'
ASSIGNED_FILE = 'assigned_ids.json'
ROTATION_STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rotation_status.json')
SCORES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scores.json')
CONTROL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'control_mode.json')
ATTACK_SUCCESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'attack_success.json')
ATTACK_SCORING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'attack_scoring.json')
file_lock = threading.Lock()

# -------------------------
# JSONヘルパー
# -------------------------
def load_json_file(filename):
    with file_lock:
        if os.path.exists(filename):
            with open(filename) as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        return {}

def save_json_file(filename, data):
    with file_lock:
        with open(filename, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[ファイル書き込み] {filename} -> {data}")

def load_current_turn():
    data = load_json_file(TURN_FILE)
    return data.get("current_turn")

def save_current_turn(turn, advance=False):
    data = load_json_file(TURN_FILE)
    turn_number = data.get("turn_number", 0)
    if advance:
        turn_number += 1
    save_json_file(TURN_FILE, {"current_turn": turn, "turn_number": turn_number})


def award_turn_scores(current_turn):
    control_mode = load_json_file(CONTROL_FILE).get("mode")
    attack_success = load_json_file(ATTACK_SUCCESS_FILE)
    if control_mode == "attack_challenge":
        scoring = load_json_file(ATTACK_SCORING_FILE)
        scoring_mode = scoring.get("mode") if isinstance(scoring, dict) else "success"
        if scoring_mode not in ATTACK_SCORING_MODES:
            scoring_mode = "success"
        awards = attack_challenge_score_awards(attack_success, current_turn, scoring_mode)
        if awards:
            scores = normalize_scores(load_json_file(SCORES_FILE))
            for watch_id, award in awards.items():
                scores = apply_points(scores, [watch_id], award["points"])
            save_json_file(SCORES_FILE, scores)
        return

    targets = turn_scoring_targets(
        control_mode,
        load_json_file(ROTATION_STATUS_FILE),
        attack_success,
        current_turn,
    )
    if targets:
        scores = apply_points(normalize_scores(load_json_file(SCORES_FILE)), targets, 1)
        save_json_file(SCORES_FILE, scores)

# -------------------------
# APIルート
# -------------------------

@turn_api.route('/turn', methods=['GET'])
def get_turn():
    data = load_json_file(TURN_FILE)
    turn = data.get("current_turn")
    turn_number = data.get("turn_number", 0)
    print(f"[API] 現在のターン取得 -> {turn}")
    return jsonify({"current_turn": turn, "turn_number": turn_number})

@turn_api.route('/next_turn', methods=['POST'])
def next_turn():
    assigned_ids = load_json_file(ASSIGNED_FILE)
    all_ids = sorted(set(assigned_ids.values()))

    if not all_ids:
        return jsonify({"status": "error", "message": "割り当てIDがありません"}), 500

    current = load_current_turn()
    if current not in all_ids:
        next_index = 0
    else:
        current_index = all_ids.index(current)
        next_index = (current_index + 1) % len(all_ids)

    next_id = all_ids[next_index]
    if current != next_id:
        award_turn_scores(current)
    save_current_turn(next_id, advance=True)

    print()
    print("=== [ターン進行] ===")
    print(f"    {current} → {next_id}")
    print("======================")
    print()

    turn_number = load_json_file(TURN_FILE).get("turn_number", 0)
    return jsonify({
        "status": "ok",
        "message": f"{current} → {next_id}",
        "next_turn": next_id,
        "turn_number": turn_number,
    })
