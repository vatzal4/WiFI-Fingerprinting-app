import 'dart:io';
import 'package:device_info_plus/device_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';

/// Metadata identifying the surveying device and surveyor.
class DeviceInfoData {
  final String deviceId;
  final String deviceName;
  final String model;
  final String platform;
  final String osVersion;

  const DeviceInfoData({
    required this.deviceId,
    required this.deviceName,
    required this.model,
    required this.platform,
    required this.osVersion,
  });

  Map<String, dynamic> toMap() => {
        'device_id': deviceId,
        'device_name': deviceName,
        'model': model,
        'platform': platform,
        'os_version': osVersion,
      };

  factory DeviceInfoData.fromMap(Map<String, dynamic> map) {
    return DeviceInfoData(
      deviceId: map['device_id'] as String? ?? 'unknown',
      deviceName: map['device_name'] as String? ?? 'Anonymous Device',
      model: map['model'] as String? ?? 'Unknown Model',
      platform: map['platform'] as String? ?? 'Unknown OS',
      osVersion: map['os_version'] as String? ?? '',
    );
  }
}

/// Manages device identification, persistent UUID, and surveyor alias.
class DeviceService {
  static const String _prefKeyDeviceId = 'survey_device_id';
  static const String _prefKeySurveyorName = 'survey_surveyor_name';

  static DeviceInfoData? _cachedInfo;

  /// Loads or generates the device info and surveyor alias.
  static Future<DeviceInfoData> getDeviceInfo() async {
    if (_cachedInfo != null) return _cachedInfo!;

    final prefs = await SharedPreferences.getInstance();
    String? deviceId = prefs.getString(_prefKeyDeviceId);
    if (deviceId == null || deviceId.isEmpty) {
      deviceId = const Uuid().v4();
      await prefs.setString(_prefKeyDeviceId, deviceId);
    }

    String? customName = prefs.getString(_prefKeySurveyorName);

    String model = 'Unknown';
    String platform = Platform.operatingSystem;
    String osVersion = '';

    final deviceInfoPlugin = DeviceInfoPlugin();

    try {
      if (Platform.isAndroid) {
        final androidInfo = await deviceInfoPlugin.androidInfo;
        model = '${androidInfo.manufacturer} ${androidInfo.model}';
        osVersion = 'Android ${androidInfo.version.release} (SDK ${androidInfo.version.sdkInt})';
        if (customName == null || customName.isEmpty) {
          customName = androidInfo.model;
        }
      } else if (Platform.isIOS) {
        final iosInfo = await deviceInfoPlugin.iosInfo;
        model = iosInfo.utsname.machine;
        osVersion = 'iOS ${iosInfo.systemVersion}';
        if (customName == null || customName.isEmpty) {
          customName = iosInfo.name;
        }
      } else {
        model = Platform.operatingSystem;
        osVersion = Platform.operatingSystemVersion;
        if (customName == null || customName.isEmpty) {
          customName = 'Surveyor-${deviceId.substring(0, 6)}';
        }
      }
    } catch (_) {
      customName ??= 'Surveyor-${deviceId.substring(0, 6)}';
    }

    _cachedInfo = DeviceInfoData(
      deviceId: deviceId,
      deviceName: customName,
      model: model,
      platform: platform,
      osVersion: osVersion,
    );

    return _cachedInfo!;
  }

  /// Sets a friendly name/alias for this surveyor device.
  static Future<void> setSurveyorName(String name) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefKeySurveyorName, name.trim());
    _cachedInfo = null; // Invalidate cache so it reloads with the new name
  }

  /// Gets the current surveyor alias.
  static Future<String> getSurveyorName() async {
    final info = await getDeviceInfo();
    return info.deviceName;
  }
}
