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
HEART_API_URL = f'{API_HOST}/heart'
STATUS_API_URL = f'{API_HOST}/status'
TURN_API_URL = f'{API_HOST}/turn'

device_direction = {}
rotation_settings = {}
rotation_settings_lock = threading.Lock()

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
# ここで速度の調整を行います
# def calculate_rpm(bpm):
#     if bpm <= 70:
#         return 0  # 動かない
#     elif bpm <= 75:
#         return 10
#     elif bpm <= 85:
#         return 15
#     else:
#         return 20

def calculate_rpm(bpm, baseline_bpm):
    diff = bpm - baseline_bpm
    if diff < 5:
        return 0  # 小さな変化は無視
    elif diff < 10:
        return 10
    elif diff < 20:
        return 20
    else:
        return 30

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

# --------------------
# データ取得スレッド
# --------------------
def data_fetch_loop():
    while True:
        try:
            running = get_game_status()
            if not running:
                with rotation_settings_lock:
                    rotation_settings.clear()
                print("[INFO] Game stopped. Motors paused.")
                time.sleep(1)
                continue

            current_turn = get_current_turn()
            heart_data = get_heart_data()

            if not current_turn or current_turn not in heart_data:
                with rotation_settings_lock:
                    rotation_settings.clear()
                time.sleep(1)
                continue

            # ターン中の人のデータだけ更新
            record = heart_data.get(current_turn)
            try:
                bpm = int(record.get("heartbeat", 0))
            except (ValueError, TypeError):
                bpm = 0

            if bpm > 0:
                rpm = calculate_rpm(bpm)
                with rotation_settings_lock:
                    rotation_settings.clear()
                    rotation_settings[current_turn] = rpm
                print(f"[心拍受信] {current_turn}: {bpm} bpm -> RPM: {rpm}")
            else:
                with rotation_settings_lock:
                    rotation_settings.clear()

            time.sleep(1)

        except Exception as e:
            print("[ERROR] Data fetch error:", e)
            time.sleep(1)

def rotation_loop():
    last_update_time = 0  # 前回更新時刻

    while True:
        try:
            current_time = time.time()

            # 1秒ごとに回転設定を更新（directionやrpm）
            if current_time - last_update_time >= 1:
                with rotation_settings_lock:
                    items = list(rotation_settings.items())
                last_update_time = current_time

                # 回転設定がない場合
                if not items:
                    time.sleep(0.05)
                    continue

                for device_id, rpm in items:
                    if device_id not in device_direction:
                        device_direction[device_id] = 'c'
                        print(f"[回転] {device_id} 初期方向 -> c")
                    else:
                        if random.random() < 0.5:
                            prev = device_direction[device_id]
                            device_direction[device_id] = 'a' if prev == 'c' else 'c'
                            print(f"[方向変更] {device_id} により回転方向が {prev} → {device_direction[device_id]} に変更")

                    direction = device_direction[device_id]
                    stepSpeed = (60 / rpm) / stepsPerRevolution
                    safe_stepSpeed = max(stepSpeed * 4, MIN_STEPSPEED)

                    print(f"[回転設定更新] {device_id} -> 方向: {direction}, RPM: {rpm}, StepSpeed: {safe_stepSpeed:.5f}")

            # 直前の回転設定に従って回す（常に回す）
            with rotation_settings_lock:
                items = list(rotation_settings.items())

            for device_id, rpm in items:
                direction = device_direction.get(device_id, 'c')
                stepSpeed = (60 / rpm) / stepsPerRevolution
                safe_stepSpeed = max(stepSpeed * 4, MIN_STEPSPEED)

                rotary(direction, safe_stepSpeed)

        except KeyboardInterrupt:
            print("[STOP] KeyboardInterrupt detected. Cleaning up GPIO...")
            GPIO.cleanup()
            break
        except Exception as e:
            print("[ERROR] Rotation error:", e)
            time.sleep(0.1)
# --------------------
# 回転スレッド（1回だけ定義）
# --------------------
def rotation_loop():
    last_update_time = 0  # 前回更新時刻

    while True:
        try:
            current_time = time.time()

            # 1秒ごとに回転設定を更新（directionやrpm）
            if current_time - last_update_time >= 1:
                with rotation_settings_lock:
                    items = list(rotation_settings.items())
                last_update_time = current_time

                # 回転設定がない場合
                if not items:
                    time.sleep(0.05)
                    continue

                for device_id, rpm in items:
                    if device_id not in device_direction:
                        device_direction[device_id] = 'c'
                        print(f"[回転] {device_id} 初期方向 -> c")
                    else:
                        if random.random() < 0.5:
                            prev = device_direction[device_id]
                            device_direction[device_id] = 'a' if prev == 'c' else 'c'
                            print(f"[方向変更] {device_id} の方向が {prev} → {device_direction[device_id]} に変更")

                    direction = device_direction[device_id]
                    stepSpeed = (60 / rpm) / stepsPerRevolution
                    safe_stepSpeed = max(stepSpeed * 4, MIN_STEPSPEED)

                    print(f"[回転設定更新] {device_id} -> 方向: {direction}, RPM: {rpm}, StepSpeed: {safe_stepSpeed:.5f}")

            # 直前の回転設定に従って回す（常に回す）
            with rotation_settings_lock:
                items = list(rotation_settings.items())

            for device_id, rpm in items:
                direction = device_direction.get(device_id, 'c')
                stepSpeed = (60 / rpm) / stepsPerRevolution
                safe_stepSpeed = max(stepSpeed * 4, MIN_STEPSPEED)

                rotary(direction, safe_stepSpeed)

        except KeyboardInterrupt:
            print("[STOP] KeyboardInterrupt detected. Cleaning up GPIO...")
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