from flask import Blueprint, request, jsonify
import json
import os
import threading
import time
from datetime import datetime


heart_api = Blueprint('heart_api', __name__)
reset_api = Blueprint('reset_api', __name__)

file_lock = threading.Lock()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'heart_rates.json')
HISTORY_FILE = os.path.join(BASE_DIR, 'heart_history.json')
TURN_FILE = os.path.join(BASE_DIR, 'turn.json')
GAME_FILE = os.path.join(BASE_DIR, "game_status.json")

# デバイスごとの最新保存タイムスタンプを記録
latest_timestamps = {}
# 最後に保存した heartbeat（補完用）
latest_heartbeats = {}

def is_game_running():
    status = load_json_file(GAME_FILE)
    return status.get("running", False)  # ← ここは実際のキー名に合わせる

def is_collecting_baseline():
    status = load_json_file(GAME_FILE)
    return status.get("baseline_mode", False)


file_lock = threading.Lock()

def load_json_file(filename):
    with file_lock:
        if not os.path.exists(filename):
            return {}
        with open(filename) as f:
            content = f.read().strip()
            return json.loads(content) if content else {}

def save_json_file(filename, data):
    with file_lock:
        with open(filename, 'w') as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())

# ----------------------------------------
# 🔴 POST /heart（通常保存）
# ----------------------------------------
@heart_api.route('/heart', methods=['POST'])
def post_heart():
    try:
        game = load_json_file(GAME_FILE)

        if not game.get("running", False) and not game.get("baseline_mode", False):
            print("[ALLOW] ゲーム停止中でもPOST許可")

        data = request.get_json(force=True)
        device_id = data.get('device_id')
        heartbeat = data.get("data", {}).get("heartbeat")

        if not device_id or heartbeat is None:
            return jsonify({"status": "error", "message": "invalid data"}), 400

        timestamp = int(time.time() * 1000)

        # 保存処理
        data_file = load_json_file(DATA_FILE)
        data_file.setdefault(device_id, []).append({
            "timestamp": timestamp,
            "heartbeat": heartbeat
        })
        save_json_file(DATA_FILE, data_file)

        # ヒストリも保存
        history = load_json_file(HISTORY_FILE)
        history.setdefault(device_id, []).append({
            "time": timestamp,
            "bpm": heartbeat
        })
        history[device_id] = history[device_id][-30:]
        save_json_file(HISTORY_FILE, history)

        print(f"[{datetime.now()}] 🔴 保存: {device_id}, BPM={heartbeat}, timestamp={timestamp}")

        # 補完用データ更新
        latest_timestamps[device_id] = timestamp
        latest_heartbeats[device_id] = heartbeat

        return jsonify({"status": "ok"})

    except Exception as e:
        print("POST /heart error:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

# ----------------------------------------
# 🟡 自動補完スレッド（1秒間POSTが来ない場合）
# ----------------------------------------
def auto_fill_thread():
    while True:
        time.sleep(1)
        now = int(time.time() * 1000)

        game = load_json_file(GAME_FILE)

        running = game.get("running", False)
        baseline = game.get("baseline_mode", False)

        # ❗ゲーム中 or baseline取得中以外は補完しないだけ
        if not running and not baseline:
            continue

        for device_id, last_ts in latest_timestamps.items():
            diff = now - last_ts

            if diff >= 1000:
                heartbeat = latest_heartbeats.get(device_id)
                if heartbeat is None:
                    continue

                fake_ts = now

                data_file = load_json_file(DATA_FILE)
                data_file.setdefault(device_id, []).append({
                    "timestamp": fake_ts,
                    "heartbeat": heartbeat
                })
                save_json_file(DATA_FILE, data_file)

                latest_timestamps[device_id] = fake_ts

                print(f"[{datetime.now()}] 🟡 補完保存: {device_id}, BPM={heartbeat}")
                
# スレッド起動（アプリ起動時に1回だけ実行）
threading.Thread(target=auto_fill_thread, daemon=True).start()

@heart_api.route('/heart', methods=['GET'])
def get_latest_heart_rates():
    heart_data = load_json_file(DATA_FILE)
    turn = load_json_file(TURN_FILE)
    current_turn = turn.get("current_turn")

    print(f"[API] 現在のターン取得 -> {current_turn}")
    # print(f"[API] heart_data -> {heart_data}")

    result = {}
    for device_id, records in heart_data.items():
        if not records:
            continue
        latest = records[-1]
        if current_turn is None or current_turn == device_id:
            result[device_id] = latest

    # print("[API] /heart ->", result)
    return jsonify(result)

@reset_api.route('/reset', methods=['POST'])
def reset():
    # heart_rates.json を空にする
    save_json_file(DATA_FILE, {})

    # turn.json もリセット（任意）
    save_json_file(TURN_FILE, {"current_turn": None})

    # assigned_ids.json もリセットするなら
    # save_json_file(ASSIGNED_IDS_FILE, {})

    print("[RESET] heart_rates.json などを初期化しました")
    return jsonify({"status": "ok", "message": "全データをリセットしました"})

def heartbeat_complement_worker():
    while True:
        time.sleep(1)  # 毎秒チェック
        now = int(time.time() * 1000)
        data_file = load_json_file(DATA_FILE)

        for device_id, last_time in latest_timestamps.items():
            if device_id not in data_file or not data_file[device_id]:
                continue

            last_entry = data_file[device_id][-1]
            time_diff = now - last_entry["timestamp"]

            # 1秒以上経過 & 最終記録から補完されていない（重複防止）
            if time_diff >= 1000 and last_time == last_entry["timestamp"]:
                new_timestamp = last_entry["timestamp"] + 1000
                new_entry = {
                    "timestamp": new_timestamp,
                    "heartbeat": last_entry["heartbeat"]
                }
                data_file[device_id].append(new_entry)
                save_json_file(DATA_FILE, data_file)

                # historyにも追加
                history = load_json_file(HISTORY_FILE)
                if device_id not in history:
                    history[device_id] = []
                history[device_id].append({
                    "time": new_timestamp,
                    "bpm": last_entry["heartbeat"]
                })
                history[device_id] = history[device_id][-30:]
                save_json_file(HISTORY_FILE, history)

                # 表示
                readable = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(new_timestamp / 1000))
                print(f"[{readable}] 🟡 補完: device={device_id}, BPM={last_entry['heartbeat']}, timestamp={new_timestamp}")

@heart_api.route('/get_baselines', methods=['GET'])
def get_baselines():
    baseline = load_json_file('baseline.json')
    return jsonify(baseline)

@heart_api.route('/start_baseline', methods=['POST'])
def start_baseline():
    status = load_json_file(GAME_FILE)
    status["baseline_mode"] = True
    save_json_file(GAME_FILE, status)
    return jsonify({"status": "ok"})

@heart_api.route('/stop_baseline', methods=['POST'])
def stop_baseline():
    status = load_json_file(GAME_FILE)
    status["baseline_mode"] = False
    save_json_file(GAME_FILE, status)
    return jsonify({"status": "ok"})