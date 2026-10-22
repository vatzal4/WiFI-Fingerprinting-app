import 'dart:io' show Platform;
import 'package:permission_handler/permission_handler.dart';
import 'package:wifi_scan/wifi_scan.dart';
import '../models/fingerprint.dart';

/// Wraps the wifi_scan plugin. Real AP scanning only works on Android --
/// Apple blocks third-party apps from reading raw WiFi scan results, so
/// on iOS every method here returns an empty/false result instead of
/// throwing, letting the rest of the app degrade gracefully (see
/// SurveyScreen for the "iOS fallback" messaging this enables).
class WifiScannerService {
  bool get isScanSupportedPlatform => Platform.isAndroid;

  /// Requests ACCESS_FINE_LOCATION, which Android requires before it
  /// will return WiFi scan results. No-op (returns true) on iOS since
  /// scanning isn't available there regardless.
  Future<bool> requestPermission() async {
    if (!isScanSupportedPlatform) return true;
    final status = await Permission.location.request();
    return status.isGranted;
  }

  Future<bool> hasPermission() async {
    if (!isScanSupportedPlatform) return false;
    return await Permission.location.isGranted;
  }

  /// Asks Android to kick off a fresh scan. Android rate-limits how often
  /// an app can request a new scan (a handful of times per 2 minutes on
  /// recent OS versions) -- getScanResults() below still returns the most
  /// recent completed scan even if a new one was throttled.
  Future<bool> requestFreshScan() async {
    if (!isScanSupportedPlatform) return false;

    final can = await WiFiScan.instance.canStartScan();
    if (can != CanStartScan.yes) return false;

    return await WiFiScan.instance.startScan();
  }

  /// Returns the most recent scan as a list of AP readings, ready to
  /// attach to a Fingerprint. Empty list on iOS or if permission/scan
  /// is unavailable.
  Future<List<ApReading>> getCurrentReadings() async {
    if (!isScanSupportedPlatform) return [];

    final can = await WiFiScan.instance.canGetScannedResults();
    if (can != CanGetScannedResults.yes) return [];

    final results = await WiFiScan.instance.getScannedResults();

    return results
        .map((ap) => ApReading(
              bssid: ap.bssid,
              ssid: ap.ssid.isEmpty ? '(hidden)' : ap.ssid,
              rssi: ap.level,
            ))
        .toList();
  }
}
