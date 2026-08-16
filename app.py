from flask import Flask, request, jsonify
import sqlite3
import uuid
from datetime import datetime, timezone

app = Flask(__name__)

DB = "lukirby.db"


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            plan TEXT NOT NULL DEFAULT 'free',
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    conn.commit()
    conn.close()


@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "service": "LukirbyVPN Backend"
    })


@app.post("/api/users")
def create_user():
    data = request.get_json(silent=True) or {}

    user_id = str(data.get("user_id", "")).strip()

    if not user_id:
        return jsonify({
            "ok": False,
            "error": "user_id is required"
        }), 400

    conn = db()

    existing = conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if existing:
        conn.close()
        return jsonify({
            "ok": True,
            "user_id": user_id,
            "plan": existing["plan"],
            "created": False
        })

    conn.execute(
        "INSERT INTO users (user_id, plan, created_at) VALUES (?, ?, ?)",
        (
            user_id,
            "free",
            datetime.now(timezone.utc).isoformat()
        )
    )

    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "plan": "free",
        "created": True
    })


@app.post("/api/devices")
def add_device():
    data = request.get_json(silent=True) or {}

    user_id = str(data.get("user_id", "")).strip()
    name = str(data.get("name", "Unknown device")).strip()

    if not user_id:
        return jsonify({
            "ok": False,
            "error": "user_id is required"
        }), 400

    conn = db()

    user = conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if not user:
        conn.close()
        return jsonify({
            "ok": False,
            "error": "user not found"
        }), 404

    limits = {
        "free": 1,
        "vip": 5
    }

    limit = limits.get(user["plan"], 1)

    count = conn.execute(
        "SELECT COUNT(*) FROM devices WHERE user_id = ?",
        (user_id,)
    ).fetchone()[0]

    if count >= limit:
        conn.close()
        return jsonify({
            "ok": False,
            "error": "device limit reached",
            "limit": limit
        }), 403

    device_id = uuid.uuid4().hex

    conn.execute(
        """
        INSERT INTO devices
        (device_id, user_id, name, last_seen)
        VALUES (?, ?, ?, ?)
        """,
        (
            device_id,
            user_id,
            name,
            datetime.now(timezone.utc).isoformat()
        )
    )

    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "device_id": device_id,
        "user_id": user_id,
        "name": name,
        "limit": limit
    })


@app.get("/api/devices/<user_id>")
def get_devices(user_id):
    conn = db()

    devices = conn.execute(
        """
        SELECT device_id, name, last_seen
        FROM devices
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchall()

    conn.close()

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "devices": [dict(device) for device in devices]
    })


@app.delete("/api/devices/<user_id>/<device_id>")
def delete_device(user_id, device_id):
    conn = db()

    result = conn.execute(
        """
        DELETE FROM devices
        WHERE user_id = ? AND device_id = ?
        """,
        (user_id, device_id)
    )

    conn.commit()
    deleted = result.rowcount
    conn.close()

    if deleted == 0:
        return jsonify({
            "ok": False,
            "error": "device not found"
        }), 404

    return jsonify({
        "ok": True,
        "deleted": device_id
    })


@app.post("/api/users/<user_id>/plan")
def change_plan(user_id):
    data = request.get_json(silent=True) or {}
    plan = str(data.get("plan", "")).lower()

    if plan not in ("free", "vip"):
        return jsonify({
            "ok": False,
            "error": "plan must be free or vip"
        }), 400

    conn = db()

    result = conn.execute(
        "UPDATE users SET plan = ? WHERE user_id = ?",
        (plan, user_id)
    )

    conn.commit()
    updated = result.rowcount
    conn.close()

    if updated == 0:
        return jsonify({
            "ok": False,
            "error": "user not found"
        }), 404

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "plan": plan
    })


init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000
    )
