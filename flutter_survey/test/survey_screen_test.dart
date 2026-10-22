import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:wifi_survey/screens/survey_screen.dart';

void main() {
  testWidgets('SurveyScreen renders InteractiveViewer and zoom controls',
      (WidgetTester tester) async {
    // Set a predictable surface size for testing
    tester.view.physicalSize = const Size(800, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(() => tester.view.resetPhysicalSize());

    await tester.pumpWidget(
      const MaterialApp(
        home: SurveyScreen(
          svgAssetPath: 'assets/floorplans/floor-0.svg',
          floorId: 0,
          backendBaseUrl: 'http://127.0.0.1:5050',
          svgWidth: 1024.0,
          svgHeight: 526.0,
        ),
      ),
    );

    // Initial check: Floor title in AppBar
    expect(find.text('Survey -- Floor 0'), findsOneWidget);

    // InteractiveViewer is present for zooming and panning
    expect(find.byType(InteractiveViewer), findsOneWidget);

    // Zoom controls (+, -, reset) are present
    expect(find.byIcon(Icons.add), findsOneWidget);
    expect(find.byIcon(Icons.remove), findsOneWidget);
    expect(find.byIcon(Icons.restart_alt), findsOneWidget);

    // Initial zoom indicator badge
    expect(find.text('100%'), findsOneWidget);

    // Initial prompt
    expect(find.text('Tap map to pick a location'), findsOneWidget);
  });

  testWidgets('Tapping on floor plan drops pin and displays SVG coordinates',
      (WidgetTester tester) async {
    tester.view.physicalSize = const Size(800, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(() => tester.view.resetPhysicalSize());

    await tester.pumpWidget(
      const MaterialApp(
        home: SurveyScreen(
          svgAssetPath: 'assets/floorplans/floor-0.svg',
          floorId: 1,
          backendBaseUrl: 'http://127.0.0.1:5050',
          svgWidth: 1024.0,
          svgHeight: 526.0,
        ),
      ),
    );

    // Tap on the center of the InteractiveViewer (center of the floor plan)
    final interactiveViewer = find.byType(InteractiveViewer);
    await tester.tap(interactiveViewer);
    await tester.pumpAndSettle();

    // Pin icon should now be displayed
    expect(find.byIcon(Icons.location_pin), findsOneWidget);

    // Selected text should now show coordinates close to center (512, 263)
    expect(find.textContaining('Selected: (512'), findsOneWidget);

    // "Focus on Pin" button should now be available in the toolbar
    expect(find.byIcon(Icons.my_location), findsOneWidget);

    // Tap clear button to remove selection
    await tester.tap(find.byIcon(Icons.close));
    await tester.pumpAndSettle();

    // Pin is removed
    expect(find.byIcon(Icons.location_pin), findsNothing);
    expect(find.text('Tap map to pick a location'), findsOneWidget);
  });

  testWidgets('Zoom In, Zoom Out, and Reset buttons adjust zoom scale',
      (WidgetTester tester) async {
    tester.view.physicalSize = const Size(800, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(() => tester.view.resetPhysicalSize());

    await tester.pumpWidget(
      const MaterialApp(
        home: SurveyScreen(
          svgAssetPath: 'assets/floorplans/floor-0.svg',
          floorId: 2,
          backendBaseUrl: 'http://127.0.0.1:5050',
          svgWidth: 1024.0,
          svgHeight: 526.0,
        ),
      ),
    );

    expect(find.text('100%'), findsOneWidget);

    // Tap Zoom In (+)
    await tester.tap(find.byIcon(Icons.add));
    await tester.pumpAndSettle();

    // Zoom scale increased to ~150%
    expect(find.text('150%'), findsOneWidget);

    // Reset button appears in top-left badge
    expect(find.text('Reset'), findsOneWidget);

    // Tap Zoom In (+) again -> ~225%
    await tester.tap(find.byIcon(Icons.add));
    await tester.pumpAndSettle();
    expect(find.text('225%'), findsOneWidget);

    // Tap Zoom Out (-) -> back to ~150%
    await tester.tap(find.byIcon(Icons.remove));
    await tester.pumpAndSettle();
    expect(find.text('150%'), findsOneWidget);

    // Tap Fit / Reset Zoom button
    await tester.tap(find.byIcon(Icons.restart_alt));
    await tester.pumpAndSettle();

    // Reset to 100%
    expect(find.text('100%'), findsOneWidget);
  });

  testWidgets('SurveyScreen renders floor-0-ground, floor-1-first, and floor-2-second SVGs',
      (WidgetTester tester) async {
    const floorPlans = [
      'assets/floorplans/floor-0-ground.svg',
      'assets/floorplans/floor-1-first.svg',
      'assets/floorplans/floor-2-second.svg',
    ];

    for (var i = 0; i < floorPlans.length; i++) {
      await tester.pumpWidget(
        MaterialApp(
          home: SurveyScreen(
            svgAssetPath: floorPlans[i],
            floorId: i + 1,
            backendBaseUrl: 'http://127.0.0.1:5050',
            svgWidth: 1750.0,
            svgHeight: 899.0,
          ),
        ),
      );

      expect(find.byType(InteractiveViewer), findsOneWidget);
      expect(find.text('Survey -- Floor ${i + 1}'), findsOneWidget);
    }
  });

  testWidgets('SurveyScreen renders SOS building SVGs',
      (WidgetTester tester) async {
    const sosFloorPlans = [
      'assets/floorplans/sos/floor-0-ground.svg',
      'assets/floorplans/sos/floor-1-first.svg',
      'assets/floorplans/sos/floor-2-second.svg',
    ];

    for (var i = 0; i < sosFloorPlans.length; i++) {
      await tester.pumpWidget(
        MaterialApp(
          home: SurveyScreen(
            svgAssetPath: sosFloorPlans[i],
            floorId: i + 4,
            buildingName: 'SOS',
            backendBaseUrl: 'http://127.0.0.1:5050',
            svgWidth: 1262.0,
            svgHeight: 800.0,
          ),
        ),
      );

      expect(find.byType(InteractiveViewer), findsOneWidget);
      expect(find.text('SOS -- Floor ${i + 4}'), findsOneWidget);
    }
  });
}
