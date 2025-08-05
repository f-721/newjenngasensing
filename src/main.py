from flask import Flask, jsonify, send_from_directory, request
import os
import json
import threading

from heart_api import heart_api
from turn_api import turn_api
from id_api import id_api

app = Flask(__name__, static_folder='static')
app.register_blueprint(heart_api)
app.register_blueprint(turn_api)
app.register_blueprint(id_api)
clients = {}  # IP: device_id
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


# -------------------------
# APIエンドポイント
# -------------------------
@app.route('/start', methods=['POST'])
def start_game():
    save_json_file(GAME_STATUS_FILE, {"running": True})
    
    assigned_ids = load_json_file(ASSIGNED_FILE)
    if assigned_ids:
        all_ids = sorted(set(assigned_ids.values()))
        first_turn = all_ids[0]
        save_json_file(TURN_FILE, {"current_turn": first_turn})
        print(f"[API] ゲーム開始。最初のターン: {first_turn}")
    else:
        save_json_file(TURN_FILE, {"current_turn": None})
        print("[API] ゲーム開始。しかし割り当てIDが存在しません")

    return jsonify({"status": "ok", "message": "ゲームを開始しました"})

@app.route('/stop', methods=['POST'])
def stop_game():
    save_json_file(GAME_STATUS_FILE, {"running": False})
    print("[API] ゲーム停止")
    return jsonify({"status": "ok", "message": "ゲームを停止しました"})

@app.route('/status', methods=['GET'])
def get_status():
    data = load_json_file(GAME_STATUS_FILE)
    running = data.get("running", False)
    print(f"[API] ゲーム状態取得 -> running={running}")
    return jsonify({"running": running})

@app.route('/reset', methods=['POST'])
def reset_server():
    save_json_file(DATA_FILE, {})
    save_json_file(GAME_STATUS_FILE, {"running": False})
    save_json_file(TURN_FILE, {"current_turn": None})
    save_json_file(ASSIGNED_FILE, {})  # ← これを追加でリセット
    print("[API] サーバーデータを初期化しました（ID割り当てもリセット）")
    return jsonify({"status": "ok", "message": "サーバーを完全リセットしました"})

@app.route("/assign_id")
def assign_id():
    global id_counter
    ip = request.remote_addr
    assigned_ids = load_json_file(ASSIGNED_FILE)

    # すでにIDが割り当てられている場合は再利用
    if ip in assigned_ids:
        device_id = assigned_ids[ip]
    else:
        # すでに割り当て済みのIDを収集
        existing_ids = set(assigned_ids.values())
        while f"watch{id_counter}" in existing_ids:
            id_counter += 1
        device_id = f"watch{id_counter}"
        assigned_ids[ip] = device_id
        save_json_file(ASSIGNED_FILE, assigned_ids)

    clients[ip] = device_id
    return jsonify({"device_id": device_id})


@app.route('/set_turn', methods=['POST'])
def set_turn():
    data = request.get_json()
    new_turn = data.get("current_turn")

    if not new_turn:
        return jsonify({"status": "error", "message": "current_turnが必要です"}), 400

    # ゲームが開始しているか確認
    game_status = load_json_file(GAME_STATUS_FILE)
    if not game_status.get("running", False):
        return jsonify({"status": "error", "message": "ゲームを開始してください"}), 400

    # デバイスが接続されているか確認
    assigned_ids = load_json_file(ASSIGNED_FILE)
    if not assigned_ids:
        return jsonify({"status": "error", "message": "デバイスが接続されていません"}), 400

    if new_turn not in assigned_ids.values():
        return jsonify({"status": "error", "message": "指定されたIDが存在しません"}), 400

    save_json_file("turn.json", {"current_turn": new_turn})
    print(f"[API] 管理者操作: ターンを {new_turn} に設定しました")
    return jsonify({"status": "ok", "message": f"{new_turn} に設定しました"})

@app.route("/clients")
def get_clients():
    return jsonify({
        "count": len(clients),
        "ids": clients
    })

@app.route('/next_turn', methods=['POST'])
def next_turn():
    data = request.get_json()
    device_id = data.get("end_turn")

    if not turn_list or current_turn_index is None:
        return jsonify({"error": "ゲームが開始されていません"}), 400

    if device_id not in turn_list:
        print(f"[エラー] ターン終了リクエスト: {device_id} は turn_list に存在しません")
        return jsonify({"error": f"{device_id} は登録されたデバイスではありません"}), 400

    if turn_list[current_turn_index] != device_id:
        return jsonify({"error": "今のターンのデバイスではありません"}), 400

    # 通常通りターンを進める
    current_turn_index = (current_turn_index + 1) % len(turn_list)
    return jsonify({"next_turn": turn_list[current_turn_index]})

@app.route('/reconnect', methods=['POST'])
def reconnect():
    data = request.get_json()
    reconnect_id = data.get("reconnect_id")
    ip = request.remote_addr

    if not reconnect_id:
        return jsonify({"status": "error", "message": "IDが指定されていません"}), 400

    assigned_ids = load_json_file(ASSIGNED_FILE)

    # すでに他のIPに割り当てられていた場合 → IDを変える
    existing_ids = set(assigned_ids.values())
    if reconnect_id in existing_ids and assigned_ids.get(ip) != reconnect_id:
        # 別のIDを割り当て直す
        id_num = 1
        while f"watch{id_num}" in existing_ids:
            id_num += 1
        reconnect_id = f"watch{id_num}"

    # IDをIPに登録・保存
    clients[ip] = reconnect_id
    assigned_ids[ip] = reconnect_id
    save_json_file(ASSIGNED_FILE, assigned_ids)

    print(f"[API] 再接続: IP {ip} に {reconnect_id} を割り当てました")
    return jsonify({"status": "ok", "message": f"{reconnect_id} を再登録しました", "device_id": reconnect_id})

@app.route('/game_start', methods=['POST'])
def game_start():
    global game_running, turn_list, current_turn_index
    game_running = True
    turn_list = list(clients.values())
    current_turn_index = 0
    print(f"[開始] ターン順: {turn_list}")
    return jsonify({"status": "started"})

@app.route('/', methods=['GET'])
def serve_index():
    return send_from_directory(STATIC_FOLDER, 'index.html')

@app.route('/graph.html')
def serve_graph():
    return send_from_directory(STATIC_FOLDER, 'graph.html')

@app.route('/favicon.ico')
def favicon():
    return '', 204


# -------------------------
# エラーハンドリング
# -------------------------
@app.errorhandler(404)
def not_found(error):
    print(f"[エラー 404] {request.path} が見つかりません")
    return jsonify({"status": "error", "message": "Not Found"}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    print(f"[エラー 405] {request.path} は許可されていないメソッドです")
    return jsonify({"status": "error", "message": "Method Not Allowed"}), 405


# -------------------------
# サーバー起動
# -------------------------
if __name__ == '__main__':
    print("[APIサーバー起動] 全JSONファイルをリセットします。")
    save_json_file(DATA_FILE, {})
    save_json_file(GAME_STATUS_FILE, {"running": False})
    save_json_file(TURN_FILE, {"current_turn": None})
    app.run(host='0.0.0.0', port=8080)