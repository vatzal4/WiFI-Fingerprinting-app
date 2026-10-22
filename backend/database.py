"""
Database layer for the WiFi fingerprint survey tool.
Supports MongoDB Atlas (Cloud) as primary database, with automatic
spatial & radio similarity redundancy filtering to prevent duplicate data,
plus multi-device contribution tracking.
"""

import os
import math
from datetime import datetime, timezone
import pymongo
from pymongo import MongoClient

# MongoDB Atlas URI (provided cluster)
DEFAULT_MONGO_URI = (
    "mongodb+srv://24bt04d238_db_user:BuHPFJoodpXiiWwu@wifi-fingerprint-cluste.9vfenvq.mongodb.net/wifi_fingerprints?appName=Wifi-Fingerprint-Cluster"
)
MONGO_URI = os.environ.get("MONGODB_URI", DEFAULT_MONGO_URI)
DB_NAME = os.environ.get("MONGODB_DB_NAME", "wifi_fingerprints")

# Spatial duplicate threshold in SVG units
SPATIAL_THRESHOLD = float(os.environ.get("SPATIAL_THRESHOLD", 20.0))
# Radio similarity threshold (0.0 to 1.0)
RADIO_THRESHOLD = float(os.environ.get("RADIO_THRESHOLD", 0.75))

_client = None
_db = None


class DuplicateFingerprintError(Exception):
    """Raised when attempting to insert a fingerprint that is same or similar to an existing one."""
    def __init__(self, message, matched_id=None, distance=None, similarity=None):
        super().__init__(message)
        self.matched_id = matched_id
        self.distance = distance
        self.similarity = similarity


def get_mongo_db():
    global _client, _db
    if _db is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
        _db = _client[DB_NAME]
    return _db


def calculate_spatial_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)


def calculate_radio_similarity(readings_a: list, readings_b: list) -> tuple:
    """
    Computes (jaccard_overlap, rssi_similarity, blended_similarity) between two scans.
    """
    if not readings_a and not readings_b:
        return 1.0, 1.0, 1.0
    if not readings_a or not readings_b:
        return 0.0, 0.0, 0.0

    map_a = {r["bssid"].lower(): r["rssi"] for r in readings_a if "bssid" in r and "rssi" in r}
    map_b = {r["bssid"].lower(): r["rssi"] for r in readings_b if "bssid" in r and "rssi" in r}

    keys_a = set(map_a.keys())
    keys_b = set(map_b.keys())

    union = keys_a.union(keys_b)
    intersection = keys_a.intersection(keys_b)

    if not union:
        return 0.0, 0.0, 0.0

    jaccard = len(intersection) / len(union)

    if not intersection:
        return 0.0, 0.0, 0.0

    total_diff = sum(abs(map_a[b] - map_b[b]) for b in intersection)
    mae = total_diff / len(intersection)
    rssi_sim = max(0.0, 1.0 - (mae / 25.0))

    blended = 0.45 * jaccard + 0.55 * rssi_sim
    return jaccard, rssi_sim, blended


def init_db():
    """Initializes MongoDB Atlas collections, indexes, and default building data."""
    try:
        db = get_mongo_db()
        fingerprints_col = db["fingerprints"]

        # Compound index for fast spatial lookup per building & floor
        fingerprints_col.create_index([
            ("building_name", pymongo.ASCENDING),
            ("floor_id", pymongo.ASCENDING),
            ("x", pymongo.ASCENDING),
            ("y", pymongo.ASCENDING),
        ])
        fingerprints_col.create_index([("captured_at", pymongo.DESCENDING)])
        fingerprints_col.create_index([("device_info.device_id", pymongo.ASCENDING)])

        seed_default_buildings_and_floors()
        print("Connected to MongoDB Atlas and initialized collections & indexes.")
    except Exception as e:
        print(f"MongoDB initialization notice: {e}")


def seed_default_buildings_and_floors():
    """Seed campus building and floor metadata into MongoDB Atlas."""
    db = get_mongo_db()
    buildings_col = db["buildings"]
    floors_col = db["floors"]

    # 1. SOT
    sot_id = get_or_create_building("SOT")
    get_or_create_floor(sot_id, 0, "Floor 0 -- Ground floor", "SOT")
    get_or_create_floor(sot_id, 1, "Floor 1 -- First floor", "SOT")
    get_or_create_floor(sot_id, 2, "Floor 2 -- Second floor", "SOT")

    # 2. SOS
    sos_id = get_or_create_building("SOS")
    get_or_create_floor(sos_id, 0, "Floor 0 -- Ground floor", "SOS")
    get_or_create_floor(sos_id, 1, "Floor 1 -- First floor", "SOS")
    get_or_create_floor(sos_id, 2, "Floor 2 -- Second floor", "SOS")

    # 3. Anviksha
    get_or_create_building("Anviksha")


def get_or_create_building(name: str) -> int:
    db = get_mongo_db()
    col = db["buildings"]
    existing = col.find_one({"name": {"$regex": f"^{name}$", "$options": "i"}})
    if existing:
        return existing.get("id", 1)

    max_doc = col.find_one(sort=[("id", -1)])
    next_id = (max_doc.get("id", 0) + 1) if max_doc else 1
    col.insert_one({"id": next_id, "name": name, "created_at": datetime.now(timezone.utc).isoformat()})
    return next_id


def get_or_create_floor(building_id: int, floor_number: int, label: str = None, building_name: str = None) -> int:
    db = get_mongo_db()
    col = db["floors"]
    existing = col.find_one({"building_id": building_id, "floor_number": floor_number})
    if existing:
        return existing.get("id", 1)

    max_doc = col.find_one(sort=[("id", -1)])
    next_id = (max_doc.get("id", 0) + 1) if max_doc else 1
    col.insert_one({
        "id": next_id,
        "building_id": building_id,
        "building_name": building_name,
        "floor_number": floor_number,
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return next_id


def insert_fingerprint(
    floor_id: int,
    x: float,
    y: float,
    readings: list,
    node_id: str = None,
    building_name: str = None,
    floor_label: str = None,
    device_info: dict = None,
    merge_on_redundancy: bool = False,
) -> str:
    """
    Inserts a new fingerprint into MongoDB Atlas.
    Filters out same/similar fingerprints based on spatial proximity
    and radio signature similarity.
    """
    db = get_mongo_db()
    col = db["fingerprints"]

    # Query existing points on this floor
    query = {"floor_id": floor_id}
    if building_name:
        query["building_name"] = building_name
    existing_points = list(col.find(query))

    # Evaluate redundancy against existing points
    for ep in existing_points:
        dist = calculate_spatial_distance(x, y, ep["x"], ep["y"])
        jaccard, rssi_sim, blended = calculate_radio_similarity(readings, ep.get("readings", []))

        is_duplicate = False
        reason = ""

        if dist <= SPATIAL_THRESHOLD:
            is_duplicate = True
            reason = f"Exact or close spatial duplicate (distance={dist:.1f}px <= {SPATIAL_THRESHOLD:.0f}px, signal match {blended*100:.0f}%)"
        elif dist <= SPATIAL_THRESHOLD * 2.0 and blended >= RADIO_THRESHOLD:
            is_duplicate = True
            reason = f"High radio similarity match ({blended*100:.0f}% >= {RADIO_THRESHOLD*100:.0f}%) within distance {dist:.1f}px"

        if is_duplicate:
            if merge_on_redundancy:
                # Merge readings to refine the existing point
                current_n = ep.get("sample_count", 1)
                existing_map = {r["bssid"].lower(): r for r in ep.get("readings", [])}
                new_map = {r["bssid"].lower(): r for r in readings}

                merged_readings = []
                for bssid in set(existing_map.keys()).union(new_map.keys()):
                    if bssid in existing_map and bssid in new_map:
                        avg_rssi = round((existing_map[bssid]["rssi"] * current_n + new_map[bssid]["rssi"]) / (current_n + 1))
                        merged_readings.append({
                            "bssid": existing_map[bssid]["bssid"],
                            "ssid": existing_map[bssid].get("ssid") or new_map[bssid].get("ssid"),
                            "rssi": avg_rssi,
                        })
                    elif bssid in existing_map:
                        merged_readings.append(existing_map[bssid])
                    else:
                        merged_readings.append(new_map[bssid])

                contributors = ep.get("contributors", [])
                if device_info:
                    contributors.append({
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                        "device": device_info,
                    })

                col.update_one(
                    {"_id": ep["_id"]},
                    {
                        "$set": {
                            "readings": merged_readings,
                            "contributors": contributors,
                            "last_updated_at": datetime.now(timezone.utc).isoformat(),
                        },
                        "$inc": {"sample_count": 1},
                    },
                )
                return str(ep["_id"])
            else:
                raise DuplicateFingerprintError(
                    f"Redundant fingerprint filtered: {reason}. Existing point at ({ep['x']:.1f}, {ep['y']:.1f})",
                    matched_id=str(ep["_id"]),
                    distance=dist,
                    similarity=blended,
                )

    # Clean unique fingerprint -> insert into MongoDB Atlas
    now_iso = datetime.now(timezone.utc).isoformat()
    doc = {
        "floor_id": floor_id,
        "building_name": building_name,
        "floor_label": floor_label,
        "x": float(x),
        "y": float(y),
        "node_id": node_id,
        "captured_at": now_iso,
        "device_info": device_info or {"device_name": "API Client"},
        "sample_count": 1,
        "contributors": [
            {
                "captured_at": now_iso,
                "device": device_info or {"device_name": "API Client"},
                "ap_count": len(readings),
            }
        ],
        "readings": [
            {
                "bssid": r["bssid"],
                "ssid": r.get("ssid", ""),
                "rssi": int(r["rssi"]),
            }
            for r in readings
        ],
    }

    result = col.insert_one(doc)
    return str(result.inserted_id)


def list_fingerprints(floor_id: int = None, building_id: int = None, building_name: str = None) -> list:
    """Lists fingerprints from MongoDB Atlas."""
    db = get_mongo_db()
    col = db["fingerprints"]

    query = {}
    if floor_id is not None:
        query["floor_id"] = floor_id
    if building_name:
        query["building_name"] = {"$regex": f"^{building_name}$", "$options": "i"}

    docs = list(col.find(query).sort("captured_at", pymongo.ASCENDING))
    results = []
    for d in docs:
        d["id"] = str(d.pop("_id"))
        results.append(d)
    return results


def list_buildings() -> list:
    db = get_mongo_db()
    b_col = db["buildings"]
    f_col = db["floors"]

    buildings = list(b_col.find().sort("id", pymongo.ASCENDING))
    result = []
    for b in buildings:
        floor_count = f_col.count_documents({"building_id": b.get("id")})
        result.append({
            "id": b.get("id"),
            "name": b.get("name"),
            "floor_count": floor_count,
        })
    return result


def list_floors(building_id: int = None, building_name: str = None) -> list:
    db = get_mongo_db()
    col = db["floors"]

    query = {}
    if building_id is not None:
        query["building_id"] = building_id
    if building_name:
        query["building_name"] = {"$regex": f"^{building_name}$", "$options": "i"}

    floors = list(col.find(query).sort("floor_number", pymongo.ASCENDING))
    for f in floors:
        f.pop("_id", None)
    return floors


def deduplicate_fingerprints() -> int:
    """
    Scans MongoDB Atlas for existing duplicate or redundant points
    and removes or fuses them.
    """
    db = get_mongo_db()
    col = db["fingerprints"]
    all_points = list(col.find().sort("captured_at", pymongo.ASCENDING))

    removed_count = 0
    to_delete_ids = set()

    for i in range(len(all_points)):
        p1 = all_points[i]
        if p1["_id"] in to_delete_ids:
            continue
        for j in range(i + 1, len(all_points)):
            p2 = all_points[j]
            if p2["_id"] in to_delete_ids:
                continue

            # Compare if on same floor
            if p1.get("floor_id") == p2.get("floor_id") and p1.get("building_name") == p2.get("building_name"):
                dist = calculate_spatial_distance(p1["x"], p1["y"], p2["x"], p2["y"])
                _, _, blended = calculate_radio_similarity(p1.get("readings", []), p2.get("readings", []))

                if dist <= SPATIAL_THRESHOLD or (dist <= SPATIAL_THRESHOLD * 2.0 and blended >= RADIO_THRESHOLD):
                    # Flag p1 (older scan) for removal, keep p2 (newer scan)
                    to_delete_ids.add(p1["_id"])
                    break

    if to_delete_ids:
        col.delete_many({"_id": {"$in": list(to_delete_ids)}})
        removed_count = len(to_delete_ids)

    return removed_count


def migrate_sqlite_to_mongodb():
    """Migrates any existing points in fingerprints.db (SQLite) to MongoDB Atlas."""
    import sqlite3

    sqlite_path = os.path.join(os.path.dirname(__file__), "fingerprints.db")
    if not os.path.exists(sqlite_path):
        print(f"No SQLite file found at {sqlite_path}")
        return 0

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT * FROM fingerprints").fetchall()
    migrated = 0
    skipped = 0

    for r in rows:
        readings_rows = conn.execute(
            "SELECT bssid, ssid, rssi FROM readings WHERE fingerprint_id = ?", (r["id"],)
        ).fetchall()
        readings = [dict(rd) for rd in readings_rows]

        # Determine building name
        floor = conn.execute("SELECT building_id, floor_number FROM floors WHERE id = ?", (r["floor_id"],)).fetchone()
        building_name = "SOT"
        if floor:
            b = conn.execute("SELECT name FROM buildings WHERE id = ?", (floor["building_id"],)).fetchone()
            if b:
                building_name = b["name"]

        try:
            insert_fingerprint(
                floor_id=r["floor_id"],
                x=r["x"],
                y=r["y"],
                readings=readings,
                node_id=r["node_id"],
                building_name=building_name,
                device_info={"device_name": "SQLite Migrated Data"},
            )
            migrated += 1
        except DuplicateFingerprintError:
            skipped += 1

    conn.close()
    print(f"Migration finished: {migrated} points migrated to MongoDB Atlas, {skipped} redundant points filtered.")
    return migrated
