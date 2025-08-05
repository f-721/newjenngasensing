from flask import Blueprint, request, jsonify
from flask import request
import json
import os
import threading


file_lock = threading.Lock()

heart_api = Blueprint('heart_api', __name__)

DATA_FILE = 'heart_rates.json'
TURN_FILE = 'turn.json'

# ヘルパー
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
    print(f"[ファイル書き込み] {filename} -> {list(data.keys())}")

def load_current_turn():
    data = load_json_file(TURN_FILE)
    return data.get("current_turn")

def load_heart_data():
    return load_json_file(DATA_FILE)

def save_heart_data(data):
    save_json_file(DATA_FILE, data)

# -------------------------
# ルーティング
# -------------------------

@heart_api.route('/heart', methods=['POST'])
def post_heart():
    try:
        data = request.get_json()
        print(f"[API] 心拍数POST受信 -> {data}")

        if not data:
            return jsonify({"status": "error", "message": "JSONボディがありません"}), 400

        device_id = data.get('device_id')
        timestamp = data.get('timestamp')
        heartbeat_data = data.get('data')

        if not device_id or not timestamp or not heartbeat_data:
            return jsonify({"status": "error", "message": "必要なフィールドが不足しています"}), 400

        heartbeat = heartbeat_data.get('heartbeat')
        if heartbeat is None:
            return jsonify({"status": "error", "message": "heartbeatが含まれていません"}), 400

        # 現在のターンを確認
        current_turn = load_current_turn()
        if current_turn and current_turn != device_id:
            print(f"[API] 心拍数受信を無視 -> 現在は {current_turn} のターン、受信は {device_id}")
            return jsonify({"status": "ignored", "message": "現在は他のデバイスのターンです"}), 200

        # 受け取って保存
        heart_data = load_heart_data()
        if device_id not in heart_data:
            heart_data[device_id] = []

        heart_data[device_id].append({
            "timestamp": timestamp,
            "heartbeat": heartbeat
        })

        save_heart_data(heart_data)

        print(f"[API] 心拍数保存完了 -> {device_id}")
        return jsonify({"status": "ok"})

    except Exception as e:
        print(f"[エラー] /heart処理中に例外発生: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@heart_api.route('/heart', methods=['GET'])
def get_heart():
    try:
        heart_data = load_heart_data()
        latest_data = {}
        for device_id, history in heart_data.items():
            if history:
                latest_data[device_id] = history[-1]
        print(f"[API] 心拍数取得 -> {latest_data}")
        return jsonify(latest_data)

    except Exception as e:
        print(f"[エラー] /heart取得中に例外発生: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500