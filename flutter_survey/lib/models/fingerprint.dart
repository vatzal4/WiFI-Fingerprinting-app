/// One access point seen during a scan.
class ApReading {
  final String bssid;
  final String ssid;
  final int rssi; // signal strength in dBm, e.g. -55 (closer to 0 = stronger)

  ApReading({
    required this.bssid,
    required this.ssid,
    required this.rssi,
  });

  Map<String, dynamic> toJson() => {
        'bssid': bssid,
        'ssid': ssid,
        'rssi': rssi,
      };

  factory ApReading.fromMap(Map<String, dynamic> map) {
    return ApReading(
      bssid: map['bssid'] as String? ?? '',
      ssid: map['ssid'] as String? ?? '',
      rssi: (map['rssi'] as num?)?.toInt() ?? -100,
    );
  }
}

/// One surveyed point: a physical location plus visible APs and metadata.
class Fingerprint {
  final String? id; // MongoDB ObjectId string or unique key
  final int floorId;
  final int? floorNumber;
  final String? floorLabel;
  final String? buildingName; // e.g. "SOT", "SOS"
  final double x;
  final double y;
  final String? nodeId; // optional: id of the nav-graph node, if captured there
  final DateTime capturedAt;
  final Map<String, dynamic>? deviceInfo;
  final List<ApReading> readings;
  final int sampleCount; // Number of scans merged/averaged into this point
  final List<Map<String, dynamic>>? contributors; // Devices that contributed scans

  Fingerprint({
    this.id,
    required this.floorId,
    this.floorNumber,
    this.floorLabel,
    this.buildingName,
    required this.x,
    required this.y,
    required this.readings,
    this.nodeId,
    DateTime? capturedAt,
    this.deviceInfo,
    this.sampleCount = 1,
    this.contributors,
  }) : capturedAt = capturedAt ?? DateTime.now().toUtc();

  Map<String, dynamic> toJson() => {
        if (id != null) '_id': id,
        'floor_id': floorId,
        if (floorNumber != null) 'floor_number': floorNumber,
        if (floorLabel != null) 'floor_label': floorLabel,
        if (buildingName != null) 'building_name': buildingName,
        'x': x,
        'y': y,
        if (nodeId != null) 'node_id': nodeId,
        'captured_at': capturedAt.toIso8601String(),
        if (deviceInfo != null) 'device_info': deviceInfo,
        'sample_count': sampleCount,
        if (contributors != null) 'contributors': contributors,
        'readings': readings.map((r) => r.toJson()).toList(),
      };

  factory Fingerprint.fromMap(Map<String, dynamic> map) {
    String? docId;
    if (map['_id'] != null) {
      docId = map['_id'].toString();
    } else if (map['id'] != null) {
      docId = map['id'].toString();
    }

    final rawReadings = map['readings'] as List<dynamic>? ?? [];
    final readings = rawReadings
        .map((r) => ApReading.fromMap(r as Map<String, dynamic>))
        .toList();

    DateTime parsedDate;
    if (map['captured_at'] is DateTime) {
      parsedDate = map['captured_at'] as DateTime;
    } else if (map['captured_at'] is String) {
      parsedDate = DateTime.tryParse(map['captured_at'] as String) ?? DateTime.now();
    } else {
      parsedDate = DateTime.now();
    }

    return Fingerprint(
      id: docId,
      floorId: (map['floor_id'] as num?)?.toInt() ?? 0,
      floorNumber: (map['floor_number'] as num?)?.toInt(),
      floorLabel: map['floor_label'] as String?,
      buildingName: map['building_name'] as String?,
      x: (map['x'] as num?)?.toDouble() ?? 0.0,
      y: (map['y'] as num?)?.toDouble() ?? 0.0,
      nodeId: map['node_id'] as String?,
      capturedAt: parsedDate,
      deviceInfo: map['device_info'] != null
          ? Map<String, dynamic>.from(map['device_info'] as Map)
          : null,
      sampleCount: (map['sample_count'] as num?)?.toInt() ?? 1,
      contributors: map['contributors'] != null
          ? List<Map<String, dynamic>>.from(
              (map['contributors'] as List).map((c) => Map<String, dynamic>.from(c as Map)))
          : null,
      readings: readings,
    );
  }

  /// Copies this fingerprint with optional updated fields.
  Fingerprint copyWith({
    String? id,
    int? floorId,
    int? floorNumber,
    String? floorLabel,
    String? buildingName,
    double? x,
    double? y,
    String? nodeId,
    DateTime? capturedAt,
    Map<String, dynamic>? deviceInfo,
    List<ApReading>? readings,
    int? sampleCount,
    List<Map<String, dynamic>>? contributors,
  }) {
    return Fingerprint(
      id: id ?? this.id,
      floorId: floorId ?? this.floorId,
      floorNumber: floorNumber ?? this.floorNumber,
      floorLabel: floorLabel ?? this.floorLabel,
      buildingName: buildingName ?? this.buildingName,
      x: x ?? this.x,
      y: y ?? this.y,
      nodeId: nodeId ?? this.nodeId,
      capturedAt: capturedAt ?? this.capturedAt,
      deviceInfo: deviceInfo ?? this.deviceInfo,
      readings: readings ?? this.readings,
      sampleCount: sampleCount ?? this.sampleCount,
      contributors: contributors ?? this.contributors,
    );
  }
}
