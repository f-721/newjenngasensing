from flask import Blueprint, request, jsonify
import json
import os
import threading
import time

file_lock = threading.Lock()

heart_api = Blueprint('heart_api', __name__)

DATA_FILE = 'heart_rates.json'
HISTORY_FILE = 'heart_history.json'
GAME_STATUS_FILE = 'game_status.json'
TURN_FILE = 'turn.json'

# ---------- 共通 ----------
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
            json.dump(data, f, indent=2, ensure_ascii=False)

# ---------- API ----------

@heart_api.route('/heart', methods=['POST'])
def post_heart():
    data = request.get_json()
    print("[POST] /heart ->", data)

    device_id = data.get('device_id')
    heartbeat = data["data"]["heartbeat"]
    timestamp = int(time.time() * 1000)  # ← 修正前は time.strftime('%H:%M:%S')

    # ■ゲーム中のみ保存
    game_status = load_json_file(GAME_STATUS_FILE)
    if not game_status.get("running", False):
        return jsonify({"status": "ignored", "message": "ゲーム未開始"}), 200

    # ■ターン制御
    current_turn = load_json_file(TURN_FILE).get("current_turn")
    if current_turn and current_turn != device_id:
        return jsonify({"status": "ignored", "message": "他人のターン"}), 200

    # ---------- 最新心拍保存 ----------
    data_file = load_json_file(DATA_FILE)
    if device_id not in data_file:
        data_file[device_id] = []

    data_file[device_id].append({
        "timestamp": timestamp,
        "heartbeat": heartbeat
    })
    save_json_file(DATA_FILE, data_file)

    # ---------- 履歴（30件だけ保存） ----------
    history = load_json_file(HISTORY_FILE)
    if device_id not in history:
        history[device_id] = []

    history[device_id].append({"time": timestamp, "bpm": heartbeat})
    history[device_id] = history[device_id][-30:]
    save_json_file(HISTORY_FILE, history)

    return jsonify({"status": "ok"})


@heart_api.route('/heart', methods=['GET'])
def get_heart():
    try:
        data = load_json_file(DATA_FILE)
        latest = {}
        for device_id, rec in data.items():
            latest[device_id] = rec[-1]   # 最新のみ返す
        return jsonify(latest)
    except:
        
        return jsonify({})