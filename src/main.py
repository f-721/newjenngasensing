from flask import Flask, jsonify, send_from_directory, request
import os
import json
import threading
import csv
import time  # ← CSV保存に必要
import random
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

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
CONTROL_FILE = "control_mode.json"
ROTATION_SETTINGS_FILE = os.path.join(BASE_DIR, "rotation_settings.json")
ROTATION_STATUS_FILE = os.path.join(BASE_DIR, "rotation_status.json")
ATTACK_TARGETS_FILE = os.path.join(BASE_DIR, "attack_targets.json")
ATTACK_ROUND_FILE = os.path.join(BASE_DIR, "attack_round.json")
ATTACK_PENDING_FILE = os.path.join(BASE_DIR, "attack_pending.json")
ATTACK_CONDITION_FILE = os.path.join(BASE_DIR, "attack_condition.json")
CSV_HISTORY_FILE = os.path.join(BASE_DIR, "csv_history.json")
CSV_COLUMNS = [
    "timestamp", "device_id", "heartbeat", "baseline", "diff", "abs_diff",
    "game_phase", "current_turn", "control_mode", "random_extreme",
    "target_watch", "is_target", "rpm", "direction", "source_timestamp", "collapse",
]
CSV_INTEGER_COLUMNS = {"heartbeat", "baseline", "diff", "abs_diff", "rpm"}
ATTACK_CHALLENGE_RULES = {
    "first_up_baseline_offset": 30,
    "first_down_baseline_offset": -10,
    "down_after_up_baseline_offset": 5,
    "up_after_down_turn_start_offset": 50,
    "up_repeat_turn_start_offset": 10,
}


# -------------------------
# 共通ヘルパー
# -------------------------
def save_json_file(filename, data, log=True):
    with file_lock:
        with open(filename, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    if log:
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


def load_rotation_settings():
    settings = load_json_file(ROTATION_SETTINGS_FILE)
    direction = settings.get("direction", "auto")
    if direction not in {"auto", "c", "a"}:
        direction = "auto"
    hold = settings.get("hold", True)
    return {"direction": direction, "hold": hold if isinstance(hold, bool) else True}


def save_rotation_settings(settings):
    save_json_file(ROTATION_SETTINGS_FILE, settings)


def save_rotation_status(status):
    save_json_file(ROTATION_STATUS_FILE, status, log=False)


def load_csv_history():
    history = load_json_file(CSV_HISTORY_FILE)
    if isinstance(history, list):
        return history
    if isinstance(history, dict) and isinstance(history.get("rows"), list):
        return history["rows"]
    return []


def format_csv_value(column, value):
    if column not in CSV_INTEGER_COLUMNS or value in {"", None}:
        return value
    try:
        return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return value


def record_csv_snapshot(target_watch, mode, rpm, direction, extreme=None):
    if not load_json_file(GAME_STATUS_FILE).get("running", False):
        return

    heart_data = load_json_file(DATA_FILE)
    baselines = load_json_file(BASELINE_FILE)
    current_turn = load_json_file(TURN_FILE).get("current_turn")
    timestamp = int(time.time() * 1000)
    rows = []

    for device_id, records in sorted(heart_data.items()):
        if not records:
            continue
        latest = records[-1]
        heartbeat = latest.get("heartbeat")
        baseline = baselines.get(device_id, "")
        try:
            diff = float(heartbeat) - float(baseline)
            abs_diff = abs(diff)
        except (TypeError, ValueError):
            diff = abs_diff = ""

        rows.append({
            "timestamp": timestamp, "device_id": device_id, "heartbeat": heartbeat,
            "baseline": baseline, "diff": diff, "abs_diff": abs_diff,
            "game_phase": "playing", "current_turn": current_turn or "",
            "control_mode": mode,
            "random_extreme": {"up": "上昇", "down": "下降"}.get(extreme, "") if mode == "random_diff" else "",
            "target_watch": target_watch, "is_target": device_id == target_watch,
            "rpm": rpm, "direction": direction,
            "source_timestamp": latest.get("timestamp", ""), "collapse": "",
        })

    if rows:
        history = load_csv_history()
        history.extend(rows)
        save_json_file(CSV_HISTORY_FILE, history, log=False)


def load_attack_targets():
    targets = load_json_file(ATTACK_TARGETS_FILE)
    return targets if isinstance(targets, dict) else {}


def save_attack_targets(targets):
    save_json_file(ATTACK_TARGETS_FILE, targets, log=False)


def load_attack_round():
    round_state = load_json_file(ATTACK_ROUND_FILE)
    return round_state if isinstance(round_state, dict) else {}


def save_attack_round(round_state):
    save_json_file(ATTACK_ROUND_FILE, round_state, log=False)


def update_attack_round_for_turn(current_turn, assigned_watches):
    """Advance the attack-use round only after every connected watch has had a turn."""
    round_state = load_attack_round()
    previous_turn = round_state.get("last_turn")
    used_attackers = set(round_state.get("used_attackers", []))
    seen_turns = set(round_state.get("seen_turns", []))
    completed = bool(round_state.get("completed"))

    if current_turn == previous_turn:
        return round_state

    if completed:
        round_state = {
            "used_attackers": [],
            "seen_turns": [current_turn],
            "last_turn": current_turn,
            "completed": False,
        }
        save_attack_targets({})
        save_attack_pending({})
        save_attack_round(round_state)
        return round_state

    if current_turn in assigned_watches:
        seen_turns.add(current_turn)
    round_state = {
        "used_attackers": sorted(used_attackers),
        "seen_turns": sorted(seen_turns),
        "last_turn": current_turn,
        "completed": bool(assigned_watches) and assigned_watches.issubset(seen_turns),
    }
    save_attack_round(round_state)
    return round_state


def load_attack_pending():
    pending = load_json_file(ATTACK_PENDING_FILE)
    return pending if isinstance(pending, dict) else {}


def save_attack_pending(pending):
    save_json_file(ATTACK_PENDING_FILE, pending, log=False)


def get_latest_heartbeats():
    heart_data = load_json_file(DATA_FILE)
    heartbeats = {}
    for watch_id, records in heart_data.items():
        if not records:
            continue
        try:
            heartbeats[watch_id] = float(records[-1].get("heartbeat"))
        except (AttributeError, TypeError, ValueError):
            continue
    return heartbeats


def get_attack_challenge_condition():
    """Return the stable per-turn challenge condition, creating it on a turn change."""
    current_turn = load_json_file(TURN_FILE).get("current_turn")
    condition = load_json_file(ATTACK_CONDITION_FILE)
    if condition.get("turn") == current_turn and condition.get("direction") in {"up", "down"}:
        return condition

    previous_direction = condition.get("direction")
    is_first_turn = not condition.get("turn")
    direction = random.choice(["up", "down"])
    condition = {
        "turn": current_turn,
        "direction": direction,
        "previous_direction": previous_direction,
        "first_turn": is_first_turn,
        "turn_start_heartbeats": get_latest_heartbeats(),
    }
    pending = load_attack_pending()
    expired_pending = {
        attacker: signal
        for attacker, signal in pending.items()
        if signal.get("turn") == current_turn
    }
    if expired_pending != pending:
        save_attack_pending(expired_pending)
    save_json_file(ATTACK_CONDITION_FILE, condition, log=False)
    return condition


def attack_threshold(attacker, condition):
    """Return the current challenge target for one attacking watch."""
    baselines = load_json_file(BASELINE_FILE)
    try:
        baseline = float(baselines[attacker])
    except (KeyError, TypeError, ValueError):
        return None

    direction = condition["direction"]
    if condition.get("first_turn"):
        offset = ATTACK_CHALLENGE_RULES["first_up_baseline_offset"] if direction == "up" else ATTACK_CHALLENGE_RULES["first_down_baseline_offset"]
        return baseline + offset

    previous_direction = condition.get("previous_direction")
    turn_start = condition.get("turn_start_heartbeats", {})
    try:
        start_bpm = float(turn_start[attacker])
    except (KeyError, TypeError, ValueError):
        return None

    if direction == "down":
        offset = ATTACK_CHALLENGE_RULES["first_down_baseline_offset"] if previous_direction == "down" else ATTACK_CHALLENGE_RULES["down_after_up_baseline_offset"]
        return baseline + offset

    offset = ATTACK_CHALLENGE_RULES["up_repeat_turn_start_offset"] if previous_direction == "up" else ATTACK_CHALLENGE_RULES["up_after_down_turn_start_offset"]
    return start_bpm + offset


def resolve_attack_challenge():
    """Promote pending attack signals once their sender meets this turn's condition."""
    condition = get_attack_challenge_condition()
    pending = load_attack_pending()
    active_targets = load_attack_targets()
    heartbeats = get_latest_heartbeats()
    resolved = []

    for attacker, signal in list(pending.items()):
        if signal.get("turn") != condition.get("turn"):
            continue
        threshold = attack_threshold(attacker, condition)
        heartbeat = heartbeats.get(attacker)
        if threshold is None or heartbeat is None:
            continue

        success = heartbeat >= threshold if condition["direction"] == "up" else heartbeat <= threshold
        if success:
            active_targets[attacker] = signal["target"]
            del pending[attacker]
            resolved.append(attacker)

    if resolved:
        save_attack_targets(active_targets)
        save_attack_pending(pending)

    return condition, pending, active_targets, resolved


@app.route('/start', methods=['POST'])
def start_game():
    if load_csv_history():
        return jsonify({"status": "error", "message": "前回ゲームのCSVデータが残っています。「ゲームだけリセット」でCSVデータを消去してください"}), 400

    assigned_ids = load_json_file(ASSIGNED_FILE)      # {"ip":"watch1", ...}
    baseline_data = load_json_file(BASELINE_FILE)     # {"watch1": 68.2, ...}

    assigned_watch_ids = set(assigned_ids.values())

    # ✅ 1) そもそもwatchが認識できてないなら開始させない
    if not assigned_watch_ids:
        return jsonify({
            "status": "error",
            "message": "watch側にて再接続を行なってください（接続watchが認識できていません）"
        }), 400

    # ✅ 2) baselineの値が数値かチェックしつつ、baseline側のwatch集合を作る
    baseline_watch_ids = set()
    bad = []
    for wid, v in baseline_data.items():
        try:
            float(v)
            baseline_watch_ids.add(wid)
        except:
            bad.append(wid)

    if bad:
        return jsonify({
            "status": "error",
            "message": f"baseline.jsonの値が数値でないwatchがあります: {', '.join(bad)}"
        }), 400

    # ✅ 3) 「不足」も「余分」も許さず、完全一致でないと開始不可
    missing = assigned_watch_ids - baseline_watch_ids
    extra   = baseline_watch_ids - assigned_watch_ids

    if missing or extra:
        parts = []
        if missing:
            parts.append("未取得: " + ", ".join(sorted(missing)))
        if extra:
            parts.append("余計に入ってる: " + ", ".join(sorted(extra)))
        return jsonify({
            "status": "error",
            "message": "baselineが揃っていません（接続watchと一致しません）: " + " / ".join(parts)
        }), 400

    # 🟢 baseline揃ったので開始OK
    game_status = load_json_file(GAME_STATUS_FILE)
    game_status["running"] = True
    game_status["game_over"] = False
    save_json_file(GAME_STATUS_FILE, game_status)

    # ターン初期化
    ids = sorted(assigned_watch_ids)
    save_json_file(TURN_FILE, {"current_turn": ids[0] if ids else None})

    print("[GAME START] baseline完全一致 → 開始")
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
    save_json_file(CSV_HISTORY_FILE, [], log=False)
    save_json_file(GAME_STATUS_FILE, {
        "running": False,
        "game_over": False,
        "baseline_mode": False
    })
    save_json_file(TURN_FILE, {"current_turn": None})
    save_json_file(ASSIGNED_FILE, {})
    save_json_file(BASELINE_FILE, {})
    save_json_file(CONTROL_FILE, {"mode": "self_fast"})
    save_attack_targets({})
    save_attack_round({"used_attackers": []})
    save_attack_pending({})
    save_json_file(ATTACK_CONDITION_FILE, {}, log=False)

    print("[API] サーバーデータを完全初期化しました")
    return jsonify({
        "status": "ok",
        "message": "サーバーを完全リセットしました"
    })


@app.route('/reset_game', methods=['POST'])
def reset_game_only():
    save_json_file(CSV_HISTORY_FILE, [], log=False)
    return jsonify({"status": "ok", "message": "CSVデータを消去しました"})

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
    assigned_ids = load_json_file(ASSIGNED_FILE)
    return jsonify({
        "count": len(assigned_ids),
        "ids": assigned_ids
    })

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

    history = load_csv_history()
    if not history:
        return jsonify({"status": "error", "message": "保存するCSVデータがありません"}), 400

    # ファイル名生成と保存先フォルダ
    timestamp = int(time.time())
    filename = f"heart_rate_data_{timestamp}.csv"
    filepath = os.path.join("data", filename)
    os.makedirs("data", exist_ok=True)

    with open(filepath, mode='w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(CSV_COLUMNS)
        for row in history:
            writer.writerow([format_csv_value(column, row.get(column, "")) for column in CSV_COLUMNS])

    print(f"[CSV保存] {filepath} に保存されました")

    # クライアントにファイル送信（ダウンロード）
    return send_file(filepath, as_attachment=True, download_name="heart_rate_data.csv")

@app.route("/get_control_mode")
def get_control_mode():
    if os.path.exists(CONTROL_FILE):
        with open(CONTROL_FILE) as f:
            return jsonify(json.load(f))
    return jsonify({"mode": "self_fast"})


@app.route('/get_rotation_settings')
def get_rotation_settings():
    return jsonify(load_rotation_settings())


@app.route('/get_rotation_direction')
def get_rotation_direction():
    return jsonify({"direction": load_rotation_settings()["direction"]})


@app.route('/set_rotation_direction', methods=['POST'])
def set_rotation_direction():
    data = request.get_json(silent=True) or {}
    direction = data.get("direction")
    if direction not in {"auto", "c", "a"}:
        return jsonify({"status": "error", "message": "directionはauto、c、aのいずれかです"}), 400

    settings = load_rotation_settings()
    settings["direction"] = direction
    save_rotation_settings(settings)
    return jsonify({"status": "ok", "direction": direction})


@app.route('/get_rotation_hold')
def get_rotation_hold():
    return jsonify({"hold": load_rotation_settings()["hold"]})


@app.route('/get_rotation_status')
def get_rotation_status():
    return jsonify(load_json_file(ROTATION_STATUS_FILE))


@app.route('/set_rotation_status', methods=['POST'])
def set_rotation_status():
    data = request.get_json(silent=True) or {}
    motor_watch = data.get("motor_watch")
    target_watch = data.get("target_watch")
    mode = data.get("mode")

    if not all(isinstance(value, str) and value for value in (motor_watch, target_watch, mode)):
        return jsonify({"status": "error", "message": "motor_watch、target_watch、modeが必要です"}), 400

    try:
        rpm = float(data.get("rpm"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "rpmは数値で指定してください"}), 400

    direction = data.get("direction")
    if direction not in {"c", "a"}:
        return jsonify({"status": "error", "message": "directionはcまたはaで指定してください"}), 400

    extreme = data.get("extreme")
    if extreme not in {None, "up", "down"}:
        return jsonify({"status": "error", "message": "extremeはupまたはdownで指定してください"}), 400

    attackers = data.get("attackers", [])
    if not isinstance(attackers, list) or not all(isinstance(attacker, str) for attacker in attackers):
        return jsonify({"status": "error", "message": "attackersはwatch IDの配列で指定してください"}), 400

    status = load_json_file(ROTATION_STATUS_FILE)
    status[motor_watch] = {
        "target_watch": target_watch,
        "mode": mode,
        "rpm": rpm,
        "direction": direction,
        "extreme": extreme,
        "attackers": attackers,
        "attack_count": len(attackers),
    }
    save_rotation_status(status)
    record_csv_snapshot(target_watch, mode, rpm, direction, extreme)
    return jsonify({"status": "ok"})


@app.route('/set_rotation_hold', methods=['POST'])
def set_rotation_hold():
    data = request.get_json(silent=True) or {}
    hold = data.get("hold")
    if not isinstance(hold, bool):
        return jsonify({"status": "error", "message": "holdはtrueまたはfalseで指定してください"}), 400

    settings = load_rotation_settings()
    settings["hold"] = hold
    save_rotation_settings(settings)
    return jsonify({"status": "ok", "hold": hold})


@app.route("/set_control_mode", methods=["POST"])
def set_control_mode():
    data = request.get_json()
    mode = data.get("mode", "self_fast")

    allowed_modes = {
        "self_fast",
        "self_slow",
        "next_fast",
        "prev_fast",
        "random_fast",
        "highest_diff",
        "lowest_diff",
        "random_diff",
        "attack_challenge",
    }

    if mode not in allowed_modes:
        return jsonify({
            "status": "error",
            "message": "無効なモードです"
        }), 400

    assigned_ids = load_json_file(ASSIGNED_FILE)
    watch_ids = set(assigned_ids.values())

    # 他人参照モードは2台以上必要
    if mode in {"next_fast", "prev_fast", "random_fast"} and len(watch_ids) < 2:
        return jsonify({
            "status": "error",
            "message": "このモードは2台以上接続されていないと使用できません"
        }), 400

    with open(CONTROL_FILE, "w") as f:
        json.dump({"mode": mode}, f)

    if mode == "attack_challenge":
        save_attack_targets({})
        save_attack_pending({})
        save_json_file(ATTACK_CONDITION_FILE, {}, log=False)

    print("[CONTROL MODE]", mode)
    return jsonify({
        "status": "ok",
        "mode": mode,
        "message": f"{mode} に変更しました"
    })


@app.route('/attack_targets')
def get_attack_targets():
    return jsonify(load_attack_targets())


@app.route('/set_attack_target', methods=['POST'])
def set_attack_target():
    data = request.get_json(silent=True) or {}
    attacker = data.get("attacker")
    target = data.get("target")
    valid_watches = {f"watch{number}" for number in range(1, 5)}
    if attacker not in valid_watches or (target is not None and target not in valid_watches) or attacker == target:
        return jsonify({"status": "error", "message": "不正な攻撃対象です"}), 400
    targets = load_attack_targets()
    targets[attacker] = target
    save_attack_targets(targets)
    return jsonify({"status": "ok", "attacker": attacker, "target": target})


@app.route('/attack_signal', methods=['POST'])
def receive_attack_signal():
    """Pixel Watchからの妨害信号を受け取り、現在の妨害対象へ反映する。"""
    data = request.get_json(silent=True) or {}
    attacker = data.get("attacker")
    current_turn = load_json_file(TURN_FILE).get("current_turn")
    target = data.get("target", current_turn)
    assigned_watches = set(load_json_file(ASSIGNED_FILE).values())

    if attacker not in assigned_watches:
        return jsonify({"status": "error", "message": "送信元watchが接続されていません"}), 400
    if target not in assigned_watches or attacker == target:
        return jsonify({"status": "error", "message": "自分自身への妨害はできません"}), 400

    round_state = update_attack_round_for_turn(current_turn, assigned_watches)
    used_attackers = set(round_state.get("used_attackers", []))
    if attacker in used_attackers:
        return jsonify({"status": "error", "message": "このラウンドでは既に妨害信号を送信しています"}), 409

    mode = load_json_file(CONTROL_FILE).get("mode")
    targets = load_attack_targets()
    used_attackers.add(attacker)
    round_state["used_attackers"] = sorted(used_attackers)

    if mode == "attack_challenge":
        condition = get_attack_challenge_condition()
        pending = load_attack_pending()
        pending[attacker] = {"target": target, "turn": condition.get("turn")}
        save_attack_pending(pending)
        save_attack_targets(targets)
        save_attack_round(round_state)
        threshold = attack_threshold(attacker, condition)
        return jsonify({
            "status": "pending",
            "attacker": attacker,
            "target": target,
            "direction": condition["direction"],
            "threshold": threshold,
            "message": "心拍条件を満たすと妨害が有効になります",
        })

    targets[attacker] = target
    save_attack_targets(targets)
    save_attack_round(round_state)
    attackers = sorted(source for source, attack_target in targets.items() if attack_target == target)
    return jsonify({
        "status": "ok",
        "attacker": attacker,
        "target": target,
        "attackers": attackers,
        "attack_count": len(attackers),
        "used_attackers": sorted(used_attackers),
    })


@app.route('/current_attackers')
def get_current_attackers():
    current_turn = load_json_file(TURN_FILE).get("current_turn")
    update_attack_round_for_turn(current_turn, set(load_json_file(ASSIGNED_FILE).values()))
    if load_json_file(CONTROL_FILE).get("mode") == "attack_challenge":
        resolve_attack_challenge()
    attackers = sorted(attacker for attacker, target in load_attack_targets().items() if target == current_turn)
    return jsonify({"current_turn": current_turn, "attackers": attackers, "attack_count": len(attackers)})


@app.route('/attack_status')
def get_attack_status():
    game_status = load_json_file(GAME_STATUS_FILE)
    current_turn = load_json_file(TURN_FILE).get("current_turn")
    mode = load_json_file(CONTROL_FILE).get("mode")
    update_attack_round_for_turn(current_turn, set(load_json_file(ASSIGNED_FILE).values()))
    if mode != "attack_challenge":
        attackers = sorted(attacker for attacker, target in load_attack_targets().items() if target == current_turn)
        return jsonify({"round": game_status.get("round"), "current_turn": current_turn, "attackers": attackers})

    condition = None
    pending = {}
    condition, pending, _, _ = resolve_attack_challenge()
    attackers = sorted(attacker for attacker, target in load_attack_targets().items() if target == current_turn)
    pending_attackers = sorted(attacker for attacker, signal in pending.items() if signal.get("target") == current_turn)
    thresholds = {
        attacker: attack_threshold(attacker, condition)
        for attacker in pending_attackers
    } if condition else {}
    heartbeats = get_latest_heartbeats()
    challenge_requirements = {
        watch_id: {
            "heartbeat": heartbeats.get(watch_id),
            "threshold": attack_threshold(watch_id, condition),
            "status": "達成" if watch_id in attackers else "挑戦中" if watch_id in pending_attackers else "未挑戦",
        }
        for watch_id in sorted(set(load_json_file(ASSIGNED_FILE).values()))
        if watch_id != current_turn
    }
    return jsonify({
        "round": game_status.get("round"),
        "current_turn": current_turn,
        "attackers": attackers,
        "pending_attackers": pending_attackers,
        "attack_mode": mode == "attack_challenge",
        "challenge_direction": condition.get("direction") if condition else None,
        "pending_thresholds": thresholds,
        "challenge_requirements": challenge_requirements,
    })

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