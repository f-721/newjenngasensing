import RPi.GPIO as GPIO
from time import sleep
import time
import requests
import threading
import random

# モーター設定
motorPins = (18, 23, 24, 25)
stepsPerRevolution = 2048
MIN_STEPSPEED = 0.003

API_HOST = 'http://localhost:8080'
HEART_API_URL = f'{API_HOST}/heart'
STATUS_API_URL = f'{API_HOST}/status'

# デバイス情報
device_direction = {}
device_bpm = {}
game_running = False

# GPIOセットアップ
def setup_motor():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    for pin in motorPins:
        GPIO.setup(pin, GPIO.OUT)

def rotary(direction, stepSpeed):
    for _ in range(8):
        for j in range(4):
            for i in range(4):
                if direction == 'c':
                    GPIO.output(motorPins[i], (0x99 >> j) & (0x08 >> i))
                else:
                    GPIO.output(motorPins[i], (0x99 << j) & (0x80 >> i))
            sleep(stepSpeed)

def calculate_rpm(bpm):
    if bpm <= 70:
        return 5
    elif bpm <= 80:
        return 15
    else:
        return 30

# ===== スレッド1: APIポーリング =====
def api_polling_loop():
    global game_running
    while True:
        try:
            res = requests.get(STATUS_API_URL, timeout=2)
            res.raise_for_status()
            running = res.json().get("running", False)
            game_running = running
            print(f"[STATUS] Game running = {running}")

            if not running:
                time.sleep(1)
                continue

            # 心拍数取得
            res = requests.get(HEART_API_URL, timeout=2)
            res.raise_for_status()
            heart_data = res.json()
            print(f"[HEART] Received data: {heart_data}")

            if heart_data:
                for device_id, record in heart_data.items():
                    try:
                        bpm = int(record.get("heartbeat", 0))
                        device_bpm[device_id] = bpm
                    except (ValueError, TypeError):
                        print(f"[WARN] Invalid BPM for {device_id}: {record}")
            else:
                print("[WARN] No heart data")
        except Exception as e:
            print("[ERROR] API polling error:", e)

        time.sleep(1)

# ===== スレッド2: モーター制御 =====
def motor_loop():
    print("[Motor Loop STARTED]")
    while True:
        if not game_running or not device_bpm:
            sleep(0.05)
            continue

        snapshot = device_bpm.copy()
        for device_id, bpm in snapshot.items():
            rpm = calculate_rpm(bpm)
            stepSpeed = (60 / rpm) / stepsPerRevolution
            safe_stepSpeed = max(stepSpeed * 4, MIN_STEPSPEED)

            # 方向決定
            if device_id not in device_direction:
                device_direction[device_id] = 'c'
            else:
                if random.random() < 0.2:
                    prev = device_direction[device_id]
                    device_direction[device_id] = 'a' if prev == 'c' else 'c'
                    print(f"[DIR] {device_id}: {prev} → {device_direction[device_id]}")

            direction = device_direction[device_id]

            print(f"[CONTROL] {device_id} BPM={bpm} RPM={rpm} DIR={direction}")
            rotary(direction, safe_stepSpeed)

# ===== MAIN =====
if __name__ == '__main__':
    setup_motor()
    try:
        # 2スレッド起動
        threading.Thread(target=api_polling_loop, daemon=True).start()
        motor_loop()
    except KeyboardInterrupt:
        print("[STOP] Interrupted. Cleaning up GPIO...")
        GPIO.cleanup()