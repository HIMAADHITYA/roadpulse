import os
import json
import base64
import time
import sqlite3
import threading
import random
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
from functools import wraps
import hashlib
import math

try:
    import jwt
except ImportError:
    jwt = None
    print("[WARN] PyJWT not installed. Run: pip install PyJWT")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(DB_DIR, 'roadpulse.db')
os.makedirs(DB_DIR, exist_ok=True)

PERUNGUDI_CENTER = {'lat': 12.96095, 'lon': 80.24094}
CLUSTER_RADIUS_M = 50
MAX_CLUSTERS_SCAN = 500
ROAD_SEGMENT_RADIUS_M = 80
SEGMENT_REPEAT_THRESHOLD = 2

COST_ESTIMATION = {
    'base_cost': 500,
    'per_depth_mm': 2,
    'per_radius_cm': 50,
    'severity_multiplier': {
        'LOW': 0.8,
        'MODERATE': 1.0,
        'HIGH': 1.5,
        'CRITICAL': 2.0,
    },
}

# -------------------------------
# Utility helpers
# -------------------------------
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def make_token(user_id, username, role):
    if jwt is None:
        return None
    return jwt.encode({
        'id': user_id,
        'username': username,
        'role': role,
        'exp': datetime.utcnow() + timedelta(days=30),
    }, app.config['SECRET_KEY'], algorithm='HS256')


def verify_token(token):
    if jwt is None:
        return None
    try:
        return jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
    except Exception:
        return None


def require_auth(roles=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            token = request.headers.get('Authorization', '').replace('Bearer ', '')
            user = verify_token(token) if token else None
            if not user:
                return jsonify({'error': 'Unauthorized'}), 401
            if roles and user.get('role') not in roles:
                return jsonify({'error': 'Forbidden'}), 403
            request.user = user
            return f(*args, **kwargs)
        return wrapper
    return decorator


def ensure_data_url(b64):
    if not b64:
        return ''
    if b64.startswith('data:image'):
        return b64
    return 'data:image/jpeg;base64,' + b64


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def generate_nearby_coordinates(center_lat, center_lon, count=12, radius_km=2.5):
    coords = []
    for _ in range(count):
        angle = random.uniform(0, 2 * math.pi)
        distance_km = random.uniform(0, radius_km)
        lat_offset = (distance_km / 111.0) * math.cos(angle)
        lon_offset = (distance_km / 111.0) * math.sin(angle) / math.cos(math.radians(center_lat))
        coords.append({
            'lat': center_lat + lat_offset,
            'lon': center_lon + lon_offset,
        })
    return coords


def calculate_repair_cost(depth_mm, radius_cm, priority):
    base = COST_ESTIMATION['base_cost']
    depth_cost = COST_ESTIMATION['per_depth_mm'] * (depth_mm or 0)
    radius_cost = COST_ESTIMATION['per_radius_cm'] * (radius_cm or 0)
    mult = COST_ESTIMATION['severity_multiplier'].get(priority, 1.0)
    return round((base + depth_cost + radius_cost) * mult, 2)


# -------------------------------
# Database init & migration
# -------------------------------
def seed_demo_users(cursor):
    default_user_config = [
        ('admin', os.getenv('ADMIN_PASSWORD', 'admin123'), 'admin', 'Administrator', 'admin@roadpulse.local', 'Municipality'),
        ('public', os.getenv('PUBLIC_PASSWORD', 'public123'), 'public', 'Citizen', 'public@roadpulse.local', 'Citizen Network'),
        ('contractor', os.getenv('CONTRACTOR_PASSWORD', 'contract123'), 'contractor', 'Contractor', 'contractor@roadpulse.local', 'RoadWorks Pvt Ltd'),
    ]
    for username, raw_password, role, full_name, email, organization in default_user_config:
        try:
            cursor.execute(
                """INSERT INTO users (username, email, password_hash, role, full_name, organization)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (username, email, hash_password(raw_password), role, full_name, organization)
            )
        except sqlite3.IntegrityError:
            continue


def seed_demo_data(cursor):
    sample_points = generate_nearby_coordinates(PERUNGUDI_CENTER['lat'], PERUNGUDI_CENTER['lon'], count=15, radius_km=2.0)
    severities = [25, 50, 75, 100]
    priorities = ['LOW', 'MODERATE', 'HIGH', 'CRITICAL']
    statuses = ['new', 'assigned', 'in_progress', 'resolved']
    for idx, point in enumerate(sample_points):
        severity = severities[idx % len(severities)]
        priority = priorities[idx % len(priorities)]
        status = statuses[idx % len(statuses)]
        depth_mm = random.randint(20, 110)
        radius_cm = random.randint(15, 50)
        confidence = round(random.uniform(0.77, 0.99), 2)
        estimated_cost = calculate_repair_cost(depth_mm, radius_cm, priority)
        cursor.execute(
            """INSERT INTO detections
               (device_id, timestamp, confidence, severity, priority, lat, lon, depth_mm, depth_cm,
                radius_cm, status, estimated_cost, duplicate_count, gas_detected, accel_x, accel_y, accel_z)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                'perungudi-demo',
                datetime.now().isoformat(),
                confidence,
                severity,
                priority,
                point['lat'],
                point['lon'],
                depth_mm,
                round(depth_mm / 10.0, 2),
                radius_cm,
                status,
                estimated_cost,
                random.randint(1, 3),
                0,
                round(random.uniform(-1, 1), 2),
                round(random.uniform(-1, 1), 2),
                round(random.uniform(-1, 1), 2),
            )
        )


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        full_name TEXT,
        phone TEXT,
        organization TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS detections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT,
        timestamp TEXT,
        image_b64 TEXT,
        full_frame_b64 TEXT,
        bbox TEXT,
        confidence REAL,
        severity INTEGER,
        priority TEXT,
        lat REAL,
        lon REAL,
        depth_mm INTEGER,
        depth_cm REAL,
        radius_cm INTEGER,
        gas_detected INTEGER,
        accel_x REAL,
        accel_y REAL,
        accel_z REAL,
        status TEXT DEFAULT 'new',
        repair_date TEXT,
        repair_notes TEXT,
        reported_by INTEGER,
        assigned_to INTEGER,
        estimated_cost REAL,
        duplicate_count INTEGER DEFAULT 1,
        cluster_id INTEGER,
        road_segment_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        detection_id INTEGER NOT NULL,
        ticket_number TEXT UNIQUE,
        assigned_to INTEGER,
        status TEXT DEFAULT 'open',
        priority TEXT,
        estimated_cost REAL,
        actual_cost REAL,
        notes TEXT,
        before_image_b64 TEXT,
        after_image_b64 TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT,
        FOREIGN KEY(detection_id) REFERENCES detections(id),
        FOREIGN KEY(assigned_to) REFERENCES users(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS location_clusters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cluster_name TEXT UNIQUE,
        center_lat REAL,
        center_lon REAL,
        radius_m INTEGER DEFAULT 50,
        detection_count INTEGER DEFAULT 1,
        latest_detection_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS road_segments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        segment_name TEXT UNIQUE,
        center_lat REAL,
        center_lon REAL,
        pothole_count INTEGER DEFAULT 0,
        severity_avg REAL DEFAULT 0,
        status TEXT DEFAULT 'normal',
        is_uneven INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS repairs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        contractor_id INTEGER,
        status TEXT DEFAULT 'pending',
        started_at TEXT,
        completed_at TEXT,
        before_image_b64 TEXT,
        after_image_b64 TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT,
        timestamp TEXT,
        depth_cm REAL,
        accel_x REAL,
        accel_y REAL,
        accel_z REAL,
        fps REAL,
        lat REAL,
        lon REAL,
        detections TEXT
    )""")

    c.execute("PRAGMA table_info(detections)")
    existing = {r[1] for r in c.fetchall()}
    migrations = {
        'cluster_id': 'ALTER TABLE detections ADD COLUMN cluster_id INTEGER',
        'radius_cm': 'ALTER TABLE detections ADD COLUMN radius_cm INTEGER',
        'estimated_cost': 'ALTER TABLE detections ADD COLUMN estimated_cost REAL',
        'duplicate_count': 'ALTER TABLE detections ADD COLUMN duplicate_count INTEGER DEFAULT 1',
        'depth_cm': 'ALTER TABLE detections ADD COLUMN depth_cm REAL',
        'road_segment_id': 'ALTER TABLE detections ADD COLUMN road_segment_id INTEGER',
    }
    for col, sql in migrations.items():
        if col not in existing:
            print(f"[DB] adding column: {col}")
            c.execute(sql)

    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        seed_demo_users(c)

    c.execute("SELECT COUNT(*) FROM detections")
    if c.fetchone()[0] == 0:
        seed_demo_data(c)

    conn.commit()
    conn.close()


# -------------------------------
# Cluster, duplicate and segment logic
# -------------------------------
def find_duplicate_cluster(lat, lon, detection_id=None, threshold_m=CLUSTER_RADIUS_M):
    if lat is None or lon is None:
        return None, 1
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, center_lat, center_lon, detection_count FROM location_clusters ORDER BY id DESC LIMIT ?", (MAX_CLUSTERS_SCAN,))
    rows = c.fetchall()
    for cid, clat, clon, ccount in rows:
        if haversine_m(lat, lon, clat, clon) <= threshold_m:
            new_count = (ccount or 1) + 1
            if detection_id is not None:
                c.execute("UPDATE location_clusters SET detection_count = ?, latest_detection_id = ? WHERE id = ?", (new_count, detection_id, cid))
            else:
                c.execute("UPDATE location_clusters SET detection_count = ? WHERE id = ?", (new_count, cid))
            conn.commit(); conn.close(); return cid, new_count
    name = f"cluster_{lat:.5f}_{lon:.5f}_{int(time.time()*1000)}"
    c.execute("INSERT INTO location_clusters (cluster_name, center_lat, center_lon, radius_m, detection_count, latest_detection_id) VALUES (?, ?, ?, ?, ?, ?)",
              (name, lat, lon, threshold_m, 1, detection_id))
    conn.commit(); new_id = c.lastrowid; conn.close(); return new_id, 1


def get_or_create_road_segment(lat, lon, severity=0):
    if lat is None or lon is None:
        return None
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, center_lat, center_lon, pothole_count, severity_avg FROM road_segments ORDER BY id DESC LIMIT 200")
    rows = c.fetchall()
    for rid, rlat, rlon, count, avg in rows:
        if haversine_m(lat, lon, rlat, rlon) <= ROAD_SEGMENT_RADIUS_M:
            conn.close()
            return rid
    name = f"road_segment_{lat:.5f}_{lon:.5f}_{int(time.time()*1000)}"
    c.execute("INSERT INTO road_segments (segment_name, center_lat, center_lon, pothole_count, severity_avg, status, is_uneven) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (name, lat, lon, 1, severity, 'normal', 0))
    conn.commit(); new_id = c.lastrowid; conn.close(); return new_id


def update_road_segment_counts(lat, lon, severity):
    if lat is None or lon is None:
        return None
    segment_id = get_or_create_road_segment(lat, lon, severity)
    if segment_id is None:
        return None
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT pothole_count, severity_avg FROM road_segments WHERE id=?", (segment_id,))
    row = c.fetchone()
    if row:
        count = (row[0] or 0) + 1
        avg = ((row[1] or 0) * max((row[0] or 0), 1) + max(severity, 0)) / max(count, 1)
        is_uneven = 1 if count >= SEGMENT_REPEAT_THRESHOLD else 0
        status = 'uneven' if is_uneven else 'normal'
        c.execute("UPDATE road_segments SET pothole_count=?, severity_avg=?, status=?, is_uneven=? WHERE id=?", (count, avg, status, is_uneven, segment_id))
    conn.commit(); conn.close(); return segment_id


# -------------------------------
# In-memory telemetry
# -------------------------------
_telemetry_lock = threading.Lock()
_latest_telemetry = {
    'device_id': None,
    'timestamp': None,
    'image_b64': '',
    'lat': None,
    'lon': None,
    'depth_cm': 0.0,
    'accel_x': 0.0,
    'accel_y': 0.0,
    'accel_z': 0.0,
    'fps': 0.0,
    'detections': None,
}
_telemetry_history = []
TELEMETRY_HISTORY_MAX = 50
TELEMETRY_PERSIST = os.getenv('TELEMETRY_PERSIST', '0') == '1'


# -------------------------------
# Routes
# -------------------------------
@app.route('/')
def index():
    return render_template('dashboard.html')


@app.route('/health')
def health():
    return jsonify({'status': 'ok'}), 200


@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    role = data.get('role', 'public')
    if not username or not password:
        return jsonify({'error': 'username and password required'}), 400
    if role not in ['public', 'admin', 'contractor']:
        return jsonify({'error': 'Invalid role'}), 400
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("""INSERT INTO users (username, email, password_hash, role, full_name, phone, organization)
                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                  (username, email, hash_password(password), role,
                   data.get('full_name'), data.get('phone'), data.get('organization')))
        conn.commit(); uid = c.lastrowid; conn.close()
        return jsonify({'status': 'ok', 'token': make_token(uid, username, role), 'user': {'id': uid, 'username': username, 'role': role}}), 201
    except sqlite3.IntegrityError:
        conn.close(); return jsonify({'error': 'User already exists'}), 409


@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = data.get('username')
    password = data.get('password')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, role, is_active FROM users WHERE username=? AND password_hash=?", (username, hash_password(password)))
    user = c.fetchone()
    conn.close()
    if not user or not user[2]:
        return jsonify({'error': 'Invalid credentials'}), 401
    return jsonify({'status': 'ok', 'token': make_token(user[0], username, user[1]), 'user': {'id': user[0], 'username': username, 'role': user[1]}}), 200


@app.route('/api/full_event', methods=['POST'])
def full_event():
    data = request.get_json() or {}
    device_id = data.get('device_id', 'mobile-app')
    lat = data.get('lat')
    lon = data.get('lon')

    depth_cm_raw = data.get('depth_cm')
    if depth_cm_raw is not None:
        depth_cm = float(depth_cm_raw)
        depth_mm = int(round(depth_cm * 10))
    else:
        depth_mm = int(data.get('depth_mm') or 0)
        depth_cm = round(depth_mm / 10.0, 2)

    radius_cm = int(data.get('radius_cm') or 0)
    severity = int(data.get('severity', 50) or 50)
    priority = data.get('priority') or (
        'CRITICAL' if severity >= 75 else
        'HIGH' if severity >= 50 else
        'MODERATE' if severity >= 25 else 'LOW')

    cluster_id, duplicate_count = find_duplicate_cluster(lat, lon)
    estimated_cost = calculate_repair_cost(depth_mm, radius_cm, priority)
    road_segment_id = update_road_segment_counts(lat, lon, severity)

    image_b64 = ensure_data_url(data.get('image_b64', ''))
    full_b64 = ensure_data_url(data.get('full_frame_b64', ''))

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO detections
        (device_id, timestamp, image_b64, full_frame_b64, bbox,
         confidence, severity, priority, lat, lon, depth_mm, depth_cm, radius_cm,
         gas_detected, accel_x, accel_y, accel_z, status,
         estimated_cost, duplicate_count, cluster_id, road_segment_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (device_id,
               data.get('timestamp') or datetime.now().isoformat(),
               image_b64,
               full_b64,
               json.dumps(data.get('bbox', [])),
               data.get('confidence'),
               severity,
               priority,
               lat,
               lon,
               depth_mm,
               depth_cm,
               radius_cm,
               data.get('gas_detected', 0),
               data.get('accel_x'),
               data.get('accel_y'),
               data.get('accel_z'),
               'new',
               estimated_cost,
               duplicate_count,
               cluster_id,
               road_segment_id))
    conn.commit(); det_id = c.lastrowid
    if cluster_id:
        c.execute("UPDATE location_clusters SET latest_detection_id=? WHERE id=?", (det_id, cluster_id))

    ticket_number = f"TKT-{det_id}-{int(time.time())}"
    c.execute("INSERT INTO tickets (detection_id, ticket_number, priority, estimated_cost, status) VALUES (?, ?, ?, ?, ?)",
              (det_id, ticket_number, priority, estimated_cost, 'open'))
    conn.commit(); conn.close()

    with _telemetry_lock:
        _latest_telemetry.update({
            'device_id': device_id,
            'timestamp': data.get('timestamp') or datetime.utcnow().isoformat(),
            'image_b64': full_b64,
            'lat': lat,
            'lon': lon,
            'depth_cm': depth_cm,
            'accel_x': data.get('accel_x', 0.0),
            'accel_y': data.get('accel_y', 0.0),
            'accel_z': data.get('accel_z', 0.0),
            'fps': data.get('fps', 0.0),
            'detections': {
                'count': 1,
                'top_severity': severity,
                'top_priority': priority,
                'top_confidence': data.get('confidence', 0.0),
                'top_bbox': data.get('bbox', []),
            },
        })
        _telemetry_history.append({
            'timestamp': data.get('timestamp') or datetime.utcnow().isoformat(),
            'depth_cm': depth_cm,
            'accel_z': data.get('accel_z', 0.0),
            'severity': severity,
        })
        if len(_telemetry_history) > TELEMETRY_HISTORY_MAX:
            _telemetry_history.pop(0)

    return jsonify({
        'status': 'ok',
        'id': det_id,
        'cluster_id': cluster_id,
        'duplicate_count': duplicate_count,
        'estimated_cost': estimated_cost,
        'ticket_number': ticket_number,
        'depth_cm': depth_cm,
        'depth_mm': depth_mm,
        'road_segment_id': road_segment_id,
    }), 201


@app.route('/api/detections')
def api_detections():
    limit = request.args.get('limit', 200, type=int)
    priority = request.args.get('priority')
    status = request.args.get('status')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = """SELECT id, timestamp, lat, lon, depth_mm, radius_cm, severity, priority,
                      image_b64, full_frame_b64, status, confidence, device_id,
                      estimated_cost, duplicate_count, cluster_id, road_segment_id,
                      accel_x, accel_y, accel_z, depth_cm
               FROM detections WHERE 1=1"""
    params = []
    if priority:
        query += ' AND priority=?'; params.append(priority)
    if status:
        query += ' AND status=?'; params.append(status)
    query += ' ORDER BY id DESC LIMIT ?'; params.append(limit)
    c.execute(query, params)
    rows = c.fetchall(); conn.close()
    out = []
    for r in rows:
        out.append({
            'id': r[0], 'timestamp': r[1], 'lat': r[2], 'lon': r[3], 'depth_mm': r[4], 'radius_cm': r[5],
            'severity': r[6], 'priority': r[7], 'image': ensure_data_url(r[8]), 'full_frame': ensure_data_url(r[9]),
            'status': r[10], 'confidence': r[11], 'device_id': r[12], 'estimated_cost': r[13],
            'duplicate_count': r[14] or 1, 'cluster_id': r[15], 'road_segment_id': r[16],
            'accel_x': r[17], 'accel_y': r[18], 'accel_z': r[19],
            'depth_cm': r[20] if r[20] is not None else round((r[4] or 0) / 10.0, 2),
        })
    return jsonify(out)


@app.route('/api/heatmap')
def api_heatmap():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT lat, lon, severity, duplicate_count FROM detections WHERE lat IS NOT NULL AND lon IS NOT NULL")
    rows = c.fetchall(); conn.close()
    heatmap = []
    for lat, lon, severity, dup_count in rows:
        heatmap.append({
            'lat': lat,
            'lon': lon,
            'intensity': min(1.0, ((severity or 0) / 100.0) * ((dup_count or 1) * 0.25 + 1)),
            'severity': severity or 0,
            'duplicate_count': dup_count or 1,
        })
    return jsonify(heatmap)


@app.route('/api/road_segments')
def api_road_segments():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, segment_name, center_lat, center_lon, pothole_count, severity_avg, status, is_uneven FROM road_segments ORDER BY id DESC LIMIT 200")
    rows = c.fetchall(); conn.close()
    return jsonify([{ 'id': r[0], 'segment_name': r[1], 'lat': r[2], 'lon': r[3], 'pothole_count': r[4], 'severity_avg': r[5], 'status': r[6], 'is_uneven': r[7] } for r in rows])


@app.route('/api/stats')
def api_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM detections")
    total = c.fetchone()[0]
    c.execute("SELECT priority, COUNT(*) FROM detections GROUP BY priority")
    by_priority = {r[0]: r[1] for r in c.fetchall()}
    c.execute("SELECT status, COUNT(*) FROM detections GROUP BY status")
    by_status = {r[0]: r[1] for r in c.fetchall()}
    c.execute("SELECT AVG(severity) FROM detections")
    avg_sev = c.fetchone()[0] or 0
    c.execute("SELECT SUM(estimated_cost) FROM detections WHERE status != 'resolved'")
    total_cost = c.fetchone()[0] or 0
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    c.execute("SELECT COUNT(*) FROM detections WHERE severity>=75 AND timestamp> ?", (cutoff,))
    crit24 = c.fetchone()[0]
    conn.close()
    return jsonify({
        'total': total,
        'by_priority': by_priority,
        'by_status': by_status,
        'avg_severity': round(float(avg_sev), 2),
        'critical_24h': crit24,
        'total_estimated_cost': round(float(total_cost), 2),
    })


@app.route('/api/update_status/<int:detection_id>', methods=['PATCH'])
def update_status(detection_id):
    data = request.get_json() or {}
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if token:
        user = verify_token(token)
        if not user or user.get('role') not in ('admin', 'contractor'):
            return jsonify({'error': 'Forbidden'}), 403
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE detections SET status=?, repair_date=?, repair_notes=? WHERE id=?",
              (data.get('status'), data.get('repair_date'), data.get('repair_notes'), detection_id))
    conn.commit(); conn.close(); return jsonify({'status': 'ok'}), 200


@app.route('/api/detections/<int:det_id>/upvote', methods=['POST'])
def upvote(det_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT cluster_id FROM detections WHERE id=?", (det_id,))
    row = c.fetchone();
    if not row:
        conn.close(); return jsonify({'error': 'not found'}), 404
    cid = row[0]
    if cid:
        c.execute("UPDATE location_clusters SET detection_count = COALESCE(detection_count,1) + 1 WHERE id=?", (cid,))
        c.execute("UPDATE detections SET duplicate_count = COALESCE(duplicate_count,1) + 1 WHERE cluster_id=?", (cid,))
    else:
        c.execute("UPDATE detections SET duplicate_count = COALESCE(duplicate_count,1) + 1 WHERE id=?", (det_id,))
    conn.commit(); conn.close(); return jsonify({'status': 'ok'})


@app.route('/api/tickets')
def get_tickets():
    status = request.args.get('status')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = """SELECT t.id, t.detection_id, t.ticket_number, t.status, t.priority,
                      t.estimated_cost, t.actual_cost, t.created_at,
                      d.lat, d.lon, d.severity
               FROM tickets t JOIN detections d ON t.detection_id = d.id WHERE 1=1"""
    params = []
    if status:
        query += ' AND t.status=?'; params.append(status)
    query += ' ORDER BY t.created_at DESC'
    c.execute(query, params)
    rows = c.fetchall(); conn.close()
    return jsonify([{ 'id': r[0], 'detection_id': r[1], 'ticket_number': r[2], 'status': r[3], 'priority': r[4],
        'estimated_cost': r[5], 'actual_cost': r[6], 'created_at': r[7], 'lat': r[8], 'lon': r[9], 'severity': r[10] } for r in rows])


@app.route('/api/tickets/<int:ticket_id>/assign', methods=['PATCH'])
def assign_ticket(ticket_id):
    data = request.get_json() or {}
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE tickets SET assigned_to=?, status='assigned' WHERE id=?", (data.get('contractor_id'), ticket_id))
    conn.commit(); conn.close(); return jsonify({'status': 'ok'})


@app.route('/api/tickets/<int:ticket_id>/update', methods=['PATCH'])
def update_ticket(ticket_id):
    data = request.get_json() or {}
    status = data.get('status')
    after_b64 = ensure_data_url(data.get('after_image_b64', ''))
    completed_at = datetime.now().isoformat() if status == 'completed' else None
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE tickets SET status=?, actual_cost=?, notes=?, after_image_b64=?, completed_at=? WHERE id=?",
              (status, data.get('actual_cost'), data.get('notes'), after_b64, completed_at, ticket_id))
    conn.commit(); conn.close(); return jsonify({'status': 'ok'})


@app.route('/api/repairs/<int:ticket_id>/start', methods=['POST'])
def start_repair(ticket_id):
    data = request.get_json() or {}
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    user = verify_token(token)
    if not user or user.get('role') != 'contractor':
        return jsonify({'error': 'Only contractors can start repairs'}), 403
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO repairs (ticket_id, contractor_id, status, started_at, before_image_b64) VALUES (?, ?, ?, ?, ?)",
              (ticket_id, user['id'], 'in_progress', datetime.now().isoformat(), ensure_data_url(data.get('before_image_b64', ''))))
    conn.commit(); repair_id = c.lastrowid
    c.execute("UPDATE tickets SET status='in_progress' WHERE id=?", (ticket_id,))
    c.execute("UPDATE detections SET status='in_progress' WHERE id=(SELECT detection_id FROM tickets WHERE id=?)", (ticket_id,))
    conn.commit(); conn.close(); return jsonify({'status': 'ok', 'repair_id': repair_id}), 201


@app.route('/api/repairs/<int:repair_id>/complete', methods=['POST'])
def complete_repair(repair_id):
    data = request.get_json() or {}
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    user = verify_token(token)
    if not user or user.get('role') != 'contractor':
        return jsonify({'error': 'Only contractors can complete repairs'}), 403
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE repairs SET status='completed', completed_at=?, after_image_b64=?, notes=? WHERE id=?",
              (datetime.now().isoformat(), ensure_data_url(data.get('after_image_b64', '')), data.get('notes', ''), repair_id))
    c.execute("SELECT ticket_id FROM repairs WHERE id=?", (repair_id,))
    row = c.fetchone();
    if row:
        ticket_id = row[0]
        c.execute("UPDATE tickets SET status='awaiting_verification' WHERE id=?", (ticket_id,))
        c.execute("UPDATE detections SET status='awaiting_verification' WHERE id=(SELECT detection_id FROM tickets WHERE id=?)", (ticket_id,))
    conn.commit(); conn.close(); return jsonify({'status': 'ok'}), 200


@app.route('/api/repairs/<int:repair_id>/verify', methods=['POST'])
@require_auth(roles=['admin'])
def verify_repair(repair_id):
    data = request.get_json() or {}
    verified = data.get('verified', False)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if verified:
        c.execute("UPDATE repairs SET status='verified' WHERE id=?", (repair_id,))
        c.execute("SELECT ticket_id FROM repairs WHERE id=?", (repair_id,))
        row = c.fetchone();
        if row:
            ticket_id = row[0]
            c.execute("UPDATE tickets SET status='resolved' WHERE id=?", (ticket_id,))
            c.execute("UPDATE detections SET status='resolved' WHERE id=(SELECT detection_id FROM tickets WHERE id=?)", (ticket_id,))
    else:
        c.execute("UPDATE repairs SET status='rejected' WHERE id=?", (repair_id,))
        c.execute("SELECT ticket_id FROM repairs WHERE id=?", (repair_id,))
        row = c.fetchone()
        if row:
            ticket_id = row[0]
            c.execute("UPDATE tickets SET status='reopened' WHERE id=?", (ticket_id,))
            c.execute("UPDATE detections SET status='reopened' WHERE id=(SELECT detection_id FROM tickets WHERE id=?)", (ticket_id,))
    conn.commit(); conn.close(); return jsonify({'status': 'ok'})


@app.route('/api/public/report', methods=['POST'])
def public_report():
    data = request.get_json() or {}
    data['device_id'] = 'public-web'
    depth = data.get('depth_mm', 40) or 40
    depth = max(1, min(int(depth), 500))
    data['depth_mm'] = depth
    data.setdefault('severity', min(100, depth * 2))
    return full_event()


# LIVE TELEMETRY
@app.route('/api/telemetry', methods=['POST'])
def api_telemetry():
    data = request.get_json(silent=True) or {}
    snapshot = {
        'device_id': data.get('device_id'),
        'timestamp': data.get('timestamp') or datetime.utcnow().isoformat(),
        'image_b64': ensure_data_url(data.get('image_b64', '')),
        'lat': data.get('lat'),
        'lon': data.get('lon'),
        'depth_cm': data.get('depth_cm', 0.0),
        'accel_x': data.get('accel_x', 0.0),
        'accel_y': data.get('accel_y', 0.0),
        'accel_z': data.get('accel_z', 0.0),
        'fps': data.get('fps', 0.0),
        'detections': data.get('detections'),
    }
    with _telemetry_lock:
        _latest_telemetry.update(snapshot)
        _telemetry_history.append({
            'timestamp': snapshot['timestamp'],
            'depth_cm': snapshot['depth_cm'],
            'accel_z': snapshot['accel_z'],
            'severity': (snapshot['detections'] or {}).get('top_severity'),
        })
        if len(_telemetry_history) > TELEMETRY_HISTORY_MAX:
            _telemetry_history.pop(0)
    if TELEMETRY_PERSIST:
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("INSERT INTO telemetry (device_id, timestamp, depth_cm, accel_x, accel_y, accel_z, fps, lat, lon, detections) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (snapshot['device_id'], snapshot['timestamp'], snapshot['depth_cm'], snapshot['accel_x'], snapshot['accel_y'], snapshot['accel_z'], snapshot['fps'], snapshot['lat'], snapshot['lon'], json.dumps(snapshot['detections'])))
            conn.commit(); conn.close()
        except Exception as e:
            print(f"[TELE] persist error: {e}")
    return jsonify({'status': 'ok'}), 200


@app.route('/api/live', methods=['GET'])
def api_live():
    with _telemetry_lock:
        return jsonify(dict(_latest_telemetry)), 200


@app.route('/api/live/status', methods=['GET'])
def api_live_status():
    with _telemetry_lock:
        ts = _latest_telemetry.get('timestamp')
        dev = _latest_telemetry.get('device_id')
    alive = False; age_s = None
    if ts:
        try:
            t = datetime.fromisoformat(ts.replace('Z', ''))
            age_s = (datetime.utcnow() - t).total_seconds(); alive = age_s < 30
        except Exception:
            pass
    return jsonify({'alive': alive, 'last_seen': ts, 'device_id': dev, 'age_seconds': age_s}), 200


@app.route('/api/live/history', methods=['GET'])
def api_live_history():
    with _telemetry_lock:
        return jsonify(list(_telemetry_history)), 200


@app.route('/api/live/latest_frame', methods=['GET'])
def api_live_latest_frame():
    from flask import Response
    with _telemetry_lock:
        data_url = _latest_telemetry.get('image_b64', '')
    if not data_url or ',' not in data_url:
        return ('', 204)
    try:
        b64 = data_url.split(',', 1)[1]
        raw = base64.b64decode(b64)
    except Exception:
        return ('', 500)
    resp = Response(raw, mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp


init_db()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

