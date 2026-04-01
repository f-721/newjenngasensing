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
MIN_STEPSPEED = 0.003

API_HOST = 'http://192.168.100.26:8080'
HEART_API_URL = f'{API_HOST}/heart_all'  # ★全watchの心拍を取得するAPI
STATUS_API_URL = f'{API_HOST}/status'
TURN_API_URL = f'{API_HOST}/turn'
BASELINE_API_URL = f'{API_HOST}/get_baselines'   # ★追加

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
# diff → RPM & 方向
# --------------------
def calculate_rpm_fast(diff):
    ad = abs(diff)

    if ad < 3:
        return 0
    elif ad < 8:
        return 10
    elif ad < 15:
        return 20
    else:
        return 30

def calculate_rpm_slow(diff):
    ad = abs(diff)

    if ad < 3:
        return 30
    elif ad < 8:
        return 20
    elif ad < 15:
        return 10
    else:
        return 5

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

# --------------------
# データ取得スレッド
# --------------------
def data_fetch_loop():
    last_turn = None
    last_info = 0

    while True:
        try:
            running = get_game_status()
            if not running:
                with rotation_settings_lock:
                    rotation_settings.clear()

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
            # ターン変化ログ
            if current_turn != last_turn:
                print(f"[TURN] {last_turn} -> {current_turn}")

                with random_target_lock:
                    if last_turn in random_target_map:
                        del random_target_map[last_turn]

                last_turn = current_turn

            if not current_turn or current_turn not in heart_data:
                with rotation_settings_lock:
                    rotation_settings.clear()
                time.sleep(1)
                continue

            mode = get_control_mode()

            # 参照する心拍のwatchを決める
            if mode == "self_fast" or mode == "self_slow":
                target_watch = current_turn
            elif mode == "next_fast":
                target_watch = get_next_watch(current_turn)
            elif mode == "prev_fast":
                target_watch = get_prev_watch(current_turn)
            elif mode == "random_fast":
                target_watch = get_random_watch(current_turn)
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

            # baselineは「参照する心拍のwatch」に合わせる（★重要）
            with baseline_lock:
                baseline = baseline_cache.get(target_watch)

            if baseline is None:
                print(f"[WARN] baseline無し: target={target_watch} （モーター停止）")
                with rotation_settings_lock:
                    rotation_settings.clear()
                time.sleep(1)
                continue

            diff = bpm - baseline

            if mode == "self_slow":
                rpm = calculate_rpm_slow(diff)
            else:
                rpm = calculate_rpm_fast(diff)

            direction = calculate_direction(diff)

            # ★回転させる対象は「今ターンの人」（プレイ中の人）
            with rotation_settings_lock:
                rotation_settings.clear()
                if rpm > 0:
                    rotation_settings[current_turn] = (rpm, direction)

            print(f"[心拍] mode={mode} motor={current_turn} uses={target_watch}: bpm={bpm:.1f}, base={baseline:.1f}, diff={diff:+.1f} -> rpm={rpm}, dir={direction}")

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
                stepSpeed = (60 / rpm) / stepsPerRevolution
                safe_stepSpeed = max(stepSpeed * 4, MIN_STEPSPEED)
                print(f"[ROT] run {device_id} rpm={rpm} dir={direction} step={safe_stepSpeed:.5f}")  # ★これ追加
                rotary(direction, safe_stepSpeed)

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