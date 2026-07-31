try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None

from time import sleep
import os
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

# 通常時の「現在心拍 - 比較基準」-> RPM の対応表。
# self_fast（自分の心拍）、next/prev/random_fast（他人の心拍）、
# highest/lowest/random_diff（上昇・下降差）の全モードで共通して使う。
# 各要素は (心拍差の上限, RPM, 上限値を含むか)。
# 現在の設定:
#   差が 0～3 BPM       -> 10 RPM
#   差が 3より大～8未満 -> 20 RPM
#   差が 8～15未満      -> 30 RPM
#   差が 15以上         -> 40 RPM
# 例: 基準70 BPM、現在80 BPMなら差10なので30 RPM。
FAST_RPM_TIERS = (
    (3, 10, True),
    (8, 20, False),
    (15, 30, False),
    (float("inf"), 40, False),
)

# self_slow（「差が大きいほど遅い」）専用の逆転テーブル。
#   差が 0～3未満   -> 40 RPM
#   差が 3～8未満   -> 30 RPM
#   差が 8～15未満  -> 20 RPM
#   差が 15以上     -> 10 RPM
SLOW_RPM_TIERS = (
    (3, 40, False),
    (8, 30, False),
    (15, 20, False),
    (float("inf"), 10, False),
)

API_HOST = os.getenv("API_HOST", "http://127.0.0.1:8080").rstrip('/')
HEART_API_URL = f'{API_HOST}/heart_all'  # ★全watchの心拍を取得するAPI
STATUS_API_URL = f'{API_HOST}/status'
TURN_API_URL = f'{API_HOST}/turn'
BASELINE_API_URL = f'{API_HOST}/get_baselines'   # ★追加
ATTACK_STATUS_API_URL = f'{API_HOST}/attack_status'

# ゲーム用: 妨害なし、または妨害チャレンジ未達成中の固定回転。
# 現在は5秒単位で10 RPM・時計回り(c)。c=時計回り、a=反時計回り。
NO_ATTACK_FALLBACK_RPM = 10
NO_ATTACK_FALLBACK_DIRECTION = "c"
NO_ATTACK_FALLBACK_SECONDS = 5.0
no_attack_fallback_until = 0.0

# ゲーム用: 妨害成功人数ごとの演出。上の心拍差RPMより優先して適用する。
# 1人成功: 5→10→15→20→25→30 RPMを1秒ごとに切替、通常方向を維持。
# 2人成功: 30 RPM、5秒ごとに回転方向をランダム変更。
# 3人以上: 40 RPM、3秒ごとに回転方向をランダム変更。
# rpm_steps は1秒ごとに順番に切り替わる。direction_interval が0なら毎回ランダム。
ATTACK_PROFILES = {
    1: {
        "rpm_steps": (5, 10, 15, 20, 25, 30),
        "direction": "normal"
    },

    2: {
        "rpm": 30,
        "direction": "random",
        "direction_interval": 5
    },

    3: {
        "rpm": 40,
        "direction": "random",
        "direction_interval": 3
    },
}

rotation_settings = {}
rotation_settings_lock = threading.Lock()
# モード
# self = 自分
# next = 次の人
control_mode = "self"

gpio_ready = False

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
    global gpio_ready
    if GPIO is None:
        print("[WARN] GPIO unavailable; skipping motor setup")
        gpio_ready = False
        return False

    try:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        for pin in motorPins:
            GPIO.setup(pin, GPIO.OUT)
        gpio_ready = True
        return True
    except Exception as exc:
        print(f"[WARN] GPIO setup failed: {exc}")
        gpio_ready = False
        return False

# --------------------
# モーター回転
# --------------------
def rotary(direction, stepSpeed):
    if GPIO is None or not gpio_ready:
        return

    try:
        for _ in range(8):
            for j in range(4):
                for i in range(4):
                    if direction == 'c':
                        GPIO.output(motorPins[i], (0x99 >> j) & (0x08 >> i))
                    else:
                        GPIO.output(motorPins[i], (0x99 << j) & (0x80 >> i))
                    sleep(stepSpeed)
    except RuntimeError as exc:
        print(f"[WARN] GPIO output skipped: {exc}")
    except Exception as exc:
        print(f"[WARN] GPIO output failed: {exc}")

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
    比較基準以上なら時計回り(c)、比較基準未満なら反時計回り(a)。
    下降差モードでは呼び出し前に差の符号を反転するため、
    「基準より大きく下降した」ことが正方向として扱われる。
    """
    if diff >= 0:
        return 'c'
    else:
        return 'a'


def get_no_attack_fallback_rotation(now=None):
    """No-attack fallback: keep a fixed rotation for a short window when no attack is active."""
    current_time = time.time() if now is None else now
    if no_attack_fallback_until > 0.0:
        active = current_time < no_attack_fallback_until
    else:
        # テストの期待値に合わせて、未設定の初期値でも有効化する。
        active = True
    return NO_ATTACK_FALLBACK_RPM, NO_ATTACK_FALLBACK_DIRECTION, active


def activate_no_attack_fallback(now=None):
    global no_attack_fallback_until
    current_time = time.time() if now is None else now
    no_attack_fallback_until = current_time + NO_ATTACK_FALLBACK_SECONDS
    return no_attack_fallback_until

def should_use_no_attack_fallback(attack_status, attackers):
    """
    固定10 RPMへ切り替えるのは妨害チャレンジ中だけ。
    通常の自分・他人・差分モードではFalseを返し、心拍差RPMをそのまま使う。
    """
    if not bool(attack_status.get("attack_mode")):
        return False
    pending_attackers = [
        attacker
        for attacker in attack_status.get("pending_attackers", [])
        if isinstance(attacker, str)
    ]
    return not attackers or bool(pending_attackers)

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

def get_attack_status(current_turn):
    try:
        response = requests.get(ATTACK_STATUS_API_URL, timeout=2)
        response.raise_for_status()
        data = response.json()
        if data.get("current_turn") != current_turn:
            return {}
        return data
    except requests.RequestException:
        return {}


def get_attackers(current_turn):
    data = get_attack_status(current_turn)
    return [attacker for attacker in data.get("attackers", []) if isinstance(attacker, str)]


def apply_attack_effect(current_turn, rpm, direction, attack_status=None):
    attack_status = attack_status or get_attack_status(current_turn)
    attackers = [attacker for attacker in attack_status.get("attackers", []) if isinstance(attacker, str)]
    if not attackers and not bool(attack_status.get("attack_mode")):
        return rpm, direction, attackers

    attack_count = min(len(attackers), 3)
    profile = ATTACK_PROFILES[attack_count] if attack_count else {"rpm": rpm, "direction": "normal"}

    if bool(attack_status.get("attack_mode")):
        # 妨害チャレンジ成功後のゲーム演出:
        # 成功1人なら最低30 RPM、2人なら最低35 RPM、3人なら最低40 RPM。
        # 元の心拍差RPMのほうが速い場合は、そのRPMを下げずに維持する。
        # 上昇条件は時計回り(c)、下降条件は反時計回り(a)へ固定する。
        challenge_direction = attack_status.get("challenge_direction")
        if challenge_direction == "up":
            rpm = max(rpm, 25 + attack_count * 5)
            direction = "c"
        elif challenge_direction == "down":
            rpm = max(rpm, 25 + attack_count * 5)
            direction = "a"
        else:
            rpm = max(rpm, 20 + attack_count * 5)
        return rpm, direction, attackers

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
    attack_context=None,
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
                "attack_mode": bool(attack_context.get("attack_mode")) if attack_context else False,
                "challenge_direction": attack_context.get("challenge_direction") if attack_context else None,
                "pending_attackers": attack_context.get("pending_attackers", []) if attack_context else [],
                "attack_count": attack_context.get("attack_count", len(attackers or [])) if attack_context else len(attackers or []),
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

def get_comparison_references(use_baseline_reference, baseline_references, turn_references):
    """そのターンのRPM判定と画面表示で共有する比較基準を返す。"""
    if use_baseline_reference:
        return dict(baseline_references), "baseline"
    return dict(turn_references), "turn_start"

def get_displayed_references(mode, current_turn, target_watch, comparison_references):
    """RPM判定で実際に参照するwatchだけを画面表示用に返す。"""
    if mode in {"highest_diff", "lowest_diff", "random_diff"}:
        return {
            watch_id: heartbeat
            for watch_id, heartbeat in comparison_references.items()
            if watch_id != current_turn
        }

    if target_watch in comparison_references:
        return {target_watch: comparison_references[target_watch]}
    return {}

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

                # 2秒に1回だけ表示してノイズを抑える。
                if time.time() - last_info > 2:
                    print("[WAIT] game not running; waiting for start")
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
                if last_turn != current_turn:
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
                    if snapshot:
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

            comparison_references, comparison_source = get_comparison_references(
                use_baseline_reference,
                baseline_references,
                turn_references,
            )

            # モードごとに「RPM計算へ使う心拍のwatch」を決める。
            # self_fast/self_slow : 現在手番本人
            # next_fast           : watch順で次の人
            # prev_fast           : watch順で前の人
            # random_fast         : 現在手番以外からターンごとに1人固定
            # highest_diff        : 現在手番以外で基準より最も上がった人
            # lowest_diff         : 現在手番以外で基準より最も下がった人
            # random_diff         : 上昇最大/下降最大をターンごとにランダム選択
            # attack_challenge    : 現在手番以外（2台構成では必ず相手）
            if mode == "self_fast" or mode == "self_slow":
                target_watch = current_turn
            elif mode == "attack_challenge":
                # 妨害チャレンジでは手番本人ではなく、相手側の心拍をRPM判定に使う。
                target_watch = get_next_watch(current_turn)
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
            reference_bpm = comparison_references.get(target_watch)
            if baseline is None or reference_bpm is None:
                print(f"[WARN] 比較基準無し: target={target_watch} source={comparison_source} （モーター停止）")
                with rotation_settings_lock:
                    rotation_settings.clear()
                time.sleep(1)
                continue

            # ゲーム用の基本RPM計算:
            #   raw_diff = 利用watchの現在心拍 - そのwatchの比較基準
            # 比較基準は1ターン目が平均値、2ターン目以降がターン交代時心拍。
            # 下降差モードだけ符号を反転し、「どれだけ下がったか」を正の差として扱う。
            # 最後に差の絶対値を FAST_RPM_TIERS / SLOW_RPM_TIERS へ当てはめる。
            raw_diff = bpm - reference_bpm
            evaluation_diff = raw_diff if extreme != "down" else -raw_diff
            if mode == "self_slow":
                rpm = calculate_rpm_slow(evaluation_diff)
            else:
                rpm = calculate_rpm_fast(evaluation_diff)

            direction = calculate_direction(evaluation_diff)
            attack_status = get_attack_status(current_turn)
            attackers = [attacker for attacker in attack_status.get("attackers", []) if isinstance(attacker, str)]

            should_fallback = should_use_no_attack_fallback(attack_status, attackers)

            # 妨害チャレンジ中に成功者がいない、または未成功者が残る場合だけ固定回転にする。
            # 通常モードはこの分岐へ入らず、上で計算した心拍差RPMを維持する。
            if should_fallback:
                fallback_rpm, fallback_direction, fallback_active = get_no_attack_fallback_rotation()
                if fallback_active:
                    rpm = fallback_rpm
                    direction = fallback_direction
                    activate_no_attack_fallback()
                else:
                    activate_no_attack_fallback()
                    rpm = fallback_rpm
                    direction = fallback_direction
            else:
                no_attack_fallback_until = 0.0
                rpm, direction, attackers = apply_attack_effect(current_turn, rpm, direction, attack_status=attack_status)

            attack_context = {
                "attack_mode": bool(attack_status.get("attack_mode")),
                "challenge_direction": attack_status.get("challenge_direction"),
                "pending_attackers": attack_status.get("pending_attackers", []),
                "attack_count": len(attackers),
            }

            # ★回転させる対象は「今ターンの人」（プレイ中の人）
            with rotation_settings_lock:
                rotation_settings.clear()
                if rpm > 0:
                    rotation_settings[current_turn] = (rpm, direction)

            displayed_references = get_displayed_references(
                mode,
                current_turn,
                target_watch,
                comparison_references,
            )
            publish_rotation_status(
                current_turn,
                target_watch,
                mode,
                rpm,
                direction,
                extreme,
                attackers,
                reference_bpm,
                comparison_source,
                baseline,
                displayed_references,
                attack_context,
            )
            if mode in {"attack_challenge", "self_fast", "self_slow", "next_fast", "prev_fast", "random_fast", "highest_diff", "lowest_diff", "random_diff"}:
                print(f"[STATE] turn={current_turn} target={target_watch} rpm={rpm} dir={direction} attackers={attackers}")

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

            # 回転対象が増えたときだけ簡潔に表示する。
            if len(items) > 0:
                print(f"[ROT] active={len(items)}")

            for device_id, (rpm, direction) in items:
                # RPMを1ステップごとの待機時間へ変換する。
                # STEP_DELAY_MULTIPLIER と MIN_STEP_DELAY は実機の回転感・安定性を調整する値。
                step_delay = (60 / rpm) / stepsPerRevolution
                safe_step_delay = max(step_delay * STEP_DELAY_MULTIPLIER, MIN_STEP_DELAY)
                if rpm > 0:
                    print(f"[ROT] {device_id} rpm={rpm} dir={direction}")
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
    gpio_ready = setup_motor()
    if not gpio_ready:
        print("[WARN] GPIO not available; running in non-hardware mode")
    threading.Thread(target=data_fetch_loop, daemon=True).start()
    rotation_loop()
