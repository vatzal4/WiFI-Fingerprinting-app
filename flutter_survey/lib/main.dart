import 'package:flutter/material.dart';
import 'screens/building_picker_screen.dart';

void main() {
  runApp(const WifiSurveyApp());
}

class WifiSurveyApp extends StatelessWidget {
  const WifiSurveyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'WiFi Fingerprint Survey',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: Colors.indigo,
        useMaterial3: true,
      ),
      home: const BuildingPickerScreen(),
    );
  }
}
