import RPi.GPIO as GPIO
from time import sleep
import time
import requests
import threading
import random

# --------------------
# 設定
# --------------------
motorPins = (18, 23, 24, 25)
stepsPerRevolution = 2048

# 実モータの速度調整値。小さいほどGPIO信号の待機時間が短くなり、速く回る。
# モータが脱調する場合は MIN_STEP_DELAY を大きくする。
MIN_STEP_DELAY = 0.003
STEP_DELAY_MULTIPLIER = 4

# 通常時の心拍差 -> RPM の対応表。
# 各要素は (境界値, RPM, 境界を含めるか)。値はここだけ変更すればよい。
FAST_RPM_TIERS = (
    (3, 10, True),
    (8, 20, False),
    (15, 30, False),
    (float("inf"), 40, False),
)

# self_slow モード用。心拍差が小さいほど高速になる逆転テーブル。
SLOW_RPM_TIERS = (
    (3, 40, False),
    (8, 30, False),
    (15, 20, False),
    (float("inf"), 10, False),
)

API_HOST = 'http://192.168.100.26:8080'
HEART_API_URL = f'{API_HOST}/heart_all'  # ★全watchの心拍を取得するAPI
STATUS_API_URL = f'{API_HOST}/status'
TURN_API_URL = f'{API_HOST}/turn'
BASELINE_API_URL = f'{API_HOST}/get_baselines'   # ★追加
ATTACK_STATUS_API_URL = f'{API_HOST}/attack_status'

# 妨害成功人数ごとの演出。通常RPMより優先して適用する。
# rpm_steps は1秒ごとに順番に切り替わる。direction_interval が0なら毎回ランダム。
ATTACK_PROFILES = {
    1: {"rpm_steps": (5, 10, 15, 20, 25, 30), "direction": "normal"},
    2: {"rpm": 30, "direction": "random", "direction_interval": 5},
    3: {"rpm": 40, "direction": "random", "direction_interval": 0},
}

rotation_settings = {}
rotation_settings_lock = threading.Lock()
# モード
# self = 自分
# next = 次の人
control_mode = "self"

# baseline キャッシュ（watchごとの平均値）
baseline_cache = {}
baseline_lock = threading.Lock()

# randomモード用：各ターンの参照先を固定する
random_target_map = {}
random_target_lock = threading.Lock()

# 上昇・下降をターンごとにランダム選択するための状態
random_difference_mode_map = {}
random_difference_mode_lock = threading.Lock()

# 2ターン目以降の上昇・下降比較に使うターン開始時の心拍
turn_start_heartbeats = {}
turn_start_heartbeats_lock = threading.Lock()

attack_direction_cache = {}
attack_direction_lock = threading.Lock()

# --------------------
# GPIOセットアップ
# --------------------
def setup_motor():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    for pin in motorPins:
        GPIO.setup(pin, GPIO.OUT)

# --------------------
# モーター回転
# --------------------
def rotary(direction, stepSpeed):
    for _ in range(8):
        for j in range(4):
            for i in range(4):
                if direction == 'c':
                    GPIO.output(motorPins[i], (0x99 >> j) & (0x08 >> i))
                else:
                    GPIO.output(motorPins[i], (0x99 << j) & (0x80 >> i))
            sleep(stepSpeed)

# --------------------
# 心拍差 -> RPM & 方向
# --------------------
def rpm_from_tiers(abs_diff, tiers):
    """絶対心拍差に対応するRPMを、上から順に境界テーブルで決める。"""
    for upper_bound, rpm, inclusive in tiers:
        if abs_diff <= upper_bound if inclusive else abs_diff < upper_bound:
            return rpm
    return tiers[-1][1]


def calculate_rpm_fast(diff):
    """通常モード用: 心拍差が大きいほど速く回す。"""
    return rpm_from_tiers(abs(diff), FAST_RPM_TIERS)

def calculate_rpm_slow(diff):
    """self_slow モード用: 心拍差が小さいほど速く回す。"""
    return rpm_from_tiers(abs(diff), SLOW_RPM_TIERS)

def calculate_direction(diff):
    """
    baselineより上なら 'c'
    baselineより下なら 'a'
    """
    if diff >= 0:
        return 'c'
    else:
        return 'a'

# --------------------
# API通信
# --------------------
def get_game_status():
    try:
        res = requests.get(STATUS_API_URL, timeout=2)
        res.raise_for_status()
        return res.json().get("running", False)
    except Exception as e:
        print("[ERROR] /status取得失敗:", e)
        return False

def get_current_turn():
    try:
        res = requests.get(TURN_API_URL, timeout=2)
        res.raise_for_status()
        return res.json().get("current_turn")
    except Exception as e:
        print("[ERROR] /turn取得失敗:", e)
        return None

def get_heart_data():
    try:
        res = requests.get(HEART_API_URL, timeout=2)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print("[ERROR] /heart取得失敗:", e)
        return {}

def fetch_baselines():
    """
    baseline.json をサーバから取得してキャッシュ更新
    """
    try:
        res = requests.get(BASELINE_API_URL, timeout=2)
        res.raise_for_status()
        data = res.json()  # {"watch1": 68.2, ...}

        # 数値化して保存
        parsed = {}
        for k, v in data.items():
            try:
                parsed[k] = float(v)
            except:
                pass

        with baseline_lock:
            baseline_cache.clear()
            baseline_cache.update(parsed)

        # print("[BASELINE] updated:", baseline_cache)
    except Exception as e:
        # 失敗しても前のキャッシュで動かす
        print("[WARN] baseline取得失敗（キャッシュ継続）:", e)

def get_control_mode():
    try:
        res = requests.get(f"{API_HOST}/get_control_mode", timeout=2)
        res.raise_for_status()
        return res.json().get("mode","self")
    except:
        return "self"

def get_attackers(current_turn):
    try:
        response = requests.get(ATTACK_STATUS_API_URL, timeout=2)
        response.raise_for_status()
        data = response.json()
        if data.get("current_turn") != current_turn:
            return []
        return [attacker for attacker in data.get("attackers", []) if isinstance(attacker, str)]
    except requests.RequestException:
        return []

def apply_attack_effect(current_turn, rpm, direction):
    attackers = get_attackers(current_turn)
    if not attackers:
        return rpm, direction, attackers

    attack_count = min(len(attackers), 3)
    profile = ATTACK_PROFILES[attack_count]

    if "rpm_steps" in profile:
        step_index = int(time.time()) % len(profile["rpm_steps"])
        rpm = profile["rpm_steps"][step_index]
    else:
        rpm = profile["rpm"]

    if profile["direction"] == "random":
        interval = profile["direction_interval"]
        period = int(time.time() // interval) if interval else time.time_ns()
        cache_key = (current_turn, attack_count, period)
        with attack_direction_lock:
            direction = attack_direction_cache.get(cache_key)
            if direction is None:
                direction = random.choice(["c", "a"])
                attack_direction_cache.clear()
                attack_direction_cache[cache_key] = direction

    return rpm, direction, attackers

def publish_rotation_status(
    motor_watch,
    target_watch,
    mode,
    rpm,
    direction,
    extreme=None,
    attackers=None,
    reference_bpm=None,
    reference_source=None,
    baseline_bpm=None,
    reference_heartbeats=None,
):
    """画面表示用に、実際に回転判断で使った比較基準もサーバーへ送る。"""
    try:
        requests.post(
            f"{API_HOST}/set_rotation_status",
            json={
                "motor_watch": motor_watch,
                "target_watch": target_watch,
                "mode": mode,
                "rpm": rpm,
                "direction": direction,
                "extreme": extreme,
                "attackers": attackers or [],
                "reference_bpm": reference_bpm,
                "reference_source": reference_source,
                "baseline_bpm": baseline_bpm,
                "reference_heartbeats": reference_heartbeats or {},
            },
            timeout=2,
        )
    except requests.RequestException:
        pass

# --------------------
# 次のwatch取得
# --------------------
def get_watch_ids():
    try:
        res = requests.get(f"{API_HOST}/clients", timeout=2)
        res.raise_for_status()
        data = res.json()

        ids = list(data.get("ids", {}).values())
        ids = sorted(set(ids))  # ["watch1","watch2"...]
        return ids
    except Exception as e:
        print("[ERROR] /clients取得失敗:", e)
        return []

def get_next_watch(current_turn):
    ids = get_watch_ids()

    if not current_turn or current_turn not in ids:
        return None

    i = ids.index(current_turn)
    return ids[(i + 1) % len(ids)]

def get_prev_watch(current_turn):
    ids = get_watch_ids()

    if not current_turn or current_turn not in ids:
        return None

    i = ids.index(current_turn)
    return ids[(i - 1) % len(ids)]

def get_random_watch(current_turn):
    ids = get_watch_ids()

    if not current_turn or current_turn not in ids:
        return None

    others = [w for w in ids if w != current_turn]
    if not others:
        return None

    with random_target_lock:
        # すでにそのターン用の相手が決まっていれば再利用
        if current_turn in random_target_map:
            saved = random_target_map[current_turn]
            if saved in others:
                return saved

        # 無ければ新しく決める
        target = random.choice(others)
        random_target_map[current_turn] = target
        print(f"[RANDOM TARGET] {current_turn} -> {target}")
        return target

def get_difference_watch(current_turn, heart_data, largest, reference_heartbeats=None):
    candidates = []

    if reference_heartbeats is None:
        with baseline_lock:
            reference_heartbeats = dict(baseline_cache)

    for watch_id in get_watch_ids():
        # 上昇・下降モードでは、手番本人の心拍は利用しない。
        if watch_id == current_turn:
            continue
        try:
            bpm = float(heart_data.get(watch_id, {}).get("heartbeat"))
            reference_bpm = float(reference_heartbeats[watch_id])
        except (KeyError, TypeError, ValueError):
            continue

        candidates.append((bpm - reference_bpm, watch_id))

    if not candidates:
        return None

    return (max if largest else min)(candidates)[1]

def get_random_difference_watch(current_turn, heart_data, reference_heartbeats=None):
    with random_difference_mode_lock:
        largest = random_difference_mode_map.get(current_turn)
        if largest is None:
            largest = random.choice([True, False])
            random_difference_mode_map[current_turn] = largest
            print(f"[RANDOM EXTREME] {current_turn} -> {'上昇' if largest else '下降'}")

    return get_difference_watch(current_turn, heart_data, largest, reference_heartbeats), largest

# --------------------
# データ取得スレッド
# --------------------
def data_fetch_loop():
    last_turn = None
    last_info = 0
    first_turn = True
    use_baseline_reference = True

    while True:
        try:
            running = get_game_status()
            if not running:
                with rotation_settings_lock:
                    rotation_settings.clear()

                last_turn = None
                first_turn = True
                use_baseline_reference = True
                with turn_start_heartbeats_lock:
                    turn_start_heartbeats.clear()

                # ★追加：2秒に1回だけ表示（うるさくしない）
                if time.time() - last_info > 2:
                    print("[WAIT] game running=false なので待機中…（UIでゲーム開始してね）")
                    last_info = time.time()

                time.sleep(1)
                continue

            # baseline更新（毎秒でOK、重いなら2～3秒にしても良い）
            fetch_baselines()

            current_turn = get_current_turn()
            heart_data = get_heart_data()
            # ターン変更時にだけ比較基準を固定する。
            # 初ターンは各watchの平均値、2ターン目以降は交代時点の全watch心拍を使う。
            if current_turn != last_turn:
                print(f"[TURN] {last_turn} -> {current_turn}")

                with random_target_lock:
                    if last_turn in random_target_map:
                        del random_target_map[last_turn]

                with random_difference_mode_lock:
                    if last_turn in random_difference_mode_map:
                        del random_difference_mode_map[last_turn]

                use_baseline_reference = first_turn
                if first_turn:
                    first_turn = False
                else:
                    snapshot = {}
                    for watch_id, record in heart_data.items():
                        try:
                            snapshot[watch_id] = float(record.get("heartbeat"))
                        except (AttributeError, TypeError, ValueError):
                            continue
                    with turn_start_heartbeats_lock:
                        turn_start_heartbeats.clear()
                        turn_start_heartbeats.update(snapshot)
                    print(f"[TURN REFERENCE] {current_turn}: {snapshot}")

                last_turn = current_turn

            if not current_turn or current_turn not in heart_data:
                with rotation_settings_lock:
                    rotation_settings.clear()
                time.sleep(1)
                continue

            mode = get_control_mode()
            extreme = None
            with baseline_lock:
                baseline_references = dict(baseline_cache)
            with turn_start_heartbeats_lock:
                turn_references = dict(turn_start_heartbeats)

            difference_mode = mode in {"highest_diff", "lowest_diff", "random_diff"}
            comparison_references = baseline_references if use_baseline_reference else turn_references
            comparison_source = "baseline" if use_baseline_reference else "turn_start"

            # 参照する心拍のwatchを決める
            if mode == "self_fast" or mode == "self_slow":
                target_watch = current_turn
            elif mode == "attack_challenge":
                target_watch = current_turn
            elif mode == "next_fast":
                target_watch = get_next_watch(current_turn)
            elif mode == "prev_fast":
                target_watch = get_prev_watch(current_turn)
            elif mode == "random_fast":
                target_watch = get_random_watch(current_turn)
            elif mode == "highest_diff":
                target_watch = get_difference_watch(current_turn, heart_data, largest=True, reference_heartbeats=comparison_references)
                extreme = "up"
            elif mode == "lowest_diff":
                target_watch = get_difference_watch(current_turn, heart_data, largest=False, reference_heartbeats=comparison_references)
                extreme = "down"
            elif mode == "random_diff":
                target_watch, largest = get_random_difference_watch(current_turn, heart_data, comparison_references)
                extreme = "up" if largest else "down"
            else:
                target_watch = current_turn

            if not target_watch or target_watch not in heart_data:
                with rotation_settings_lock:
                    rotation_settings.clear()
                time.sleep(1)
                continue

            record = heart_data.get(target_watch, {})
            try:
                bpm = float(record.get("heartbeat", 0))
            except (ValueError, TypeError):
                bpm = 0

            baseline = baseline_references.get(target_watch)
            reference_bpm = comparison_references.get(target_watch) if difference_mode else baseline
            if baseline is None or reference_bpm is None:
                print(f"[WARN] baseline無し: target={target_watch} （モーター停止）")
                with rotation_settings_lock:
                    rotation_settings.clear()
                time.sleep(1)
                continue

            # ターン中に比較基準は更新しない。上昇・下降モードは採用方向だけで符号を決める。
            raw_diff = bpm - reference_bpm
            evaluation_diff = raw_diff if extreme != "down" else -raw_diff
            if mode == "self_slow":
                rpm = calculate_rpm_slow(evaluation_diff)
            else:
                rpm = calculate_rpm_fast(evaluation_diff)

            direction = calculate_direction(evaluation_diff)
            rpm, direction, attackers = apply_attack_effect(current_turn, rpm, direction)

            # ★回転させる対象は「今ターンの人」（プレイ中の人）
            with rotation_settings_lock:
                rotation_settings.clear()
                if rpm > 0:
                    rotation_settings[current_turn] = (rpm, direction)

            # 差分モードでは交代時の固定心拍、その他では平均値を画面に渡す。
            displayed_references = comparison_references if difference_mode else baseline_references
            publish_rotation_status(
                current_turn,
                target_watch,
                mode,
                rpm,
                direction,
                extreme,
                attackers,
                reference_bpm,
                comparison_source if difference_mode else "baseline",
                baseline,
                displayed_references,
            )
            print(f"[心拍] mode={mode} motor={current_turn} uses={target_watch}: bpm={bpm:.1f}, base={baseline:.1f}, ref={reference_bpm:.1f}, diff={evaluation_diff:+.1f} -> rpm={rpm}, dir={direction}, attackers={attackers}")

            time.sleep(1)

        except Exception as e:
            print("[ERROR] Data fetch error:", e)
            time.sleep(1)

# --------------------
# 回転ループ
# --------------------
def rotation_loop():
    while True:
        try:
            with rotation_settings_lock:
                items = list(rotation_settings.items())

            if not items:
                # これがずっと出るなら「rotation_settingsが空」
                # → data_fetch_loop側が毎回clearしてる/ターン不一致/heart_data欠落 など
                # print("[ROT] no items")
                time.sleep(0.05)
                continue

            print("[ROT] items:", items)  # ★これ追加

            for device_id, (rpm, direction) in items:
                # RPMを1ステップごとの待機時間へ変換する。
                # STEP_DELAY_MULTIPLIER と MIN_STEP_DELAY は実機の回転感・安定性を調整する値。
                step_delay = (60 / rpm) / stepsPerRevolution
                safe_step_delay = max(step_delay * STEP_DELAY_MULTIPLIER, MIN_STEP_DELAY)
                print(f"[ROT] run {device_id} rpm={rpm} dir={direction} step={safe_step_delay:.5f}")  # ★これ追加
                rotary(direction, safe_step_delay)

        except KeyboardInterrupt:
            GPIO.cleanup()
            break
        except Exception as e:
            print("[ERROR] Rotation error:", e)
            time.sleep(0.1)
# --------------------
# MAIN
# --------------------
if __name__ == '__main__':
    print("[START] Motor controller starting up...")
    setup_motor()
    threading.Thread(target=data_fetch_loop, daemon=True).start()
    rotation_loop()