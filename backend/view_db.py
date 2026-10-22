"""
Utility script to quickly inspect WiFi fingerprints stored in MongoDB Atlas.

Usage:
    python view_db.py
    python view_db.py --building SOT
    python view_db.py --details
    python view_db.py --export-csv
    python view_db.py --migrate
"""

import sys
import os
import csv
import argparse
import database as db


def export_csv(fps, output_path="fingerprints_export.csv"):
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "fingerprint_id",
            "building_name",
            "floor_id",
            "x",
            "y",
            "device_name",
            "sample_count",
            "captured_at",
            "bssid",
            "ssid",
            "rssi",
        ])
        for fp in fps:
            dev_name = fp.get("device_info", {}).get("device_name", "Unknown")
            for r in fp.get("readings", []):
                writer.writerow([
                    fp["id"],
                    fp.get("building_name", ""),
                    fp.get("floor_id", ""),
                    fp.get("x", 0.0),
                    fp.get("y", 0.0),
                    dev_name,
                    fp.get("sample_count", 1),
                    fp.get("captured_at", ""),
                    r.get("bssid", ""),
                    r.get("ssid", ""),
                    r.get("rssi", -100),
                ])
    print(f"\n[+] Exported {len(fps)} fingerprints to {os.path.abspath(output_path)}")


def main():
    parser = argparse.ArgumentParser(description="View WiFi Fingerprints Database (MongoDB Atlas)")
    parser.add_argument("--building", "-b", help="Filter by building name (e.g., SOT, SOS)")
    parser.add_argument("--details", "-d", action="store_true", help="Show all AP readings and device info")
    parser.add_argument("--export-csv", "-e", nargs="?", const="fingerprints_export.csv", help="Export to CSV file")
    parser.add_argument("--migrate", "-m", action="store_true", help="Migrate SQLite data to MongoDB Atlas")
    args = parser.parse_args()

    db.init_db()

    if args.migrate:
        print("[*] Starting SQLite to MongoDB Atlas migration...")
        migrated = db.migrate_sqlite_to_mongodb()
        print(f"[+] Migration complete! {migrated} records processed.")

    buildings = db.list_buildings()
    fps = db.list_fingerprints(building_name=args.building)

    print("=" * 80)
    print("       WIFI FINGERPRINT DATABASE (MONGODB ATLAS CLUSTER)")
    print("=" * 80)

    print("\n[Buildings & Floors]")
    for b in buildings:
        floors = db.list_floors(building_id=b["id"])
        print(f"  * {b['name']} (ID: {b['id']}) - {len(floors)} floor(s)")
        for fl in floors:
            b_fps = [f for f in fps if f.get("building_name") == b["name"] and f.get("floor_id") == fl["id"]]
            print(f"      - Floor {fl['floor_number']}: {fl.get('label') or 'Floor'} (Points: {len(b_fps)})")

    print(f"\n[Total Fingerprints in Cloud: {len(fps)}]")
    if not fps:
        print("  No fingerprints recorded yet.")
        print("=" * 80)
        return

    # Count distinct devices
    devices = set(f.get("device_info", {}).get("device_id") for f in fps if f.get("device_info"))
    print(f"[Distinct Devices: {len(devices)}]")

    print("-" * 80)
    print(f"{'ID':<25} {'Building':<10} {'Floor':<7} {'(X, Y)':<16} {'APs':<5} {'Samples':<8} {'Device'}")
    print("-" * 80)

    for fp in fps:
        fid = fp["id"][:24]
        xy = f"({fp['x']:.1f}, {fp['y']:.1f})"
        dev = fp.get("device_info", {}).get("device_name", "Unknown")[:15]
        samples = f"{fp.get('sample_count', 1)}x"
        print(f"{fid:<25} {fp.get('building_name', 'N/A'):<10} {fp.get('floor_id', 'N/A'):<7} {xy:<16} {len(fp.get('readings', [])):<5} {samples:<8} {dev}")

        if args.details:
            print("      Top Access Points:")
            sorted_readings = sorted(fp.get("readings", []), key=lambda r: r["rssi"], reverse=True)
            for r in sorted_readings[:5]:
                ssid = r["ssid"] if r.get("ssid") else "(hidden)"
                print(f"        {r['bssid']} | {r['rssi']:>4} dBm | {ssid}")
            if len(sorted_readings) > 5:
                print(f"        ... and {len(sorted_readings) - 5} more APs")
            print()

    print("=" * 80)

    if args.export_csv:
        export_csv(fps, args.export_csv)


if __name__ == "__main__":
    main()
