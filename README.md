# WiFi Fingerprint Survey Tool

Captures WiFi fingerprints (an x/y/floor point + the signal strength of every
visible access point there) and stores them in **MongoDB Atlas**, matching Step 3
of the indoor wayfinding plan.

Supports **multi-device crowdsourced surveying**: compile the Flutter APK, share it with teammates, and all devices upload directly to MongoDB Atlas while smart spatial and radio redundancy filtering eliminates duplicates in real time.

```
flutter_survey/  Flutter Android app (primary). Connects directly to MongoDB Atlas.
                 Renders interactive SVG floor plans, tracks multiple surveying devices,
                 displays existing surveyed markers live on the map, and filters
                 out redundant/duplicate points.
backend/         Flask API + MongoDB Atlas database layer. Also includes inspect/export
                 CLI tools (view_db.py) and CSV dataset exports for KNN model training.
android/         Minimal native Android snippet reference.
```

## 1. MongoDB Atlas Configuration

Database connection URI:
```
mongodb+srv://24bt04d238_db_user:BuHPFJoodpXiiWwu@wifi-fingerprint-cluste.9vfenvq.mongodb.net/wifi_fingerprints?appName=Wifi-Fingerprint-Cluster
```

The database collections are:
- `fingerprints`: Stores surveyed points with device metadata, sample count, and AP readings.
- `buildings`: Campus buildings (`SOT`, `SOS`, `Anviksha`).
- `floors`: Floors and SVG viewbox configurations.

## 2. Redundancy & Duplicate Filtering

When multiple users survey points across campus:
1. **Spatial Proximity**: Points within Euclidean radius $R \le 20$ px are detected as physical duplicates.
2. **Radio Signature Similarity**: Calculates Jaccard BSSID overlap and RSSI Mean Absolute Error (MAE) across scans.
3. **Filter Policies**:
   - **Reject Duplicates (Strict)**: Skips duplicate insertion and informs surveyor of distance & match percentage.
   - **Average & Refine (Multi-Device)**: Merges multiple device scans at the same spot, averaging RSSI values to reduce device bias and noise.
   - **Overwrite**: Updates existing point with fresh scan.

## 3. Building and Sharing the APK

To share the app with multiple surveyors:

```bash
cd flutter_survey
flutter build apk --release
```

The APK will be generated at:
`flutter_survey/build/app/outputs/flutter-apk/app-release.apk`

Share this APK with surveyors. Each surveyor can set their own name/alias in the app settings, view existing surveyed points live on the map, and capture points without needing a local laptop server.

## 4. Running the Backend & Data Tools

```bash
cd backend
pip install -r requirements.txt
python app.py
```

Inspect fingerprints in MongoDB Atlas and view device contributions:
```bash
python view_db.py --details
```

Export dataset to CSV:
```bash
python view_db.py --export-csv fingerprints_export.csv
```
