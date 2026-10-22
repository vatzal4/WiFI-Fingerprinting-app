"""
WiFi Fingerprint Survey API with MongoDB Atlas cloud support.

Run with:  python app.py
Accessible from Flutter app, Android native, or curl.
"""

import time
from flask import Flask, request, jsonify
import database as db
from locator import clean_readings, locate, load_fingerprints, SSID, K, MISSING_RSSI

app = Flask(__name__)
db.init_db()

# In-memory cache for loaded fingerprints per building (60s TTL)
_fingerprints_cache = {}
CACHE_TTL_SECONDS = 60.0


def clear_fingerprints_cache():
    """Helper to clear the in-memory cache (used for testing or forced refresh)."""
    _fingerprints_cache.clear()


@app.route("/api/buildings", methods=["GET"])
def get_buildings():
    return jsonify(db.list_buildings())


@app.route("/api/buildings", methods=["POST"])
def create_building():
    data = request.get_json(force=True)
    name = data.get("name")
    if not name:
        return jsonify({"error": "name is required"}), 400
    building_id = db.get_or_create_building(name)
    return jsonify({"id": building_id, "name": name}), 201


@app.route("/api/floors", methods=["POST"])
def create_floor():
    data = request.get_json(force=True)
    building_name = data.get("building_name")
    floor_number = data.get("floor_number")
    label = data.get("label")
    if building_name is None or floor_number is None:
        return jsonify({"error": "building_name and floor_number are required"}), 400
    building_id = db.get_or_create_building(building_name)
    floor_id = db.get_or_create_floor(building_id, floor_number, label, building_name)
    return jsonify({
        "id": floor_id,
        "building_id": building_id,
        "floor_number": floor_number,
        "label": label,
    }), 201


@app.route("/api/floors", methods=["GET"])
def get_floors():
    building_id = request.args.get("building_id", type=int)
    building_name = request.args.get("building_name")
    return jsonify(db.list_floors(building_id=building_id, building_name=building_name))


@app.route("/api/fingerprints", methods=["POST"])
def create_fingerprint():
    """
    Expected body:
    {
      "floor_id": 1,
      "building_name": "SOT",
      "floor_number": 0,
      "floor_label": "Floor 0 -- Ground floor",
      "x": 10.0,
      "y": 20.0,
      "node_id": "node-hall-1",
      "device_info": {
        "device_id": "...",
        "device_name": "Pixel 7 / Alice",
        "model": "Google Pixel 7"
      },
      "readings": [
        {"bssid": "aa:bb:cc:00:11:22", "ssid": "Campus-WiFi", "rssi": -52},
        {"bssid": "aa:bb:cc:00:11:23", "ssid": "Campus-WiFi", "rssi": -67}
      ]
    }
    """
    data = request.get_json(force=True)
    floor_id = data.get("floor_id")
    building_name = data.get("building_name")
    floor_number = data.get("floor_number")
    floor_label = data.get("floor_label")
    device_info = data.get("device_info")

    if floor_id is None and building_name and floor_number is not None:
        b_id = db.get_or_create_building(building_name)
        floor_id = db.get_or_create_floor(b_id, floor_number, building_name=building_name)

    x = data.get("x")
    y = data.get("y")
    readings = data.get("readings")
    node_id = data.get("node_id")

    if floor_id is None or x is None or y is None:
        return jsonify({"error": "floor_id (or building_name + floor_number), x, and y are required"}), 400
    if not readings or not isinstance(readings, list):
        return jsonify({"error": "readings must be a non-empty list"}), 400
    for r in readings:
        if "bssid" not in r or "rssi" not in r:
            return jsonify({"error": "each reading needs bssid and rssi"}), 400

    try:
        fingerprint_id = db.insert_fingerprint(
            floor_id=floor_id,
            x=x,
            y=y,
            readings=readings,
            node_id=node_id,
            building_name=building_name,
            floor_label=floor_label,
            device_info=device_info,
        )
        return jsonify({
            "id": fingerprint_id,
            "floor_id": floor_id,
            "stored_readings": len(readings),
            "status": "stored",
        }), 201
    except db.DuplicateFingerprintError as e:
        return jsonify({
            "error": "A same or similar fingerprint already exists for this location.",
            "duplicate": True,
            "floor_id": floor_id,
            "x": x,
            "y": y,
            "matched_id": e.matched_id,
            "distance": e.distance,
            "similarity": e.similarity,
            "message": str(e),
        }), 409


@app.route("/api/fingerprints", methods=["GET"])
def get_fingerprints():
    floor_id = request.args.get("floor_id", type=int)
    building_id = request.args.get("building_id", type=int)
    building_name = request.args.get("building_name")
    return jsonify(db.list_fingerprints(floor_id=floor_id, building_id=building_id, building_name=building_name))


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Returns database summary statistics across all surveyed devices."""
    mongo_db = db.get_mongo_db()
    col = mongo_db["fingerprints"]
    total = col.count_documents({})
    distinct_devices = len(col.distinct("device_info.device_id"))
    return jsonify({
        "status": "ok",
        "database": "MongoDB Atlas",
        "total_fingerprints": total,
        "unique_devices": distinct_devices,
    })


@app.route("/api/migrate", methods=["POST"])
def migrate_sqlite():
    """Migrates any existing points in fingerprints.db (SQLite) to MongoDB Atlas."""
    count = db.migrate_sqlite_to_mongodb()
    return jsonify({"migrated_count": count})


@app.route("/api/locate", methods=["POST"])
def locate_user():
    """
    Estimates user location using WiFi fingerprint k-NN matching.

    Request body:
        {
            "building": "SOS",
            "readings": [
                {"bssid": "...", "ssid": "...", "rssi": -60},
                ...
            ]
        }
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "missing or malformed body"}), 400

    building = data.get("building")
    readings = data.get("readings")

    if not building or not isinstance(building, str) or readings is None or not isinstance(readings, list):
        return jsonify({"error": "missing or malformed body: 'building' and 'readings' list required"}), 400

    # Clean the live scan readings
    cleaned_scan = clean_readings(readings, ssid=SSID, min_rssi=MISSING_RSSI)
    if not cleaned_scan:
        return jsonify({"error": "no Student access points in scan"}), 422

    # In-memory cache check / reload every 60 seconds
    now = time.time()
    cache_key = building.strip().lower()
    cached = _fingerprints_cache.get(cache_key)

    if cached is None or (now - cached["timestamp"]) >= CACHE_TTL_SECONDS:
        mongo_db = db.get_mongo_db()
        fingerprints = load_fingerprints(
            building_name=building,
            db=mongo_db,
            ssid=SSID,
            min_rssi=MISSING_RSSI,
        )
        _fingerprints_cache[cache_key] = {
            "timestamp": now,
            "fingerprints": fingerprints,
        }
    else:
        fingerprints = cached["fingerprints"]

    if not fingerprints:
        return jsonify({"error": f"unknown building or no fingerprints for '{building}'"}), 404

    # Run k-NN positioning
    result = locate(cleaned_scan, fingerprints, k=K)
    if not result:
        return jsonify({"error": "could not estimate position"}), 422

    return jsonify({
        "x": result["x"],
        "y": result["y"],
        "floor_id": result["floor_id"],
        "floor_number": result["floor_number"],
        "confidence": result["confidence"],
        "neighbors_used": len(result["neighbors"]),
    }), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "backend": "MongoDB Atlas"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
