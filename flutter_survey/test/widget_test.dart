import 'package:flutter_test/flutter_test.dart';
import 'package:wifi_survey/main.dart';

void main() {
  testWidgets('App renders building picker with SOT, SOS, and Anviksha',
      (WidgetTester tester) async {
    await tester.pumpWidget(const WifiSurveyApp());
    await tester.pump();

    // App bar and title
    expect(find.text('WiFi Fingerprint Survey'), findsOneWidget);

    // 3 Buildings must be listed
    expect(find.text('SOT'), findsOneWidget);
    expect(find.text('SOS'), findsOneWidget);
    expect(find.text('Anviksha'), findsOneWidget);

    // Tapping on SOT opens its floor picker
    await tester.tap(find.text('SOT'));
    await tester.pumpAndSettle();

    expect(find.text('SOT -- Select Floor'), findsOneWidget);
    expect(find.text('Floor 0 -- Ground floor'), findsOneWidget);
  });
}
