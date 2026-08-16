import os
import secrets
from datetime import datetime, timezone, timedelta

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
            "SELECT * FROM users WHERE user_id = %s",
            (user_id,)
        ).fetchone()


def create_subscription(user_id, plan="free"):
    subscription_id = secrets.token_urlsafe(16)
    token = secrets.token_urlsafe(32)

    with db() as conn:
        existing = conn.execute(
            """
            SELECT *
            FROM subscriptions
            WHERE user_id = %s
            """,
            (user_id,)
        ).fetchone()

        if existing:
            return dict(existing)

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
                now(),
                None
            )
        )

    return {
        "subscription_id": subscription_id,
        "user_id": user_id,
        "token": token,
        "plan": plan,
        "created_at": now().isoformat(),
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
            existing["plan"]
        )

        return jsonify({
            "ok": True,
            "user_id": user_id,
            "plan": existing["plan"],
            "created": False,
            "subscription": subscription
        })

    with db() as conn:
        conn.execute(
            """
            INSERT INTO users
            (user_id, plan, created_at)
            VALUES (%s, %s, %s)
            """,
            (
                user_id,
                "free",
                now()
            )
        )

    subscription = create_subscription(user_id, "free")

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "plan": "free",
        "created": True,
        "subscription": subscription
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

    subscription = dict(subscription)

    return jsonify({
        "ok": True,
        "subscription": subscription
    })


@app.get("/api/users/<user_id>/subscription")
def user_subscription(user_id):
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
        "subscription": dict(subscription)
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

    with db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id = %s",
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
            (plan, user_id)
        )

        conn.execute(
            """
            UPDATE subscriptions
            SET plan = %s
            WHERE user_id = %s
            """,
            (plan, user_id)
        )

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "plan": plan
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

    user = get_user(user_id)

    if not user:
        return jsonify({
            "ok": False,
            "error": "user not found"
        }), 404

    limits = {
        "free": 1,
        "vip": 5
    }

    limit = limits.get(user["plan"], 1)

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
                "limit": limit
            }), 403

        device_id = secrets.token_hex(16)

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
                now()
            )
        )

    return jsonify({
        "ok": True,
        "device_id": device_id,
        "user_id": user_id,
        "name": name,
        "limit": limit
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
        "devices": [dict(device) for device in devices]
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
            (user_id, device_id)
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


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
