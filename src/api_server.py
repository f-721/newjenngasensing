from flask import Flask, request, jsonify, send_from_directory
import os
import json

app = Flask(__name__, static_folder='static')

DATA_FILE = 'heart_rates.json'
GAME_STATUS_FILE = 'game_status.json'
TURN_FILE = 'turn.json'
STATIC_FOLDER = 'static'


# -------------------------
# ヘルパー関数
# -------------------------

def load_json_file(filename):
    if os.path.exists(filename):
        try:
            with open(filename) as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        except Exception as e:
            print(f"[エラー] {filename} の読み込み失敗: {e}")
            return {}
    return {}

def save_json_file(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[ファイル書き込み] {filename} -> {list(data.keys())}")

# Game status
def load_game_status():
    data = load_json_file(GAME_STATUS_FILE)
    return data.get("running", False)

def save_game_status(running):
    save_json_file(GAME_STATUS_FILE, {"running": running})

# Heart rate data
def load_heart_data():
    return load_json_file(DATA_FILE)

def save_heart_data(data):
    save_json_file(DATA_FILE, data)

# Turn
def load_current_turn():
    data = load_json_file(TURN_FILE)
    return data.get("current_turn")

def save_current_turn(turn):
    save_json_file(TURN_FILE, {"current_turn": turn})


# -------------------------
# エンドポイント
# -------------------------

@app.route('/start', methods=['POST'])
def start_game():
    save_game_status(True)
    print("[API] ゲーム開始リクエストを受信しました。")
    return jsonify({"status": "ok", "message": "ゲームを開始しました"})

@app.route('/stop', methods=['POST'])
def stop_game():
    save_game_status(False)
    print("[API] ゲーム停止リクエストを受信しました。")
    return jsonify({"status": "ok", "message": "ゲームを停止しました"})

@app.route('/status', methods=['GET'])
def get_status():
    running = load_game_status()
    print(f"[API] ゲーム状態取得 -> running={running}")
    return jsonify({"running": running})

@app.route('/turn', methods=['GET'])
def get_turn():
    turn = load_current_turn()
    print(f"[API] 現在のターン取得 -> {turn}")
    return jsonify({"current_turn": turn})

@app.route('/turn', methods=['POST'])
def set_turn():
    data = request.get_json()
    if not data or 'current_turn' not in data:
        return jsonify({"status": "error", "message": "current_turnが指定されていません"}), 400

    requested_turn = data['current_turn']
    current_turn = load_current_turn()

    requested_turn = data['current_turn']
    current_turn = load_current_turn()

    if current_turn == requested_turn:
        # 同じ人がもう一度押した -> 解除
        save_current_turn(None)
        print()
        print("=== [ターン宣言] ===")
        print(f"    -> {requested_turn} のターンをキャンセルしました。")
        print("========================")
        print()
        return jsonify({"status": "ok", "message": "ターンをキャンセルしました", "current_turn": None})

    if current_turn and current_turn != requested_turn:
        # 他の人のターン中
        print()
        print("=== [ターン宣言] ===")
        print(f"    -> {requested_turn} が宣言しようとしたが、現在は {current_turn} のターン中です。")
        print("========================")
        print()
        return jsonify({"status": "blocked", "message": f"現在は {current_turn} のターンです", "current_turn": current_turn}), 200

    # 誰もターン取ってない → この人に設定
    save_current_turn(requested_turn)
    print()
    print("=== [ターン宣言] ===")
    print(f"    -> {requested_turn} のターンを開始しました。")
    print("========================")
    print()
    return jsonify({"status": "ok", "message": "ターンを設定しました", "current_turn": requested_turn})


@app.route('/heart', methods=['POST'])
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


@app.route('/heart', methods=['GET'])
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


@app.route('/reset', methods=['POST'])
def reset_server():
    save_json_file(DATA_FILE, {})
    save_json_file(GAME_STATUS_FILE, {"running": False})
    save_json_file(TURN_FILE, {"current_turn": None})
    print("[API] 全データをリセットしました。")
    return jsonify({"status": "ok", "message": "サーバーをリセットしました"})


@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/', methods=['GET'])
def serve_index():
    return send_from_directory(STATIC_FOLDER, 'index.html')

@app.route('/graph.html')
def serve_graph():
    return send_from_directory(STATIC_FOLDER, 'graph.html')


# -------------------------
# エラーハンドラー
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
# サーバ起動
# -------------------------
if __name__ == '__main__':
    print("[APIサーバー起動] 全JSONファイルをリセットします。")
    save_json_file(DATA_FILE, {})
    save_json_file(GAME_STATUS_FILE, {"running": False})
    save_json_file(TURN_FILE, {"current_turn": None})
    app.run(host='0.0.0.0', port=8080)