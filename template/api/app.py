import os
import time
from datetime import datetime
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
import yaml

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

DB_HOST = os.getenv('DB_HOST', 'db')
DB_NAME = os.getenv('DB_NAME', 'telemetry')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASS = os.getenv('DB_PASS', 'postgres')

def get_db_connection():
    retries = 10
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
            logger.info(f"Väntar på databas... {retries} försök kvar")
            time.sleep(2)
    raise Exception("Kunde inte ansluta till databasen")

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS telemetry (
                id SERIAL PRIMARY KEY,
                sensor_id VARCHAR(255) NOT NULL,
                metric_type VARCHAR(100) NOT NULL,
                value FLOAT NOT NULL,
                timestamp TIMESTAMP NOT NULL
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sensor_id ON telemetry(sensor_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON telemetry(timestamp DESC)")
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Databas initierad")
    except Exception as e:
        logger.error(f"Databasfel: {e}")
        raise

def validate_telemetry(data):
    required = ['sensor_id', 'metric_type', 'value']
    for field in required:
        if field not in data:
            return False, f"Saknar fält: {field}"
    
    if not isinstance(data['sensor_id'], str) or len(data['sensor_id'].strip()) == 0:
        return False, "sensor_id måste vara en sträng"
    
    if not isinstance(data['metric_type'], str) or len(data['metric_type'].strip()) == 0:
        return False, "metric_type måste vara en sträng"
    
    if not isinstance(data['value'], (int, float)):
        return False, "value måste vara ett nummer"
    
    if 'timestamp' in data and data['timestamp']:
        try:
            datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
        except ValueError:
            return False, "Ogiltigt timestamp-format (ISO 8601)"
    
    return True, "OK"

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"}), 200

@app.route('/openapi.json', methods=['GET'])
def get_openapi():
    try:
        path = os.path.join(os.path.dirname(__file__), '..', 'openapi.yaml')
        with open(path, 'r', encoding='utf-8') as f:
            spec = yaml.safe_load(f)
        return jsonify(spec), 200
    except:
        return jsonify({"error": "OpenAPI spec hittades inte"}), 404

@app.route('/api/v1/telemetry', methods=['POST'])
def post_telemetry():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Ingen JSON"}), 400
    
    valid, msg = validate_telemetry(data)
    if not valid:
        return jsonify({"error": msg}), 400
    
    sensor_id = data['sensor_id'].strip()
    metric_type = data['metric_type'].strip()
    value = data['value']
    ts = data.get('timestamp')
    
    if ts:
        try:
            ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except:
            return jsonify({"error": "Ogiltigt timestamp"}), 400
    else:
        ts = datetime.utcnow()
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO telemetry (sensor_id, metric_type, value, timestamp)
            VALUES (%s, %s, %s, %s)
            RETURNING id, sensor_id, metric_type, value, timestamp
        """, (sensor_id, metric_type, value, ts))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "id": row[0],
            "sensor_id": row[1],
            "metric_type": row[2],
            "value": float(row[3]),
            "timestamp": row[4].isoformat() + 'Z'
        }), 201
    except Exception as e:
        logger.error(f"POST-fel: {e}")
        return jsonify({"error": "Serverfel"}), 500

@app.route('/api/v1/telemetry', methods=['GET'])
def get_telemetry():
    try:
        sensor_id = request.args.get('sensor_id')
        metric_type = request.args.get('metric_type')
        limit = request.args.get('limit', 50, type=int)
        limit = max(1, min(limit, 100))
        
        query = "SELECT id, sensor_id, metric_type, value, timestamp FROM telemetry WHERE 1=1"
        params = []
        
        if sensor_id:
            query += " AND sensor_id = %s"
            params.append(sensor_id)
        if metric_type:
            query += " AND metric_type = %s"
            params.append(metric_type)
        
        query += " ORDER BY timestamp DESC LIMIT %s"
        params.append(limit)
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        result = []
        for row in rows:
            result.append({
                "id": row['id'],
                "sensor_id": row['sensor_id'],
                "metric_type": row['metric_type'],
                "value": float(row['value']),
                "timestamp": row['timestamp'].isoformat() + 'Z'
            })
        
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"GET-fel: {e}")
        return jsonify({"error": "Serverfel"}), 500

if __name__ == '__main__':
    init_db()
    logger.info("API startar på port 5000")
    app.run(host='0.0.0.0', port=5000, debug=False)