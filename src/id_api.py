from flask import Blueprint, request, jsonify
import json
import os
import threading
from datetime import datetime

id_api = Blueprint('id_api', __name__)

ID_FILE = 'assigned_ids.json'
MAX_DEVICES = 4
file_lock = threading.Lock()

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

from flask import Blueprint, request, jsonify
import json
import os
import threading
from datetime import datetime

id_api = Blueprint('id_api', __name__)

ID_FILE = 'assigned_ids.json'
MAX_DEVICES = 4
file_lock = threading.Lock()

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
        ids = load_ids()

        # すでに登録済みなら再利用
        if ip in ids:
            return jsonify({"status": "ok", "assigned_id": ids[ip]})

        if len(ids) >= MAX_DEVICES:
            return jsonify({"status": "error", "message": "定員に達しています"}), 403

        new_id = f"watch{len(ids)+1}"
        ids[ip] = new_id
        save_ids(ids)
        print(f"[ID割り振り] {ip} -> {new_id}")
        return jsonify({"status": "ok", "assigned_id": new_id})

    except Exception as e:
        print(f"[エラー] /register: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500