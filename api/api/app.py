import os
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
import yaml

app = Flask(__name__)

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "telemetry_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")


def get_db_connection():
    retries = 5

    while retries > 0:
        try:
            conn = psycopg2.connect(
                host=DB_HOST,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASS
            )
            return conn
        except psycopg2.OperationalError:
            retries -= 1
            print("Waiting for database connection...")
            time.sleep(2)

    raise Exception("Could not connect to database")


def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS telemetry (
                id SERIAL PRIMARY KEY,
                sensor_id VARCHAR(100) NOT NULL,
                metric_type VARCHAR(50) NOT NULL,
                value NUMERIC NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL
            );
        """)

        conn.commit()
        cur.close()
        conn.close()
        print("Database schema initialized.")
    except Exception as e:
        print(f"Database initialization error: {e}")


def parse_iso_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be an ISO 8601 string")

    if not value.endswith("Z"):
        raise ValueError("timestamp must use UTC format ending with Z")

    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ValueError("timestamp must use format YYYY-MM-DDTHH:MM:SSZ")

    return value


def validate_telemetry_payload(data):
    if not isinstance(data, dict):
        raise ValueError("JSON payload must be an object")

    allowed_fields = {"sensor_id", "metric_type", "value", "timestamp"}
    extra_fields = set(data.keys()) - allowed_fields

    if extra_fields:
        raise ValueError(f"Unknown fields: {', '.join(sorted(extra_fields))}")

    required_fields = ["sensor_id", "metric_type", "value", "timestamp"]
    missing_fields = [field for field in required_fields if field not in data]

    if missing_fields:
        raise ValueError(f"Missing required fields: {', '.join(missing_fields)}")

    sensor_id = data["sensor_id"]
    metric_type = data["metric_type"]

    if not isinstance(sensor_id, str) or not sensor_id.strip() or len(sensor_id) > 100:
        raise ValueError("sensor_id must be a string with 1-100 characters")

    if not isinstance(metric_type, str) or not metric_type.strip() or len(metric_type) > 50:
        raise ValueError("metric_type must be a string with 1-50 characters")

    try:
        value = Decimal(str(data["value"]))
    except (InvalidOperation, ValueError):
        raise ValueError("value must be a number")

    if value < Decimal("-1000000") or value > Decimal("1000000"):
        raise ValueError("value must be between -1000000 and 1000000")

    timestamp = parse_iso_timestamp(data["timestamp"])

    return {
        "sensor_id": sensor_id.strip(),
        "metric_type": metric_type.strip(),
        "value": value,
        "timestamp": timestamp
    }


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200


@app.route("/openapi.json", methods=["GET"])
def get_openapi():
    openapi_path = os.path.join(os.path.dirname(__file__), "..", "openapi.yaml")

    if os.path.exists(openapi_path):
        with open(openapi_path, "r", encoding="utf-8") as f:
            spec = yaml.safe_load(f)
        return jsonify(spec), 200

    return jsonify({"error": "OpenAPI specification not found"}), 404


@app.route("/api/v1/telemetry", methods=["GET"])
def get_telemetry():
    try:
        sensor_id = request.args.get("sensor_id")
        metric_type = request.args.get("metric_type")

        if sensor_id is not None and (not sensor_id.strip() or len(sensor_id) > 100):
            return jsonify({"error": "sensor_id must be a string with 1-100 characters"}), 400

        if metric_type is not None and (not metric_type.strip() or len(metric_type) > 50):
            return jsonify({"error": "metric_type must be a string with 1-50 characters"}), 400

        query = "SELECT id, sensor_id, metric_type, value, timestamp FROM telemetry"
        conditions = []
        params = []

        if sensor_id:
            conditions.append("sensor_id = %s")
            params.append(sensor_id)

        if metric_type:
            conditions.append("metric_type = %s")
            params.append(metric_type)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY id DESC;"

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        for row in rows:
            if isinstance(row["timestamp"], datetime):
                row["timestamp"] = row["timestamp"].isoformat()
            row["value"] = float(row["value"])

        return jsonify(rows), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/v1/telemetry", methods=["POST"])
def post_telemetry():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "No JSON payload provided"}), 400

    try:
        telemetry = validate_telemetry_payload(data)

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO telemetry (sensor_id, metric_type, value, timestamp)
            VALUES (%s, %s, %s, %s)
            RETURNING id, sensor_id, metric_type, value, timestamp;
            """,
            (
                telemetry["sensor_id"],
                telemetry["metric_type"],
                telemetry["value"],
                telemetry["timestamp"]
            )
        )

        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        created = {
            "id": row[0],
            "sensor_id": row[1],
            "metric_type": row[2],
            "value": float(row[3]),
            "timestamp": row[4].isoformat() if isinstance(row[4], datetime) else row[4]
        }

        return jsonify(created), 201

    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)