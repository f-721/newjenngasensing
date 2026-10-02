from flask import Blueprint, request, jsonify
import json
import os
import threading

id_api = Blueprint('id_api', __name__)

ID_FILE = 'assigned_ids.json'
MAX_DEVICES = 4
file_lock = threading.Lock()
registration_lock = threading.Lock()

def load_ids():
    with file_lock:
        if os.path.exists(ID_FILE):
            with open(ID_FILE) as f:
                return json.load(f)
        return {}

def save_ids(data):
    with file_lock:
        with open(ID_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

@id_api.route('/register', methods=['POST'])
def register_device():
    try:
        ip = request.remote_addr
        with registration_lock:
            ids = load_ids()
            existing_id = ids.get(ip)
            occupied_ids = {watch_id for other_ip, watch_id in ids.items() if other_ip != ip}
            valid_ids = {f"watch{number}" for number in range(1, MAX_DEVICES + 1)}

            if existing_id in valid_ids and existing_id not in occupied_ids:
                new_id = existing_id
            else:
                new_id = next(
                    (f"watch{number}" for number in range(1, MAX_DEVICES + 1)
                     if f"watch{number}" not in occupied_ids),
                    None,
                )
                if new_id is None:
                    return jsonify({"status": "error", "message": "定員に達しています"}), 403
                ids[ip] = new_id
                save_ids(ids)
        print(f"[ID割り振り] {ip} -> {new_id}")
        return jsonify({"status": "ok", "assigned_id": new_id})

    except Exception as e:
        print(f"[エラー] /register: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500