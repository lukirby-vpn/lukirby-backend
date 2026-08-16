import os
import uuid
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
        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    subscription_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id)
                        ON DELETE CASCADE,
                    plan TEXT NOT NULL DEFAULT 'free',
                    token TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    expires_at TIMESTAMPTZ
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id)
                        ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    last_seen TIMESTAMPTZ NOT NULL
                )
            """)

            conn.commit()


def device_limit(plan):
    limits = {
        "free": 1,
        "vip": 5,
        "dev": 999999
    }

    return limits.get(plan, 1)


def generate_token():
    return secrets.token_urlsafe(32)


@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "service": "LukirbyVPN Backend",
        "database": "postgresql"
    })


# =========================================================
# USER
# =========================================================

@app.post("/api/users")
def create_user():
    data = request.get_json(silent=True) or {}

    user_id = str(
        data.get("user_id", "")
    ).strip()

    if not user_id:
        return jsonify({
            "ok": False,
            "error": "user_id is required"
        }), 400

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT user_id
                FROM users
                WHERE user_id = %s
                """,
                (user_id,)
            )

            existing = cur.fetchone()

            if existing:

                cur.execute(
                    """
                    SELECT
                        subscription_id,
                        plan,
                        token,
                        created_at,
                        expires_at
                    FROM subscriptions
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (user_id,)
                )

                sub = cur.fetchone()

                if sub:
                    subscription = {
                        "subscription_id": sub[0],
                        "user_id": user_id,
                        "plan": sub[1],
                        "token": sub[2],
                        "created_at": sub[3].isoformat(),
                        "expires_at":
                            sub[4].isoformat()
                            if sub[4]
                            else None
                    }
                else:
                    subscription = None

                return jsonify({
                    "ok": True,
                    "created": False,
                    "user_id": user_id,
                    "plan":
                        subscription["plan"]
                        if subscription
                        else "free",
                    "subscription":
                        subscription
                })

            cur.execute(
                """
                INSERT INTO users
                (user_id, created_at)
                VALUES (%s, %s)
                """,
                (user_id, now())
            )

            subscription_id = (
                uuid.uuid4().hex
            )

            token = generate_token()

            cur.execute(
                """
                INSERT INTO subscriptions
                (
                    subscription_id,
                    user_id,
                    plan,
                    token,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    subscription_id,
                    user_id,
                    "free",
                    token,
                    now()
                )
            )

            conn.commit()

    return jsonify({
        "ok": True,
        "created": True,
        "user_id": user_id,
        "plan": "free",
        "subscription": {
            "subscription_id":
                subscription_id,
            "user_id": user_id,
            "plan": "free",
            "token": token,
            "created_at":
                now().isoformat(),
            "expires_at": None
        }
    })


# =========================================================
# SUBSCRIPTION
# =========================================================

@app.get("/api/subscriptions/<token>")
def get_subscription(token):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    subscription_id,
                    user_id,
                    plan,
                    token,
                    created_at,
                    expires_at
                FROM subscriptions
                WHERE token = %s
                """,
                (token,)
            )

            sub = cur.fetchone()

            if not sub:
                return jsonify({
                    "ok": False,
                    "error":
                        "subscription not found"
                }), 404

            subscription = {
                "subscription_id": sub[0],
                "user_id": sub[1],
                "plan": sub[2],
                "token": sub[3],
                "created_at":
                    sub[4].isoformat(),
                "expires_at":
                    sub[5].isoformat()
                    if sub[5]
                    else None
            }

            cur.execute(
                """
                SELECT COUNT(*)
                FROM devices
                WHERE user_id = %s
                AND status = 'active'
                """,
                (sub[1],)
            )

            active_devices = cur.fetchone()[0]

    limit = device_limit(
        subscription["plan"]
    )

    return jsonify({
        "ok": True,
        "subscription": subscription,
        "active_devices": active_devices,
        "device_limit": limit,
        "blocked":
            active_devices > limit
    })


# =========================================================
# DEVICE ADD
# =========================================================

@app.post("/api/devices")
def add_device():

    data = request.get_json(
        silent=True
    ) or {}

    token = str(
        data.get("token", "")
    ).strip()

    name = str(
        data.get(
            "name",
            "Unknown device"
        )
    ).strip()

    if not token:
        return jsonify({
            "ok": False,
            "error": "token is required"
        }), 400

    if not name:
        name = "Unknown device"

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT user_id, plan
                FROM subscriptions
                WHERE token = %s
                """,
                (token,)
            )

            sub = cur.fetchone()

            if not sub:
                return jsonify({
                    "ok": False,
                    "error":
                        "subscription not found"
                }), 404

            user_id = sub[0]
            plan = sub[1]

            limit = device_limit(plan)

            device_id = uuid.uuid4().hex

            cur.execute(
                """
                INSERT INTO devices
                (
                    device_id,
                    user_id,
                    name,
                    status,
                    last_seen
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    device_id,
                    user_id,
                    name,
                    "active",
                    now()
                )
            )

            conn.commit()

            cur.execute(
                """
                SELECT COUNT(*)
                FROM devices
                WHERE user_id = %s
                AND status = 'active'
                """,
                (user_id,)
            )

            active_devices = cur.fetchone()[0]

    return jsonify({
        "ok": True,
        "device_id": device_id,
        "user_id": user_id,
        "name": name,
        "status": "active",
        "active_devices": active_devices,
        "device_limit": limit,
        "blocked":
            active_devices > limit
    })


# =========================================================
# DEVICES LIST
# =========================================================

@app.get("/api/devices/<user_id>")
def get_devices(user_id):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    device_id,
                    name,
                    status,
                    last_seen
                FROM devices
                WHERE user_id = %s
                ORDER BY last_seen DESC
                """,
                (user_id,)
            )

            rows = cur.fetchall()

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "devices": [
            {
                "device_id": row[0],
                "name": row[1],
                "status": row[2],
                "last_seen":
                    row[3].isoformat()
            }
            for row in rows
        ]
    })


# =========================================================
# DEVICE DELETE / REMOVE
# =========================================================

@app.delete(
    "/api/devices/<user_id>/<device_id>"
)
def delete_device(
    user_id,
    device_id
):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                UPDATE devices
                SET status = 'removed'
                WHERE user_id = %s
                AND device_id = %s
                """,
                (
                    user_id,
                    device_id
                )
            )

            changed = cur.rowcount

            conn.commit()

    if changed == 0:
        return jsonify({
            "ok": False,
            "error": "device not found"
        }), 404

    return jsonify({
        "ok": True,
        "device_id": device_id,
        "status": "removed"
    })


# =========================================================
# DEVICE RESTORE
# =========================================================

@app.post(
    "/api/devices/<user_id>/<device_id>/restore"
)
def restore_device(
    user_id,
    device_id
):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                UPDATE devices
                SET status = 'active',
                    last_seen = %s
                WHERE user_id = %s
                AND device_id = %s
                """,
                (
                    now(),
                    user_id,
                    device_id
                )
            )

            changed = cur.rowcount

            conn.commit()

    if changed == 0:
        return jsonify({
            "ok": False,
            "error": "device not found"
        }), 404

    return jsonify({
        "ok": True,
        "device_id": device_id,
        "status": "active"
    })


# =========================================================
# SUBSCRIPTION STATUS
# =========================================================

@app.get(
    "/api/subscriptions/<token>/status"
)
def subscription_status(token):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT user_id, plan
                FROM subscriptions
                WHERE token = %s
                """,
                (token,)
            )

            sub = cur.fetchone()

            if not sub:
                return jsonify({
                    "ok": False,
                    "error":
                        "subscription not found"
                }), 404

            user_id = sub[0]
            plan = sub[1]
            limit = device_limit(plan)

            cur.execute(
                """
                SELECT COUNT(*)
                FROM devices
                WHERE user_id = %s
                AND status = 'active'
                """,
                (user_id,)
            )

            active_devices = cur.fetchone()[0]

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "plan": plan,
        "active_devices": active_devices,
        "device_limit": limit,
        "blocked":
            active_devices > limit
    })


# =========================================================
# CHANGE PLAN
# =========================================================

@app.post(
    "/api/users/<user_id>/plan"
)
def change_plan(user_id):

    data = request.get_json(
        silent=True
    ) or {}

    plan = str(
        data.get("plan", "")
    ).lower()

    if plan not in (
        "free",
        "vip",
        "dev"
    ):
        return jsonify({
            "ok": False,
            "error":
                "plan must be free, vip or dev"
        }), 400

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
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

            changed = cur.rowcount

            conn.commit()

    if changed == 0:
        return jsonify({
            "ok": False,
            "error":
                "subscription not found"
        }), 404

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "plan": plan,
        "device_limit":
            device_limit(plan)
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
