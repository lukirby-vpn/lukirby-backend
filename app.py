import os
import secrets
from datetime import datetime, timezone

import psycopg
from flask import Flask, request, jsonify

app = Flask(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]


def db():
    return psycopg.connect(DATABASE_URL)


def now():
    return datetime.now(timezone.utc)


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                plan TEXT NOT NULL DEFAULT 'free',
                created_at TIMESTAMPTZ NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                subscription_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL UNIQUE,
                token TEXT NOT NULL UNIQUE,
                plan TEXT NOT NULL DEFAULT 'free',
                created_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                last_seen TIMESTAMPTZ NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)


def get_user(user_id):
    with db() as conn:
        return conn.execute(
            """
            SELECT user_id, plan, created_at
            FROM users
            WHERE user_id = %s
            """,
            (user_id,)
        ).fetchone()


def subscription_to_dict(row):
    if not row:
        return None

    return {
        "subscription_id": row[0],
        "user_id": row[1],
        "token": row[2],
        "plan": row[3],
        "created_at": row[4].isoformat() if row[4] else None,
        "expires_at": row[5].isoformat() if row[5] else None
    }


def create_subscription(user_id, plan="free"):
    with db() as conn:

        existing = conn.execute(
            """
            SELECT
                subscription_id,
                user_id,
                token,
                plan,
                created_at,
                expires_at
            FROM subscriptions
            WHERE user_id = %s
            """,
            (user_id,)
        ).fetchone()

        if existing:
            return subscription_to_dict(existing)

        subscription_id = secrets.token_urlsafe(16)
        token = secrets.token_urlsafe(32)
        created_at = now()

        conn.execute(
            """
            INSERT INTO subscriptions
            (
                subscription_id,
                user_id,
                token,
                plan,
                created_at,
                expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                subscription_id,
                user_id,
                token,
                plan,
                created_at,
                None
            )
        )

        return {
            "subscription_id": subscription_id,
            "user_id": user_id,
            "token": token,
            "plan": plan,
            "created_at": created_at.isoformat(),
            "expires_at": None
        }


@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "service": "LukirbyVPN Backend",
        "database": "postgresql"
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

    existing = get_user(user_id)

    if existing:
        subscription = create_subscription(
            user_id,
            existing[1]
        )

        return jsonify({
            "ok": True,
            "user_id": user_id,
            "plan": existing[1],
            "created": False,
            "subscription": subscription
        })

    created_at = now()

    with db() as conn:
        conn.execute(
            """
            INSERT INTO users
            (
                user_id,
                plan,
                created_at
            )
            VALUES (%s, %s, %s)
            """,
            (
                user_id,
                "free",
                created_at
            )
        )

    subscription = create_subscription(
        user_id,
        "free"
    )

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "plan": "free",
        "created": True,
        "subscription": subscription
    })


@app.get("/api/users/<user_id>")
def get_user_info(user_id):
    user = get_user(user_id)

    if not user:
        return jsonify({
            "ok": False,
            "error": "user not found"
        }), 404

    return jsonify({
        "ok": True,
        "user_id": user[0],
        "plan": user[1],
        "created_at": user[2].isoformat()
            if user[2] else None
    })


@app.get("/api/users/<user_id>/subscription")
def get_user_subscription(user_id):
    with db() as conn:
        subscription = conn.execute(
            """
            SELECT
                subscription_id,
                user_id,
                token,
                plan,
                created_at,
                expires_at
            FROM subscriptions
            WHERE user_id = %s
            """,
            (user_id,)
        ).fetchone()

    if not subscription:
        return jsonify({
            "ok": False,
            "error": "subscription not found"
        }), 404

    return jsonify({
        "ok": True,
        "subscription": subscription_to_dict(subscription)
    })


@app.get("/api/subscriptions/<token>")
def get_subscription(token):
    with db() as conn:
        subscription = conn.execute(
            """
            SELECT
                subscription_id,
                user_id,
                token,
                plan,
                created_at,
                expires_at
            FROM subscriptions
            WHERE token = %s
            """,
            (token,)
        ).fetchone()

    if not subscription:
        return jsonify({
            "ok": False,
            "error": "subscription not found"
        }), 404

    return jsonify({
        "ok": True,
        "subscription": subscription_to_dict(subscription)
    })


@app.post("/api/users/<user_id>/plan")
def change_plan(user_id):
    data = request.get_json(silent=True) or {}

    plan = str(
        data.get("plan", "")
    ).lower().strip()

    if plan not in ("free", "vip", "dev"):
        return jsonify({
            "ok": False,
            "error": "plan must be free, vip or dev"
        }), 400

    with db() as conn:

        user = conn.execute(
            """
            SELECT user_id
            FROM users
            WHERE user_id = %s
            """,
            (user_id,)
        ).fetchone()

        if not user:
            return jsonify({
                "ok": False,
                "error": "user not found"
            }), 404

        conn.execute(
            """
            UPDATE users
            SET plan = %s
            WHERE user_id = %s
            """,
            (
                plan,
                user_id
            )
        )

        conn.execute(
            """
            UPDATE subscriptions
            SET plan = %s
            WHERE user_id = %s
            """,
            (
                plan,
                user_id
            )
        )

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "plan": plan
    })


def get_device_limit(plan):
    limits = {
        "free": 1,
        "vip": 5,
        "dev": 20
    }

    return limits.get(plan, 1)


@app.post("/api/devices")
def add_device():
    data = request.get_json(silent=True) or {}

    user_id = str(
        data.get("user_id", "")
    ).strip()

    name = str(
        data.get("name", "Unknown device")
    ).strip()

    if not user_id:
        return jsonify({
            "ok": False,
            "error": "user_id is required"
        }), 400

    user = get_user(user_id)

    if not user:
        return jsonify({
            "ok": False,
            "error": "user not found"
        }), 404

    plan = user[1]
    limit = get_device_limit(plan)

    with db() as conn:

        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM devices
            WHERE user_id = %s
            """,
            (user_id,)
        ).fetchone()[0]

        if count >= limit:
            return jsonify({
                "ok": False,
                "error": "device limit reached",
                "limit": limit,
                "active_devices": count
            }), 403

        device_id = secrets.token_hex(16)
        last_seen = now()

        conn.execute(
            """
            INSERT INTO devices
            (
                device_id,
                user_id,
                name,
                last_seen
            )
            VALUES (%s, %s, %s, %s)
            """,
            (
                device_id,
                user_id,
                name,
                last_seen
            )
        )

    return jsonify({
        "ok": True,
        "device_id": device_id,
        "user_id": user_id,
        "name": name,
        "plan": plan,
        "limit": limit,
        "active_devices": count + 1
    })


@app.get("/api/devices/<user_id>")
def get_devices(user_id):
    with db() as conn:

        devices = conn.execute(
            """
            SELECT
                device_id,
                name,
                last_seen
            FROM devices
            WHERE user_id = %s
            ORDER BY last_seen DESC
            """,
            (user_id,)
        ).fetchall()

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "devices": [
            {
                "device_id": device[0],
                "name": device[1],
                "last_seen": device[2].isoformat()
                    if device[2] else None
            }
            for device in devices
        ]
    })


@app.post("/api/devices/<device_id>/heartbeat")
def device_heartbeat(device_id):
    with db() as conn:

        result = conn.execute(
            """
            UPDATE devices
            SET last_seen = %s
            WHERE device_id = %s
            """,
            (
                now(),
                device_id
            )
        )

    if result.rowcount == 0:
        return jsonify({
            "ok": False,
            "error": "device not found"
        }), 404

    return jsonify({
        "ok": True,
        "device_id": device_id
    })


@app.delete("/api/devices/<user_id>/<device_id>")
def delete_device(user_id, device_id):
    with db() as conn:

        result = conn.execute(
            """
            DELETE FROM devices
            WHERE user_id = %s
            AND device_id = %s
            """,
            (
                user_id,
                device_id
            )
        )

    if result.rowcount == 0:
        return jsonify({
            "ok": False,
            "error": "device not found"
        }), 404

    return jsonify({
        "ok": True,
        "deleted": device_id
    })


@app.get("/api/users/<user_id>/devices")
def user_devices(user_id):
    with db() as conn:

        devices = conn.execute(
            """
            SELECT
                device_id,
                name,
                last_seen
            FROM devices
            WHERE user_id = %s
            ORDER BY last_seen DESC
            """,
            (user_id,)
        ).fetchall()

    user = get_user(user_id)

    if not user:
        return jsonify({
            "ok": False,
            "error": "user not found"
        }), 404

    plan = user[1]
    limit = get_device_limit(plan)

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "plan": plan,
        "limit": limit,
        "active_devices": len(devices),
        "devices": [
            {
                "device_id": device[0],
                "name": device[1],
                "last_seen": device[2].isoformat()
                    if device[2] else None
            }
            for device in devices
        ]
    })


init_db()


if __name__ == "__main__":
    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
