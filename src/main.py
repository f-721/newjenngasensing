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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.abspath(os.path.join(BASE_DIR, 'heart_rates.json'))
BASELINE_FILE = os.path.join(BASE_DIR, "baseline.json")


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


@app.route('/start', methods=['POST'])
def start_game():

    assigned_ids = load_json_file(ASSIGNED_FILE)
    baseline_data = load_json_file("baseline.json")

    assigned_watch_ids = set(assigned_ids.values())
    baseline_watch_ids = set(baseline_data.keys())

    print("[DEBUG] assigned:", assigned_watch_ids)
    print("[DEBUG] baseline:", baseline_watch_ids)

    # 🔴 baseline未取得watchチェック
    missing = assigned_watch_ids - baseline_watch_ids

    if missing:
        return jsonify({
            "status": "error",
            "message": f"以下のwatchの平均値が未取得: {', '.join(missing)}"
        }), 400

    # 🟢 baseline揃ったので開始OK
    game_status = load_json_file(GAME_STATUS_FILE)
    game_status["running"] = True
    game_status["game_over"] = False
    save_json_file(GAME_STATUS_FILE, game_status)

    # ターン初期化
    ids = sorted(assigned_watch_ids)
    save_json_file(TURN_FILE, {
        "current_turn": ids[0] if ids else None
    })

    print("[GAME START] baseline一致 → 開始")

    return jsonify({"status": "ok"})

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
    save_json_file("baseline_heart_rates.json", {})  # ← これ追加！

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
# ゲーム状態チェック削除！！
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
    data = load_json_file(DATA_FILE)  # ← ここが保存対象のJSON

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

@app.route("/set_baseline", methods=["POST"])
def set_baseline():
    data = request.get_json()
    device_id = data.get("device_id")
    bpm = data.get("bpm")
    if not device_id or bpm is None:
        return jsonify({"status": "error", "message": "IDかBPMが不足"}), 400

    path = "baseline_bpm.json"
    if os.path.exists(path):
        with open(path, "r") as f:
            baselines = json.load(f)
    else:
        baselines = {}

    baselines[device_id] = bpm
    with open(path, "w") as f:
        json.dump(baselines, f, indent=2)

    print(f"[基準BPM設定] {device_id} → {bpm}")
    return jsonify({"status": "ok", "message": f"{device_id} の基準心拍数を {bpm} に設定"})

@app.route('/start_baseline', methods=['POST'])
def start_baseline():
    status = load_json_file(GAME_STATUS_FILE)
    status["baseline_mode"] = True
    status["running"] = False
    status["game_over"] = False
    save_json_file(GAME_STATUS_FILE, status)
    print("[GAME] ベースライン取得モード開始")
    return jsonify({"status": "ok", "mode": "baseline"})

@app.route('/calculate_baseline/<device_id>', methods=['POST'])
def calculate_baseline(device_id):

    time.sleep(1.2)

    data_file = load_json_file(DATA_FILE)
    records = data_file.get(device_id, [])

    now = int(time.time() * 1000)
    ten_sec_ago = now - 10000

    recent = [
        r["heartbeat"]
        for r in records
        if r["timestamp"] >= ten_sec_ago
    ]

    if len(recent) < 5:
        return jsonify({"error":"最低5件必要"}),400

    avg = sum(recent) / len(recent)

    print(f"[BASELINE OK] {device_id} avg={avg} samples={len(recent)}")

    # 🔴🔴🔴ここが最重要🔴🔴🔴
    baseline = load_json_file(BASELINE_FILE)
    baseline[device_id] = avg
    save_json_file(BASELINE_FILE, baseline)
    print(f"[BASELINE SAVE] {device_id} -> {avg}")

    return jsonify({"average":avg})

@app.route('/stop_baseline', methods=['POST'])
def stop_baseline():
    status = load_json_file(GAME_STATUS_FILE)
    status["baseline_mode"] = False
    save_json_file(GAME_STATUS_FILE, status)
    print("[GAME] ベースライン取得モード終了")
    return jsonify({"status": "ok", "mode": "normal"})


@app.route('/speed.html')
def serve_speed():
    return send_from_directory(STATIC_FOLDER, 'speed.html')

@app.route('/babanuki.html')
def serve_babanuki():
    return send_from_directory(STATIC_FOLDER, 'babanuki.html')

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