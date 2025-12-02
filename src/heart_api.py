from flask import Blueprint, request, jsonify
import json
import os
import threading
import time

heart_api = Blueprint('heart_api', __name__)
reset_api = Blueprint('reset_api', __name__)

file_lock = threading.Lock()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'heart_rates.json')
HISTORY_FILE = os.path.join(BASE_DIR, 'heart_history.json')
TURN_FILE = os.path.join(BASE_DIR, 'turn.json')


file_lock = threading.Lock()

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
            json.dump(data, f)

@heart_api.route('/heart', methods=['POST'])
def post_heart():
    try:
        data = request.get_json(force=True)
        print("[POST] /heart ->", data)

        device_id = data.get('device_id')
        heartbeat = data.get("data", {}).get("heartbeat")

        if not device_id or heartbeat is None:
            return jsonify({"status": "error", "message": "不正なデータ"}), 400

        timestamp = int(time.time() * 1000)
        print(f"[保存] {device_id} に {heartbeat} bpm を保存します")

        # 保存処理
        data_file = load_json_file(DATA_FILE)
        if device_id not in data_file:
            data_file[device_id] = []
        data_file[device_id].append({
            "timestamp": timestamp,
            "heartbeat": heartbeat
        })
        save_json_file(DATA_FILE, data_file)

        history = load_json_file(HISTORY_FILE)
        if device_id not in history:
            history[device_id] = []
        history[device_id].append({
            "time": timestamp,
            "bpm": heartbeat
        })
        history[device_id] = history[device_id][-30:]
        save_json_file(HISTORY_FILE, history)

        return jsonify({"status": "ok"})
    except Exception as e:
        print("⚠️ POST /heart エラー:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

@heart_api.route('/heart', methods=['GET'])
def get_latest_heart_rates():
    heart_data = load_json_file(DATA_FILE)
    turn = load_json_file(TURN_FILE)
    current_turn = turn.get("current_turn")

    print(f"[API] 現在のターン取得 -> {current_turn}")
    print(f"[API] heart_data -> {heart_data}")

    result = {}
    for device_id, records in heart_data.items():
        if not records:
            continue
        latest = records[-1]
        if current_turn is None or current_turn == device_id:
            result[device_id] = latest

    print("[API] /heart ->", result)
    return jsonify(result)

@reset_api.route('/reset', methods=['POST'])
def reset():
    # heart_rates.json を空にする
    save_json_file(DATA_FILE, {})

    # turn.json もリセット（任意）
    save_json_file(TURN_FILE, {"current_turn": None})

    # assigned_ids.json もリセットするなら
    save_json_file(ASSIGNED_IDS_FILE, {})

    print("[RESET] heart_rates.json などを初期化しました")
    return jsonify({"status": "ok", "message": "全データをリセットしました"})