# 例: 平均心拍数を保存するファイル
BASELINE_FILE = 'baseline_data.json'

# 平均を保存する関数（/calculate_baseline 内）
def save_baseline(device_id, average):
    data = load_json_file(BASELINE_FILE)
    data[device_id] = average
    save_json_file(BASELINE_FILE, data)

# チェック関数：接続されたすべてのwatchに平均があるか
def all_devices_have_baseline():
    baseline_data = load_json_file(BASELINE_FILE)
    connected_devices = list(load_json_file('assigned_ids.json').values())  # 例: {"mac1": "watch1", ...}
    missing = [dev for dev in connected_devices if dev not in baseline_data]
    return (len(missing) == 0), missing