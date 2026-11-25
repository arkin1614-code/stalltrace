import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stalltrace/stalltrace.dart';
import 'package:stalltrace_example/main.dart';

void main() {
  testWidgets('offers the three reproducible workloads and export', (
    tester,
  ) async {
    const channel = MethodChannel('dev.stalltrace/collector');
    final messenger = tester.binding.defaultBinaryMessenger;
    messenger.setMockMethodCallHandler(
      channel,
      (call) async => {
        'nativeSamplingAvailable': true,
        'reportsDirectory': '/private/reports',
      },
    );
    addTearDown(() => messenger.setMockMethodCallHandler(channel, null));
    final session = await Stalltrace.start();
    await tester.pumpWidget(MaterialApp(home: Lab(session: session)));
    for (final label in ['Dart CPU', 'Native FFI', 'Short stall', 'Export']) {
      expect(find.widgetWithText(FilledButton, label), findsOneWidget);
    }
  });
}
