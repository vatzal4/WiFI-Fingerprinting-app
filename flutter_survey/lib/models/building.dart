import 'package:flutter/material.dart';

/// Configuration for a specific floor within a building.
class FloorConfig {
  final int id;
  final int floorNumber;
  final String label;
  final String svgAssetPath;
  final double svgWidth;
  final double svgHeight;

  const FloorConfig({
    required this.id,
    required this.floorNumber,
    required this.label,
    required this.svgAssetPath,
    required this.svgWidth,
    required this.svgHeight,
  });
}

/// Configuration for a campus building.
class BuildingConfig {
  final int id;
  final String code; // "SOT", "SOS", "Anviksha"
  final String name; // e.g. "School of Technology"
  final String description;
  final IconData icon;
  final List<FloorConfig> floors;
  final bool hasFloorPlans;

  const BuildingConfig({
    required this.id,
    required this.code,
    required this.name,
    this.description = '',
    this.icon = Icons.apartment,
    required this.floors,
    this.hasFloorPlans = true,
  });
}

/// Master campus data configuration with SOT, SOS, and Anviksha.
class CampusData {
  static const List<BuildingConfig> buildings = [
    BuildingConfig(
      id: 1,
      code: 'SOT',
      name: 'School of Technology (SOT)',
      description: 'Engineering & Technology (Ground, 1st & 2nd floors)',
      icon: Icons.engineering,
      hasFloorPlans: true,
      floors: [
        FloorConfig(
          id: 1,
          floorNumber: 0,
          label: 'Floor 0 -- Ground floor',
          svgAssetPath: 'assets/floorplans/sot/floor-0-ground.svg',
          svgWidth: 1750.0,
          svgHeight: 899.0,
        ),
        FloorConfig(
          id: 2,
          floorNumber: 1,
          label: 'Floor 1 -- First floor',
          svgAssetPath: 'assets/floorplans/sot/floor-1-first.svg',
          svgWidth: 1750.0,
          svgHeight: 899.0,
        ),
        FloorConfig(
          id: 3,
          floorNumber: 2,
          label: 'Floor 2 -- Second floor',
          svgAssetPath: 'assets/floorplans/sot/floor-2-second.svg',
          svgWidth: 1750.0,
          svgHeight: 899.0,
        ),
      ],
    ),
    BuildingConfig(
      id: 2,
      code: 'SOS',
      name: 'School of Science (SOS)',
      description: 'Pure & Applied Sciences (Ground, 1st & 2nd floors)',
      icon: Icons.science,
      hasFloorPlans: true,
      floors: [
        FloorConfig(
          id: 4,
          floorNumber: 0,
          label: 'Floor 0 -- Ground floor',
          svgAssetPath: 'assets/floorplans/sos/floor-0-ground.svg',
          svgWidth: 1262.0,
          svgHeight: 800.0,
        ),
        FloorConfig(
          id: 5,
          floorNumber: 1,
          label: 'Floor 1 -- First floor',
          svgAssetPath: 'assets/floorplans/sos/floor-1-first.svg',
          svgWidth: 1262.0,
          svgHeight: 800.0,
        ),
        FloorConfig(
          id: 6,
          floorNumber: 2,
          label: 'Floor 2 -- Second floor',
          svgAssetPath: 'assets/floorplans/sos/floor-2-second.svg',
          svgWidth: 1262.0,
          svgHeight: 800.0,
        ),
      ],
    ),
    BuildingConfig(
      id: 3,
      code: 'Anviksha',
      name: 'Anviksha Complex',
      description: 'Research & Administration Complex (Floor plans pending)',
      icon: Icons.account_balance,
      hasFloorPlans: false,
      floors: [],
    ),
  ];

  static BuildingConfig? findByCode(String code) {
    try {
      return buildings.firstWhere(
        (b) => b.code.toLowerCase() == code.toLowerCase(),
      );
    } catch (_) {
      return null;
    }
  }
}
