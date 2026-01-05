from flask import Flask, jsonify, send_from_directory, request
import os
import json
import threading
import csv
import time  # ← CSV保存に必要

from heart_api import heart_api
from turn_api import turn_api
from id_api import id_api
from flask import send_file, jsonify
from datetime import datetime, timedelta

app = Flask(__name__, static_folder='static')
app.register_blueprint(heart_api)
app.register_blueprint(turn_api)
app.register_blueprint(id_api)

clients = {}
id_counter = 1
file_lock = threading.Lock()

DATA_FILE = 'heart_rates.json'
GAME_STATUS_FILE = 'game_status.json'
TURN_FILE = 'turn.json'
ASSIGNED_FILE = 'assigned_ids.json'
STATIC_FOLDER = 'static'


# -------------------------
# 共通ヘルパー
# -------------------------
def save_json_file(filename, data):
    with file_lock:
        with open(filename, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[ファイル書き込み] {filename} -> {data}")

def load_json_file(filename):
    with file_lock:
        if os.path.exists(filename):
            with open(filename) as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        return {}

# -------------------------
# APIエンドポイント
# -------------------------
@app.route('/start', methods=['POST'])
def start_game():
    # ゲーム状態をファイルから読み込む
    game_status = load_json_file(GAME_STATUS_FILE)

    # フラグを更新して保存
    game_status["running"] = True
    game_status["game_over"] = False
    save_json_file(GAME_STATUS_FILE, game_status)

    # IDリストを読み込んで最初のターンをセット
    assigned_ids = load_json_file(ASSIGNED_FILE)
    if assigned_ids:
        all_ids = sorted(set(assigned_ids.values()))
        save_json_file(TURN_FILE, {"current_turn": all_ids[0]})
        print(f"[API] ゲーム開始。最初のターン: {all_ids[0]}")
    else:
        save_json_file(TURN_FILE, {"current_turn": None})
        print("[API] ゲーム開始。しかし割り当てIDが存在しません")

    return jsonify({"status": "ok", "message": "ゲームを開始しました"})

@app.route('/stop', methods=['POST'])
def stop_game():
    # ゲーム状態を読み込む
    game_status = load_json_file(GAME_STATUS_FILE)

    # フラグを更新
    game_status["running"] = False
    game_status["game_over"] = True
    save_json_file(GAME_STATUS_FILE, game_status)

    print("[API] ゲーム停止しました")
    return jsonify({"status": "ok", "message": "ゲームを停止しました"})

@app.route('/status', methods=['GET'])
def get_status():
    data = load_json_file(GAME_STATUS_FILE)
    return jsonify({
        "running": data.get("running", False),
        "game_over": data.get("game_over", False)
    })

@app.route("/get_game_status")
def get_game_status():
    status = load_json_file("game_status.json")
    return jsonify(status)

@app.route('/reset', methods=['POST'])
def reset_server():
    save_json_file(DATA_FILE, {})
    save_json_file(GAME_STATUS_FILE, {"running": False, "game_over": False})
    save_json_file(TURN_FILE, {"current_turn": None})
    save_json_file(ASSIGNED_FILE, {})

    print("[API] サーバーデータを初期化しました（ID割り当てもリセット）")
    return jsonify({"status": "ok", "message": "サーバーを完全リセットしました"})

@app.route("/assign_id")
def assign_id():
    global id_counter
    ip = request.remote_addr
    assigned_ids = load_json_file(ASSIGNED_FILE)
    if ip in assigned_ids:
        device_id = assigned_ids[ip]
    else:
        existing_ids = set(assigned_ids.values())
        while f"watch{id_counter}" in existing_ids:
            id_counter += 1
        device_id = f"watch{id_counter}"
        assigned_ids[ip] = device_id
        save_json_file(ASSIGNED_FILE, assigned_ids)
    clients[ip] = device_id
    return jsonify({"device_id": device_id})

@app.route("/clients")
def get_clients():
    return jsonify({"count": len(clients), "ids": clients})

@app.route('/set_turn', methods=['POST'])
def set_turn():
    data = request.get_json()
    new_turn = data.get("current_turn")
    if not new_turn:
        return jsonify({"status": "error", "message": "current_turnが必要です"}), 400
    game_status = load_json_file(GAME_STATUS_FILE)
    if not game_status.get("running", False):
        return jsonify({"status": "error", "message": "ゲームを開始してください"}), 400
    assigned_ids = load_json_file(ASSIGNED_FILE)
    if new_turn not in assigned_ids.values():
        return jsonify({"status": "error", "message": "指定されたIDが存在しません"}), 400
    save_json_file(TURN_FILE, {"current_turn": new_turn})
    print(f"[API] 管理者操作: ターンを {new_turn} に設定しました")
    return jsonify({"status": "ok", "message": f"{new_turn} に設定しました"})

@app.route('/reconnect', methods=['POST'])
def reconnect():
    data = request.get_json()
    reconnect_id = data.get("reconnect_id")
    ip = request.remote_addr
    if not reconnect_id:
        return jsonify({"status": "error", "message": "IDが指定されていません"}), 400
    assigned_ids = load_json_file(ASSIGNED_FILE)
    existing_ids = set(assigned_ids.values())
    if reconnect_id in existing_ids and assigned_ids.get(ip) != reconnect_id:
        id_num = 1
        while f"watch{id_num}" in existing_ids:
            id_num += 1
        reconnect_id = f"watch{id_num}"
    clients[ip] = reconnect_id
    assigned_ids[ip] = reconnect_id
    save_json_file(ASSIGNED_FILE, assigned_ids)
    print(f"[API] 再接続: IP {ip} に {reconnect_id} を割り当てました")
    return jsonify({"status": "ok", "message": f"{reconnect_id} を再登録しました", "device_id": reconnect_id})

@app.route('/export_csv')
def export_csv():
    # ゲームが終了していない場合は保存させない
    game_status = load_json_file("game_status.json")  # 必要に応じてファイル名調整
    if game_status.get("running", True):
        return jsonify({"status": "error", "message": "ゲーム終了後のみCSV保存可能です"}), 403

    # 保存するデータを読み込み
    data = load_json_file("heart_rates.json")  # ← ここが保存対象のJSON

    # ファイル名生成と保存先フォルダ
    timestamp = int(time.time())
    filename = f"heart_rate_data_{timestamp}.csv"
    filepath = os.path.join("data", filename)
    os.makedirs("data", exist_ok=True)

    # 書き込み処理（device_id, timestamp, heartbeat）
    with open(filepath, mode='w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['device_id', 'timestamp', 'heartbeat'])  # ヘッダー行
        for device_id, records in data.items():
            for record in records:
                writer.writerow([
                    device_id,
                    record.get('timestamp', ''),
                    record.get('heartbeat', '')
                ])

    print(f"[CSV保存] {filepath} に保存されました")

    # クライアントにファイル送信（ダウンロード）
    return send_file(filepath, as_attachment=True, download_name="heart_rate_data.csv")
    
@app.route('/get_heart_data', methods=['GET'])
def get_heart_data():
    try:
        all_data = load_json_file(DATA_FILE)
        now_ms = int(datetime.now().timestamp() * 1000)
        thirty_sec_ago = now_ms - 30_000

        complemented_data = {}

        for device_id, entries in all_data.items():

            # ---- Get entries from last 30 seconds ----
            recent_entries = [
                entry for entry in entries if entry['timestamp'] >= thirty_sec_ago
            ]
            recent_entries.sort(key=lambda x: x['timestamp'])

            if not recent_entries:
                continue

            filled_entries = []
            last_entry = recent_entries[0]
            filled_entries.append(last_entry)

            complement_count = 0
            last_complement_ts = None

            # ---- Fill missing intervals between samples ----
            for rec in recent_entries[1:]:
                diff = rec["timestamp"] - last_entry["timestamp"]

                if diff > 1000:
                    missing_count = diff // 1000 - 1
                    for i in range(missing_count):
                        fake_ts = last_entry["timestamp"] + 1000 * (i + 1)
                        filled_entries.append({
                            "timestamp": fake_ts,
                            "heartbeat": last_entry["heartbeat"]
                        })
                        complement_count += 1
                        last_complement_ts = fake_ts

                filled_entries.append(rec)
                last_entry = rec

            # ---- Fill from last entry to current time (existing logic) ----
            while last_entry["timestamp"] + 1000 < now_ms - 200:  # 200ms buffer
                fake_ts = last_entry["timestamp"] + 1000
                filled_entries.append({
                    "timestamp": fake_ts,
                    "heartbeat": last_entry["heartbeat"]
                })
                complement_count += 1
                last_complement_ts = fake_ts
                last_entry = {
                    "timestamp": fake_ts,
                    "heartbeat": last_entry["heartbeat"]
                }

            # ✅ 追加：もし「最後の時刻」が現在より前なら、それも補完
            if last_entry["timestamp"] < now_ms - 200:
                while last_entry["timestamp"] + 1000 <= now_ms:
                    fake_ts = last_entry["timestamp"] + 1000
                    filled_entries.append({
                        "timestamp": fake_ts,
                        "heartbeat": last_entry["heartbeat"]
                    })
                    complement_count += 1
                    last_complement_ts = fake_ts
                    last_entry = {
                        "timestamp": fake_ts,
                        "heartbeat": last_entry["heartbeat"]
                    }

            if complement_count > 0:
                print(f"[補完] {device_id}: reused previous value {last_entry['heartbeat']} {complement_count} times (last at {last_complement_ts})")

            complemented_data[device_id] = filled_entries

        return jsonify(complemented_data)

    except Exception as e:
        print(f"[ERROR] get_heart_data failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
@app.route('/')
def serve_index():
    return send_from_directory(STATIC_FOLDER, 'index.html')

@app.route('/graph.html')
def serve_graph():
    return send_from_directory(STATIC_FOLDER, 'graph.html')

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.errorhandler(404)
def not_found(error):
    print(f"[エラー 404] {request.path} が見つかりません")
    return jsonify({"status": "error", "message": "Not Found"}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    print(f"[エラー 405] {request.path} は許可されていないメソッドです")
    return jsonify({"status": "error", "message": "Method Not Allowed"}), 405

if __name__ == '__main__':
    print("[APIサーバー起動] 状態維持モードで開始")
    app.run(host='0.0.0.0', port=8080)