# WiFi Fingerprint Survey -- Flutter

Flutter app for crowdsourced WiFi fingerprint surveying for indoor wayfinding.
Supports multi-device surveying across campus: share the compiled APK with multiple users, and each device uploads fingerprints directly to **MongoDB Atlas** with smart spatial and signal redundancy filtering.

---

## Key Features

1. **Direct MongoDB Atlas Cloud Storage**:
   - Stores all surveyed fingerprints in MongoDB Atlas cluster.
   - Built with `mongo_dart` so every surveyor device connects directly over Wi-Fi or cellular without requiring a local laptop server.
   - Live connection state indicator and cloud metrics.

2. **Multi-Device Crowd Surveying**:
   - Identifies each surveying device using persistent UUIDs and surveyor aliases (e.g. `Alice - Galaxy S22`).
   - Tracks which team member surveyed which points.
   - Team members can survey the same building simultaneously.

3. **Smart Redundancy & Duplicate Filtering**:
   - **Spatial Distance Check**: Detects if a new scan is too close to an existing surveyed point (within Euclidean radius $R \le 20$ px).
   - **Radio Signature Similarity**: Computes BSSID Jaccard overlap and RSSI Mean Absolute Error (MAE) between scans.
   - **Flexible Redundancy Policies**:
     - *Reject Duplicates (Strict)*: Skips duplicate insertion and informs the surveyor with the distance and match percentage.
     - *Average & Refine (Multi-Device)*: Fuses scans from multiple devices, averaging RSSI values to reduce device bias without database bloat.
     - *Overwrite*: Updates point with fresh scan.

4. **Live Map Visual Feedback**:
   - Displays all already-surveyed points as green markers on the interactive SVG floor plan.
   - Surveyors can see where teammates have already surveyed in real time.
   - Tap any surveyed point to inspect visible APs, signal strength, surveyor name, and sample count.

---

## MongoDB Atlas Connection

Preconfigured with:
```
mongodb+srv://24bt04d238_db_user:BuHPFJoodpXiiWwu@wifi-fingerprint-cluste.9vfenvq.mongodb.net/wifi_fingerprints?appName=Wifi-Fingerprint-Cluster
```
Database: `wifi_fingerprints`
Collection: `fingerprints`

---

## Building & Distributing the APK to Multiple Users

To share the app with multiple surveyors:

1. **Build the APK**:
   ```bash
   cd flutter_survey
   flutter build apk --release
   ```
   *(Or for faster debug testing: `flutter build apk --debug`)*

2. **Output Location**:
   The generated APK will be at:
   ```
   flutter_survey/build/app/outputs/flutter-apk/app-release.apk
   ```

3. **Install on Surveyors' Android Phones**:
   - Send `app-release.apk` via WhatsApp, Google Drive, or USB.
   - Install the APK on each Android phone (enable "Install unknown apps" when prompted).
   - Ensure Location permission is enabled (required by Android for scanning Wi-Fi).

4. **Surveying Walkthrough**:
   - Open app -> Tap the **Settings icon** (top right) -> Set a custom Surveyor Name (e.g., "Surveyor 1").
   - Select building (`SOT` or `SOS`).
   - Select floor (shows how many points have already been surveyed).
   - Tap on the SVG floor plan to place a pin.
   - Tap **"Capture WiFi fingerprint here"**.
   - The scan is uploaded to MongoDB Atlas immediately and turns into a green marker!

---

## Inspecting Data & Statistics

You can inspect the collected fingerprints directly from the terminal using the Python utility:

```bash
cd backend
python view_db.py --details
```

To export all collected fingerprints to CSV for training indoor positioning models (KNN / WKNN):
```bash
python view_db.py --export-csv fingerprints_export.csv
```
