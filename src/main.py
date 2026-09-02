from flask import Flask, jsonify, send_from_directory, request
import os
import json
import threading
import csv
import time  # ← CSV保存に必要
import random
import re
import tempfile
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from score_logic import (
    ATTACK_SCORING_MODES,
    apply_points,
    attack_challenge_score_awards,
    apply_ranking_bonus,
    calculate_final_ranking,
    calculate_interference_ranking,
    calculate_set_score,
    normalize_scores,
    normalize_series_scores,
    turn_scoring_targets,
)

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
MANUAL_ROTATION_FILE = os.path.join(BASE_DIR, "manual_rotation.json")
SCORES_FILE = os.path.join(BASE_DIR, "scores.json")
ATTACK_TARGETS_FILE = os.path.join(BASE_DIR, "attack_targets.json")
ATTACK_ROUND_FILE = os.path.join(BASE_DIR, "attack_round.json")
ATTACK_PENDING_FILE = os.path.join(BASE_DIR, "attack_pending.json")
ATTACK_CONDITION_FILE = os.path.join(BASE_DIR, "attack_condition.json")
ATTACK_SUCCESS_FILE = os.path.join(BASE_DIR, "attack_success.json")
ATTACK_SCORING_FILE = os.path.join(BASE_DIR, "attack_scoring.json")
CSV_HISTORY_FILE = os.path.join(BASE_DIR, "csv_history.json")
LIVE_CSV_FILE = os.path.join(BASE_DIR, "live_rotation.csv")
JENGA_SERIES_FILE = os.path.join(BASE_DIR, "jenga_series.json")
CSV_COLUMNS = [
    "timestamp", "device_id", "heartbeat", "baseline", "diff", "abs_diff",
    "game_phase", "current_turn", "control_mode", "random_extreme",
    "target_watch", "is_target", "rpm", "direction", "source_timestamp", "collapse",
    "attackers", "attack_count", "attack_mode", "challenge_direction",
    "score", "score_change", "score_reason", "series_id", "game_number",
]
CSV_INTEGER_COLUMNS = {"heartbeat", "baseline", "diff", "abs_diff", "rpm"}
# ゲーム用: 妨害チャレンジのノルマ心拍を決める設定。
# 各watchの初回妨害だけ平均心拍を基準にし、2回目以降はターン交代時心拍を基準にする。
# 計算例:
#   初回・上昇、平均70 BPM                 -> 70 + 10 = 80 BPM
#   初回・下降、平均70 BPM                 -> 70 - 3  = 67 BPM
#   上昇後に下降、交代時70 BPM             -> 70 - 5  = 65 BPM
#   下降後に下降、交代時70 BPM             -> 70 - 2  = 68 BPM
#   下降後に上昇、交代時70 BPM             -> 70 + 20 = 90 BPM
#   上昇後に上昇、交代時70 BPM             -> 70 + 10 = 80 BPM
ATTACK_CHALLENGE_RULES = {
    "first_up_baseline_offset": 10, #最初はbaselineから10上げる
    "first_down_baseline_offset": -3, #最初はbaselineから3下げる
    "down_after_up_turn_start_offset": -5, #上昇ターンの後の下降はターン開始時の心拍数から5下げる
    "down_repeat_turn_start_offset": -2, #下降ターンの後の下降はターン開始時の心拍数から2下げる
    "up_after_down_turn_start_offset": 20, #下降ターンの後の上昇はターン開始時の心拍数から20上げる
    "up_repeat_turn_start_offset": 10,#上昇ターンの後の上昇はターン開始時の心拍数から10上げる
}


# -------------------------
# 共通ヘルパー
# -------------------------
def save_json_file(filename, data, log=True):
    """Write JSON atomically so a stopped process cannot leave a truncated state file."""
    with file_lock:
        directory = os.path.dirname(os.path.abspath(filename))
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as temporary_file:
            temporary_path = temporary_file.name
            json.dump(data, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, filename)
    if log:
        print(f"[ファイル書き込み] {filename} -> {data}")

def load_json_file(filename):
    with file_lock:
        if os.path.exists(filename):
            try:
                with open(filename, encoding="utf-8") as f:
                    content = f.read().strip()
                    if not content:
                        return {}
                    return json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                print(f"[WARN] Invalid JSON file: {filename}")
                return {}
        return {}


def load_rotation_settings():
    settings = load_json_file(ROTATION_SETTINGS_FILE)
    direction = settings.get("direction", "auto")
    if direction not in {"auto", "c", "a"}:
        direction = "auto"
    hold = settings.get("hold", True)
    return {"direction": direction, "hold": hold if isinstance(hold, bool) else True}


def is_game_start_allowed():
    """手動テストモード選択中はゲーム開始を禁止する。"""
    mode = load_json_file(CONTROL_FILE).get("mode")
    return mode != "manual_test"


def save_rotation_settings(settings):
    save_json_file(ROTATION_SETTINGS_FILE, settings)


def save_rotation_status(status):
    save_json_file(ROTATION_STATUS_FILE, status, log=False)


def load_manual_rotation():
    settings = load_json_file(MANUAL_ROTATION_FILE)
    if not isinstance(settings, dict):
        settings = {}
    enabled = bool(settings.get("enabled", False))
    mode = settings.get("mode") or settings.get("direction") or "c"
    if mode not in {"c", "a", "random"}:
        mode = "c"
    try:
        rpm = int(float(settings.get("rpm", 10)))
    except (TypeError, ValueError):
        rpm = 10
    rpm = max(0, min(rpm, 60))
    return {"enabled": enabled, "rpm": rpm, "mode": mode, "direction": mode if mode in {"c", "a"} else "c"}


def save_manual_rotation(settings):
    save_json_file(MANUAL_ROTATION_FILE, settings, log=False)


def load_scores():
    return normalize_scores(load_json_file(SCORES_FILE))


def load_attack_scoring():
    scoring = load_json_file(ATTACK_SCORING_FILE)
    mode = scoring.get("mode") if isinstance(scoring, dict) else None
    return {"mode": mode if mode in ATTACK_SCORING_MODES else "success"}


def load_jenga_series():
    state = load_json_file(JENGA_SERIES_FILE)
    if not isinstance(state, dict):
        state = {}
    try:
        game_number = max(1, int(state.get("game_number", 1)))
    except (TypeError, ValueError):
        game_number = 1
    try:
        total_sets = max(1, int(state.get("total_sets", 3)))
    except (TypeError, ValueError):
        total_sets = 3
    watches = state.get("watch_ids", []) if isinstance(state.get("watch_ids"), list) else []
    return {
        "series_id": state.get("series_id"),
        "game_number": game_number,
        "total_sets": total_sets,
        "active": bool(state.get("active", False)),
        "set_finished": bool(state.get("set_finished", False)),
        "scoring_mode": state.get("scoring_mode") if state.get("scoring_mode") in ATTACK_SCORING_MODES else "success",
        "watch_ids": sorted({watch_id for watch_id in watches if isinstance(watch_id, str) and watch_id}),
        "scores": state.get("scores", {}) if isinstance(state.get("scores"), dict) else {},
        "set_history": state.get("set_history", []) if isinstance(state.get("set_history"), list) else [],
        "current_set_events": state.get("current_set_events", []) if isinstance(state.get("current_set_events"), list) else [],
        "attack_events": state.get("attack_events", []) if isinstance(state.get("attack_events"), list) else [],
        "final_ranking": state.get("final_ranking", []) if isinstance(state.get("final_ranking"), list) else [],
        "score_history": state.get("set_history", []) if isinstance(state.get("set_history"), list) else [],
        "last_set_result": state.get("last_set_result") if isinstance(state.get("last_set_result"), dict) else {},
    }


def save_jenga_series(series):
    watches = series.get("watch_ids", [])
    series["scores"] = normalize_series_scores(series.get("scores"), watches)
    save_json_file(JENGA_SERIES_FILE, series, log=False)


def initialize_jenga_series(watch_ids, total_sets, scoring_mode):
    watches = sorted({watch_id for watch_id in watch_ids if isinstance(watch_id, str) and watch_id}, key=get_watch_sort_key)
    series = {
        "series_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "game_number": 1,
        "total_sets": total_sets,
        "active": True,
        "set_finished": False,
        "scoring_mode": scoring_mode,
        "watch_ids": watches,
        "scores": normalize_series_scores({}, watches),
        "set_history": [],
        "current_set_events": [],
        "attack_events": [],
        "final_ranking": [],
        "last_set_result": {},
    }
    save_jenga_series(series)
    save_json_file(SCORES_FILE, {watch_id: 0 for watch_id in watches}, log=False)
    return series


def record_attack_success_event(attacker, success):
    series = load_jenga_series()
    if not series["active"] or series["set_finished"] or attacker not in series["watch_ids"]:
        return
    event = {
        "attacker": attacker,
        "timestamp": int(time.time() * 1000),
        "impact": success.get("impact", 0),
        "threshold": success.get("threshold"),
        "heartbeat": success.get("heartbeat"),
        "direction": success.get("direction"),
    }
    series["current_set_events"].append(event)
    series["attack_events"].append(event)
    save_jenga_series(series)


def finish_set(collapsed_player):
    """Finalize exactly one set, keeping cumulative scores for the next set."""
    series = load_jenga_series()
    if not series["active"] or series["set_finished"]:
        return None

    scores, set_points, mvp = calculate_set_score(
        series["scores"],
        series["watch_ids"],
        collapsed_player,
        series["current_set_events"],
        series["scoring_mode"],
    )
    survivors = [watch_id for watch_id in series["watch_ids"] if watch_id != collapsed_player]
    success_counts = {
        watch_id: sum(1 for event in series["current_set_events"] if event.get("attacker") == watch_id)
        for watch_id in series["watch_ids"]
    }
    result = {
        "set": series["game_number"],
        "collapsed_player": collapsed_player,
        "survivors": survivors,
        "interference_success": success_counts,
        "mvp": mvp,
        "set_points": set_points,
        "scores": scores,
    }
    series["scores"] = scores
    series["set_history"].append(result)
    series["last_set_result"] = result
    series["set_finished"] = True

    if series["game_number"] >= series["total_sets"]:
        if series["scoring_mode"] == "ranking":
            series["scores"], _ = apply_ranking_bonus(
                series["scores"], series["attack_events"], series["watch_ids"]
            )
        series["final_ranking"] = calculate_final_ranking(series["scores"], series["watch_ids"])
        series["active"] = False

    save_jenga_series(series)
    save_json_file(SCORES_FILE, {
        watch_id: entry["total_score"] for watch_id, entry in series["scores"].items()
    }, log=False)
    return series


def start_next_set():
    series = load_jenga_series()
    if not series["active"] or not series["set_finished"] or series["game_number"] >= series["total_sets"]:
        return None
    series["game_number"] += 1
    series["set_finished"] = False
    series["current_set_events"] = []
    save_jenga_series(series)
    return series


def jenga_csv_fields():
    series = load_jenga_series()
    if not series["active"]:
        return {"series_id": "", "game_number": ""}
    return {"series_id": series["series_id"] or "", "game_number": series["game_number"]}


def award_turn_scores(current_turn):
    if load_jenga_series()["active"]:
        return []
    control_mode = load_json_file(CONTROL_FILE).get("mode")
    attack_success = load_attack_success()
    if control_mode in {"attack_challenge", "attack_challenge_wait"}:
        awards = attack_challenge_score_awards(
            attack_success,
            current_turn,
            load_attack_scoring()["mode"],
        )
        if not awards:
            return []

        scores = load_scores()
        timestamp = int(time.time() * 1000)
        history = load_csv_history()
        for watch_id in sorted(awards):
            award = awards[watch_id]
            scores = apply_points(scores, [watch_id], award["points"])
            history.append({
                "timestamp": timestamp,
                "device_id": watch_id,
                "game_phase": "playing",
                "current_turn": current_turn or "",
                "control_mode": control_mode,
                "score": scores[watch_id],
                "score_change": award["points"],
                "score_reason": " / ".join(award["reasons"]),
                **jenga_csv_fields(),
            })
        save_json_file(SCORES_FILE, scores, log=False)
        save_json_file(CSV_HISTORY_FILE, history, log=False)
        return sorted(awards)

    targets = turn_scoring_targets(
        control_mode,
        load_json_file(ROTATION_STATUS_FILE),
        attack_success,
        current_turn,
    )
    if targets:
        scores = apply_points(load_scores(), targets, 1)
        save_json_file(SCORES_FILE, scores, log=False)
        timestamp = int(time.time() * 1000)
        reason = "妨害チャレンジ成功" if control_mode in {"attack_challenge", "attack_challenge_wait"} else "上昇・下降モードで心拍採用"
        history = load_csv_history()
        history.extend({
            "timestamp": timestamp,
            "device_id": watch_id,
            "game_phase": "playing",
            "current_turn": current_turn or "",
            "control_mode": control_mode or "",
            "score": scores[watch_id],
            "score_change": 1,
            "score_reason": reason,
            **jenga_csv_fields(),
        } for watch_id in sorted(targets))
        save_json_file(CSV_HISTORY_FILE, history, log=False)
    return sorted(targets)


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


def record_csv_snapshot(target_watch, mode, rpm, direction, extreme=None, attackers=None, attack_context=None):
    if not load_json_file(GAME_STATUS_FILE).get("running", False):
        return

    heart_data = load_json_file(DATA_FILE)
    baselines = load_json_file(BASELINE_FILE)
    current_turn = load_json_file(TURN_FILE).get("current_turn")
    timestamp = int(time.time() * 1000)
    rows = []
    attackers_list = [attacker for attacker in (attackers or []) if isinstance(attacker, str)]
    attack_context = attack_context or {}
    attack_mode = "attack_challenge" if bool(attack_context.get("attack_mode")) else ""
    challenge_direction = attack_context.get("challenge_direction") or ""
    attack_count = attack_context.get("attack_count", len(attackers_list))

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
            "attackers": ",".join(attackers_list),
            "attack_count": attack_count,
            "attack_mode": attack_mode,
            "challenge_direction": challenge_direction,
            "score": load_scores().get(device_id, 0),
            "score_change": "",
            "score_reason": "",
            **jenga_csv_fields(),
        })

    if rows:
        history = load_csv_history()
        history.extend(rows)
        save_json_file(CSV_HISTORY_FILE, history, log=False)
        with open(LIVE_CSV_FILE, mode="a", encoding="utf-8", newline="") as csvfile:
            writer = csv.writer(csvfile)
            if os.path.getsize(LIVE_CSV_FILE) == 0:
                writer.writerow(CSV_COLUMNS)
            for row in rows:
                writer.writerow([format_csv_value(column, row.get(column, "")) for column in CSV_COLUMNS])


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


def reset_attack_cycle_state(reset_condition=True):
    """妨害参加状態を初期化する。ターン継続中は直前の条件履歴を残せる。"""
    save_attack_targets({})
    save_attack_pending({})
    save_attack_success({})
    save_attack_round({"used_attackers": [], "seen_turns": [], "last_turn": None, "completed": False})
    if reset_condition:
        save_json_file(ATTACK_CONDITION_FILE, {}, log=False)


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
        # 一周後は再妨害を許可するが、次ターンのノルマ計算に必要な
        # previous_direction は消さない。
        reset_attack_cycle_state(reset_condition=False)
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


def should_allow_attack(attacker, current_turn, assigned_watches):
    """A watch may only attack once per full round of turns."""
    if not attacker or not current_turn:
        return False
    round_state = load_attack_round()
    if not isinstance(round_state, dict):
        return True

    used_attackers = set(round_state.get("used_attackers", []))
    assigned = {watch for watch in assigned_watches if isinstance(watch, str) and watch}

    if attacker in used_attackers:
        return False

    # まだ全員が1巡していない間は、同じ攻撃者による再妨害をブロックする。
    if assigned and current_turn in assigned and attacker in assigned and attacker != current_turn:
        return True

    return False


def get_watch_sort_key(watch_id):
    match = re.search(r"(\d+)$", str(watch_id) or "")
    if match:
        return (0, int(match.group(1)))
    return (1, str(watch_id))


def is_reset_turn(current_turn, assigned_watches):
    if not current_turn:
        return False
    watches = [watch for watch in assigned_watches if isinstance(watch, str) and watch]
    if not watches:
        return False
    return current_turn == sorted(watches, key=get_watch_sort_key)[0]


def load_attack_success():
    success_state = load_json_file(ATTACK_SUCCESS_FILE)
    return success_state if isinstance(success_state, dict) else {}


def save_attack_success(success_state):
    save_json_file(ATTACK_SUCCESS_FILE, success_state, log=False)


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


def get_allowed_attack_targets(attacker, assigned_watches=None):
    """Return the watch order for valid targets based on one full cycle around the connected watches."""
    watches = [watch for watch in (assigned_watches or set(load_json_file(ASSIGNED_FILE).values())) if isinstance(watch, str) and watch]
    if not watches:
        return []

    ordered = sorted(watches, key=get_watch_sort_key)
    if attacker not in ordered:
        return []

    index = ordered.index(attacker)
    return ordered[index + 1:] + ordered[:index]


def get_attack_challenge_condition():
    """Return the stable per-turn challenge condition without resetting the full attack cycle on the first watch."""
    current_turn = load_json_file(TURN_FILE).get("current_turn")
    condition = load_json_file(ATTACK_CONDITION_FILE)

    if condition.get("turn") == current_turn and condition.get("direction") in {"up", "down"}:
        return condition

    if isinstance(condition, dict) and condition.get("turn") and condition.get("turn") != current_turn:
        reset_attack_cycle_state(reset_condition=False)

    previous_direction = condition.get("direction") if isinstance(condition, dict) else None
    experienced_attackers = set(condition.get("experienced_attackers", []))
    experienced_attackers.update(condition.get("attackers_this_turn", []))
    is_first_turn = not condition.get("turn")
    direction = random.choice(["up", "down"])
    condition = {
        "turn": current_turn,
        "direction": direction,
        "previous_direction": previous_direction,
        "first_turn": is_first_turn,
        "experienced_attackers": sorted(experienced_attackers),
        "attackers_this_turn": [],
        "turn_start_heartbeats": get_latest_heartbeats(),
    }
    save_json_file(ATTACK_CONDITION_FILE, condition, log=False)
    return condition


def attack_threshold(attacker, condition):
    """妨害者ごとの基準心拍に、現在・前回の方向に対応する設定値を加えてノルマを返す。"""
    reference_bpm, reference_source = attack_reference(attacker, condition)
    if reference_bpm is None:
        return None

    direction = condition["direction"]
    if reference_source == "baseline":
        offset = ATTACK_CHALLENGE_RULES["first_up_baseline_offset"] if direction == "up" else ATTACK_CHALLENGE_RULES["first_down_baseline_offset"]
        return reference_bpm + offset

    previous_direction = condition.get("previous_direction")
    if direction == "down":
        offset = (
            ATTACK_CHALLENGE_RULES["down_repeat_turn_start_offset"]
            if previous_direction == "down"
            else ATTACK_CHALLENGE_RULES["down_after_up_turn_start_offset"]
        )
        return reference_bpm + offset

    offset = ATTACK_CHALLENGE_RULES["up_repeat_turn_start_offset"] if previous_direction == "up" else ATTACK_CHALLENGE_RULES["up_after_down_turn_start_offset"]
    return reference_bpm + offset


def attack_reference(attacker, condition):
    """
    妨害ノルマの比較基準をwatchごとに返す。
    未妨害なら本人の平均心拍、妨害経験済みなら今回のターン交代時心拍を使う。
    """
    baselines = load_json_file(BASELINE_FILE)
    try:
        baseline = float(baselines[attacker])
    except (KeyError, TypeError, ValueError):
        return None, None

    experienced_attackers = set(condition.get("experienced_attackers", []))
    if attacker not in experienced_attackers:
        return baseline, "baseline"

    turn_start = condition.get("turn_start_heartbeats", {})
    try:
        start_bpm = float(turn_start[attacker])
    except (KeyError, TypeError, ValueError):
        return None, None
    return start_bpm, "turn_start"


def resolve_attack_challenge():
    """Promote pending attack signals once their sender meets this turn's condition."""
    condition = get_attack_challenge_condition()
    pending = load_attack_pending()
    active_targets = load_attack_targets()
    heartbeats = get_latest_heartbeats()
    success_state = load_attack_success()
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
            reference_bpm, reference_source = attack_reference(attacker, condition)
            impact = heartbeat - threshold if condition["direction"] == "up" else threshold - heartbeat
            active_targets[attacker] = signal["target"]
            del pending[attacker]
            success_state[attacker] = {
                "turn": condition.get("turn"),
                "target": signal.get("target"),
                "direction": condition.get("direction"),
                "heartbeat": heartbeat,
                "reference_bpm": reference_bpm,
                "reference_source": reference_source,
                "threshold": threshold,
                "impact": impact,
            }
            record_attack_success_event(attacker, success_state[attacker])
            resolved.append(attacker)

    if resolved:
        save_attack_targets(active_targets)
        save_attack_pending(pending)
        save_attack_success(success_state)

    return condition, pending, active_targets, resolved


def is_clear_attack_round_state(round_state):
    if not isinstance(round_state, dict):
        return True
    if round_state.get("used_attackers") or round_state.get("seen_turns"):
        return False
    if round_state.get("last_turn") is not None:
        return False
    if round_state.get("completed") is True:
        return False
    return True


def is_game_state_clear():
    game_status = load_json_file(GAME_STATUS_FILE)
    if game_status.get("running", False):
        return False

    if load_csv_history():
        return False
    if load_json_file(ATTACK_TARGETS_FILE):
        return False
    if load_json_file(ATTACK_PENDING_FILE):
        return False
    if load_json_file(ATTACK_SUCCESS_FILE):
        return False
    if not is_clear_attack_round_state(load_attack_round()):
        return False
    return True


@app.route('/start', methods=['POST'])
def start_game():
    if not is_game_start_allowed():
        return jsonify({"status": "error", "message": "手動テストモード中はゲームを開始できません。通常モードに戻してから開始してください"}), 409

    if not is_game_state_clear():
        return jsonify({"status": "error", "message": "ゲーム状態が残っています。まず「ゲームだけリセット」または「サーバー全体リセット」を実行してください"}), 400

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
    save_json_file(TURN_FILE, {
        "current_turn": ids[0] if ids else None,
        "turn_number": 1 if ids else 0,
    })
    if request.args.get("mode") == "jenga":
        try:
            total_sets = max(1, int(request.args.get("sets", 3)))
        except (TypeError, ValueError):
            total_sets = 3
        initialize_jenga_series(ids, total_sets, load_attack_scoring()["mode"])
        if os.path.exists(LIVE_CSV_FILE):
            with open(LIVE_CSV_FILE, "w", encoding="utf-8"):
                pass
    else:
        save_json_file(SCORES_FILE, {watch_id: 0 for watch_id in ids}, log=False)

    print("[GAME START] baseline完全一致 → 開始")
    return jsonify({"status": "ok", "message": "ゲームを開始しました"})


@app.route('/jenga_series', methods=['GET'])
def get_jenga_series():
    series = load_jenga_series()
    # ゲーム開始前は管理画面で選択中の得点方式を表示する。
    # 開始後は、そのシリーズ開始時に確定した方式を維持する。
    if not series["active"]:
        series["scoring_mode"] = load_attack_scoring()["mode"]
    return jsonify({
        **series,
        "interference_ranking": calculate_interference_ranking(series["attack_events"], series["watch_ids"]),
    })


@app.route('/jenga_settings', methods=['POST'])
def set_jenga_settings():
    """Save pre-game settings so every open screen can display the same values."""
    if load_json_file(GAME_STATUS_FILE).get("running", False):
        return jsonify({"status": "error", "message": "ゲーム中はセット数を変更できません"}), 409

    series = load_jenga_series()
    try:
        total_sets = int((request.get_json(silent=True) or {}).get("total_sets"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "セット数が不正です"}), 400
    if total_sets not in {1, 2, 3, 5}:
        return jsonify({"status": "error", "message": "セット数は1、2、3、5から選択してください"}), 400
    if series["active"] and total_sets < series["game_number"]:
        return jsonify({
            "status": "error",
            "message": f"現在SET {series['game_number']} のため、それ以上のセット数を選択してください",
        }), 409

    series["total_sets"] = total_sets
    series["scoring_mode"] = load_attack_scoring()["mode"]
    save_jenga_series(series)
    return jsonify({
        "status": "ok",
        "game_number": series["game_number"],
        "total_sets": total_sets,
        "scoring_mode": series["scoring_mode"],
    })


@app.route('/next_jenga_game', methods=['POST'])
def next_jenga_game():
    """確定済みセットの累計点を保持して次セットを開始する。"""
    status = load_json_file(GAME_STATUS_FILE)
    if status.get("running", False):
        return jsonify({
            "status": "error",
            "message": "ゲーム中です。先に「終了」ボタンでゲームを終了してください"
        }), 409

    assigned_ids = sorted(set(load_json_file(ASSIGNED_FILE).values()), key=get_watch_sort_key)
    if not assigned_ids:
        return jsonify({"status": "error", "message": "接続中のWatchがありません"}), 400

    baselines = load_json_file(BASELINE_FILE)
    missing = [watch_id for watch_id in assigned_ids if watch_id not in baselines]
    if missing:
        return jsonify({"status": "error", "message": "平均値が未取得です: " + ", ".join(missing)}), 400

    series = start_next_set()
    if not series:
        return jsonify({"status": "error", "message": "倒壊によるセット終了後、最終セット前のみ次セットを開始できます"}), 409

    reset_attack_cycle_state()
    save_json_file(ROTATION_STATUS_FILE, {}, log=False)
    save_json_file(TURN_FILE, {"current_turn": assigned_ids[0], "turn_number": 1}, log=False)
    status.update({"running": True, "game_over": False, "baseline_mode": False})
    save_json_file(GAME_STATUS_FILE, status, log=False)

    history = load_csv_history()
    history.append({
        "timestamp": int(time.time() * 1000),
        "game_phase": "game_start",
        "score_reason": f"ジェンガ第{series['game_number']}ゲーム開始",
        "series_id": series["series_id"],
        "game_number": series["game_number"],
    })
    save_json_file(CSV_HISTORY_FILE, history, log=False)
    return jsonify({
        "status": "ok",
        "game_number": series["game_number"],
        "scores": series["scores"],
        "score_history": series["set_history"],
    })


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
    global id_counter, clients

    save_json_file(DATA_FILE, {})
    save_json_file(CSV_HISTORY_FILE, [], log=False)
    save_json_file(GAME_STATUS_FILE, {
        "running": False,
        "game_over": False,
        "baseline_mode": False
    })
    save_json_file(TURN_FILE, {"current_turn": None, "turn_number": 0})
    save_json_file(ASSIGNED_FILE, {})
    save_json_file(BASELINE_FILE, {})
    save_json_file(SCORES_FILE, {}, log=False)
    save_json_file(ATTACK_SCORING_FILE, {"mode": "success"}, log=False)
    save_json_file(JENGA_SERIES_FILE, {}, log=False)
    save_json_file(CONTROL_FILE, {"mode": "self_fast"})
    reset_attack_cycle_state()
    save_attack_round({"used_attackers": [], "seen_turns": [], "last_turn": None, "completed": False})

    clients = {}
    id_counter = 1

    print("[API] サーバーデータを完全初期化しました")
    return jsonify({
        "status": "ok",
        "message": "サーバーを完全リセットしました"
    })


@app.route('/reset_game', methods=['POST'])
def reset_game_only():
    save_json_file(CSV_HISTORY_FILE, [], log=False)
    if os.path.exists(LIVE_CSV_FILE):
        with open(LIVE_CSV_FILE, "w", encoding="utf-8"):
            pass
    save_json_file(GAME_STATUS_FILE, {"running": False, "game_over": False, "baseline_mode": False}, log=False)
    save_json_file(TURN_FILE, {"current_turn": None, "turn_number": 0}, log=False)
    reset_attack_cycle_state()
    save_attack_round({"used_attackers": [], "seen_turns": [], "last_turn": None, "completed": False})
    save_json_file(ROTATION_SETTINGS_FILE, {"direction": "auto", "hold": True}, log=False)
    save_json_file(ROTATION_STATUS_FILE, {}, log=False)
    assigned_watches = set(load_json_file(ASSIGNED_FILE).values())
    save_json_file(SCORES_FILE, {watch_id: 0 for watch_id in assigned_watches}, log=False)
    save_json_file(JENGA_SERIES_FILE, {}, log=False)
    return jsonify({"status": "ok", "message": "ゲーム状態を完全にリセットしました"})

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
    turn_state = load_json_file(TURN_FILE)
    turn_number = turn_state.get("turn_number", 0)
    if turn_state.get("current_turn") != new_turn:
        current_turn = turn_state.get("current_turn")
        if load_json_file(CONTROL_FILE).get("mode") == "attack_challenge":
            # Capture a challenge completed immediately before the turn button was pressed.
            resolve_attack_challenge()
        award_turn_scores(current_turn)
        turn_number += 1
    save_json_file(TURN_FILE, {
        "current_turn": new_turn,
        "turn_number": turn_number,
    })
    print(f"[API] 管理者操作: ターンを {new_turn} に設定しました")
    return jsonify({"status": "ok", "message": f"{new_turn} に設定しました"})


@app.route('/scores', methods=['GET'])
def get_scores():
    series = load_jenga_series()
    if series["series_id"]:
        return jsonify(normalize_series_scores(series["scores"], series["watch_ids"]))
    scores = load_scores()
    for watch_id in set(load_json_file(ASSIGNED_FILE).values()):
        scores.setdefault(watch_id, 0)
    return jsonify(scores)


@app.route('/collapse', methods=['POST'])
def record_collapse():
    game_status = load_json_file(GAME_STATUS_FILE)
    if not game_status.get("running", False):
        return jsonify({"status": "error", "message": "ゲームを開始してください"}), 400

    current_turn = load_json_file(TURN_FILE).get("current_turn")
    if not current_turn:
        return jsonify({"status": "error", "message": "現在の手番が設定されていません"}), 400

    data = request.get_json(silent=True) or {}
    series = load_jenga_series()
    if series["active"]:
        series = finish_set(current_turn)
        game_status["running"] = False
        game_status["game_over"] = not series["active"]
        save_json_file(GAME_STATUS_FILE, game_status, log=False)
        result = series["last_set_result"]
        history = load_csv_history()
        history.append({
            "timestamp": int(time.time() * 1000),
            "device_id": current_turn,
            "game_phase": "set_end",
            "current_turn": current_turn,
            "collapse": data.get("notes") or data.get("message") or "倒壊",
            "score": series["scores"][current_turn]["total_score"],
            "score_change": 0,
            "score_reason": f"SET {result['set']} 終了",
            **jenga_csv_fields(),
        })
        save_json_file(CSV_HISTORY_FILE, history, log=False)
        return jsonify({
            "status": "ok",
            "watch_id": current_turn,
            "set_finished": True,
            "series_complete": not series["active"],
            "scores": series["scores"],
            "set_result": result,
            "final_ranking": series["final_ranking"],
        })

    scores = apply_points(load_scores(), [current_turn], -3)
    save_json_file(SCORES_FILE, scores, log=False)

    # Exported data keeps a lightweight collapse marker as well.
    history = load_csv_history()
    history.append({
        "timestamp": int(time.time() * 1000),
        "device_id": current_turn,
        "game_phase": "playing",
        "current_turn": current_turn,
        "collapse": data.get("notes") or data.get("message") or "倒壊",
        "score": scores[current_turn],
        "score_change": -3,
        "score_reason": "倒壊",
        **jenga_csv_fields(),
    })
    save_json_file(CSV_HISTORY_FILE, history, log=False)
    return jsonify({"status": "ok", "watch_id": current_turn, "score": scores[current_turn]})

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


@app.route('/attack_scoring', methods=['GET'])
def get_attack_scoring():
    return jsonify(load_attack_scoring())


@app.route('/attack_scoring', methods=['POST'])
def set_attack_scoring():
    if load_json_file(GAME_STATUS_FILE).get("running", False):
        return jsonify({"status": "error", "message": "ゲーム中は妨害チャレンジの得点方式を変更できません"}), 409
    if load_jenga_series()["active"]:
        return jsonify({"status": "error", "message": "連続ゲーム中は妨害チャレンジの得点方式を変更できません"}), 409

    control_mode = load_json_file(CONTROL_FILE).get("mode")
    if control_mode not in {"attack_challenge", "attack_challenge_wait"}:
        return jsonify({"status": "error", "message": "妨害チャレンジ選択中のみ得点方式を変更できます"}), 409

    mode = (request.get_json(silent=True) or {}).get("mode")
    if mode not in ATTACK_SCORING_MODES:
        return jsonify({"status": "error", "message": "無効な妨害チャレンジ得点方式です"}), 400

    save_json_file(ATTACK_SCORING_FILE, {"mode": mode}, log=False)
    series = load_jenga_series()
    if not series["active"]:
        series["scoring_mode"] = mode
        save_jenga_series(series)
    return jsonify({
        "status": "ok",
        "mode": mode,
        "game_number": series["game_number"],
        "total_sets": series["total_sets"],
    })


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

    def optional_number(field_name):
        value = data.get(field_name)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(field_name)

    try:
        reference_bpm = optional_number("reference_bpm")
        baseline_bpm = optional_number("baseline_bpm")
    except ValueError as error:
        return jsonify({"status": "error", "message": f"{error.args[0]}は数値で指定してください"}), 400

    reference_source = data.get("reference_source")
    if reference_source not in {None, "baseline", "turn_start"}:
        return jsonify({"status": "error", "message": "reference_sourceが不正です"}), 400

    reference_heartbeats = data.get("reference_heartbeats", {})
    if not isinstance(reference_heartbeats, dict):
        return jsonify({"status": "error", "message": "reference_heartbeatsはwatchごとの心拍数で指定してください"}), 400
    try:
        reference_heartbeats = {watch_id: float(heartbeat) for watch_id, heartbeat in reference_heartbeats.items()}
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "reference_heartbeatsの心拍数は数値で指定してください"}), 400

    attack_context = {
        "attack_mode": bool(data.get("attack_mode")),
        "challenge_direction": data.get("challenge_direction"),
        "pending_attackers": data.get("pending_attackers", []),
        "attack_count": data.get("attack_count", len(attackers)),
    }

    status = load_json_file(ROTATION_STATUS_FILE)
    status[motor_watch] = {
        "target_watch": target_watch,
        "mode": mode,
        "rpm": rpm,
        "direction": direction,
        "extreme": extreme,
        "attackers": attackers,
        "attack_count": len(attackers),
        "reference_bpm": reference_bpm,
        "reference_source": reference_source,
        "baseline_bpm": baseline_bpm,
        "reference_heartbeats": reference_heartbeats,
        "attack_mode": attack_context["attack_mode"],
        "challenge_direction": attack_context["challenge_direction"],
        "pending_attackers": attack_context["pending_attackers"],
    }
    save_rotation_status(status)
    record_csv_snapshot(target_watch, mode, rpm, direction, extreme, attackers=attackers, attack_context=attack_context)
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


@app.route('/manual_rotation', methods=['GET'])
def get_manual_rotation():
    return jsonify(load_manual_rotation())


@app.route('/set_manual_rotation', methods=['POST'])
def set_manual_rotation():
    if load_json_file(GAME_STATUS_FILE).get("running", False):
        return jsonify({"status": "error", "message": "ゲーム中は手動テスト回転を変更できません"}), 409

    data = request.get_json(silent=True) or {}
    mode = data.get("mode") or data.get("direction") or "c"
    if mode not in {"c", "a", "random"}:
        return jsonify({"status": "error", "message": "modeはc、a、randomのいずれかで指定してください"}), 400

    try:
        rpm = int(float(data.get("rpm", 10)))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "rpmは数値で指定してください"}), 400

    rpm = max(0, min(rpm, 60))
    enabled = bool(data.get("enabled", True))

    settings = {"enabled": enabled, "rpm": rpm, "mode": mode, "direction": mode if mode in {"c", "a"} else "c"}
    save_manual_rotation(settings)
    return jsonify({"status": "ok", "manual_rotation": settings})


@app.route('/clear_manual_rotation', methods=['POST'])
def clear_manual_rotation():
    if load_json_file(GAME_STATUS_FILE).get("running", False):
        return jsonify({"status": "error", "message": "ゲーム中は手動テスト回転を停止できません"}), 409

    save_manual_rotation({"enabled": False, "rpm": 10, "mode": "c", "direction": "c"})
    return jsonify({"status": "ok", "manual_rotation": {"enabled": False, "rpm": 10, "mode": "c", "direction": "c"}})


@app.route("/set_control_mode", methods=["POST"])
def set_control_mode():
    if load_json_file(GAME_STATUS_FILE).get("running", False):
        return jsonify({
            "status": "error",
            "message": "ゲーム中はモーター制御モードを変更できません"
        }), 409

    data = request.get_json(silent=True) or {}
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
        "attack_challenge_wait",
        "manual_test",
    }

    if mode not in allowed_modes:
        return jsonify({
            "status": "error",
            "message": "無効なモードです"
        }), 400

    assigned_ids = load_json_file(ASSIGNED_FILE)
    watch_ids = set(assigned_ids.values())

    # 他人の心拍を利用するモードは2台以上必要
    other_watch_modes = {"next_fast", "prev_fast", "random_fast", "highest_diff", "lowest_diff", "random_diff"}
    if mode in other_watch_modes and len(watch_ids) < 2:
        return jsonify({
            "status": "error",
            "message": "このモードは2台以上接続されていないと使用できません"
        }), 400

    with open(CONTROL_FILE, "w") as f:
        json.dump({"mode": mode}, f)

    if mode in {"attack_challenge", "attack_challenge_wait"}:
        reset_attack_cycle_state()

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

    allowed_targets = get_allowed_attack_targets(attacker, assigned_watches)
    if target not in allowed_targets:
        return jsonify({"status": "error", "message": f"{attacker}はこのターンサイクルでは {', '.join(allowed_targets)} へしか妨害できません"}), 400

    round_state = update_attack_round_for_turn(current_turn, assigned_watches)
    used_attackers = set(round_state.get("used_attackers", []))
    success_state = load_attack_success()
    if not should_allow_attack(attacker, current_turn, assigned_watches):
        return jsonify({"status": "error", "message": "このターンサイクルでは既に妨害済みです。次の一周まで再妨害できません"}), 409
    if success_state.get(attacker, {}).get("turn") == current_turn:
        return jsonify({"status": "error", "message": "このターンでは既に成功済みのため再妨害できません"}), 409

    mode = load_json_file(CONTROL_FILE).get("mode")
    targets = load_attack_targets()
    used_attackers.add(attacker)
    round_state["used_attackers"] = sorted(used_attackers)

    if mode in {"attack_challenge", "attack_challenge_wait"}:
        condition = get_attack_challenge_condition()
        attackers_this_turn = set(condition.get("attackers_this_turn", []))
        attackers_this_turn.add(attacker)
        condition["attackers_this_turn"] = sorted(attackers_this_turn)
        save_json_file(ATTACK_CONDITION_FILE, condition, log=False)
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
    if load_json_file(CONTROL_FILE).get("mode") in {"attack_challenge", "attack_challenge_wait"}:
        resolve_attack_challenge()
    attackers = sorted(attacker for attacker, target in load_attack_targets().items() if target == current_turn)
    return jsonify({"current_turn": current_turn, "attackers": attackers, "attack_count": len(attackers)})


@app.route('/attack_status')
def get_attack_status():
    game_status = load_json_file(GAME_STATUS_FILE)
    current_turn = load_json_file(TURN_FILE).get("current_turn")
    mode = load_json_file(CONTROL_FILE).get("mode")
    update_attack_round_for_turn(current_turn, set(load_json_file(ASSIGNED_FILE).values()))
    if mode not in {"attack_challenge", "attack_challenge_wait"}:
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
            "reference_bpm": attack_reference(watch_id, condition)[0],
            "reference_source": attack_reference(watch_id, condition)[1],
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
        "attack_mode": mode in {"attack_challenge", "attack_challenge_wait"},
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
