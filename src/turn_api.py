from flask import Blueprint, jsonify
import json
import os
import threading

turn_api = Blueprint('turn_api', __name__)

TURN_FILE = 'turn.json'
ASSIGNED_FILE = 'assigned_ids.json'
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

def save_current_turn(turn):
    save_json_file(TURN_FILE, {"current_turn": turn})

# -------------------------
# APIルート
# -------------------------

@turn_api.route('/turn', methods=['GET'])
def get_turn():
    turn = load_current_turn()
    print(f"[API] 現在のターン取得 -> {turn}")
    return jsonify({"current_turn": turn})

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
    save_current_turn(next_id)

    print()
    print("=== [ターン進行] ===")
    print(f"    {current} → {next_id}")
    print("======================")
    print()

    return jsonify({"status": "ok", "message": f"{current} → {next_id}", "next_turn": next_id})