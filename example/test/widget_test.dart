import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stalltrace_example/main.dart';

void main() {
  testWidgets('reads saved reports and collects only after opt-in', (
    tester,
  ) async {
    const channel = MethodChannel('dev.stalltrace/collector');
    final messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    final calls = <String>[];
    messenger.setMockMethodCallHandler(channel, (call) async {
      calls.add(call.method);
      return switch (call.method) {
        'listReports' => [
          {
            'id': 'saved-report',
            'kind': 'stall',
            'confirmedAnrExit': false,
            'active': false,
            'files': ['saved-report.jsonl'],
          },
        ],
        'start' => {
          'nativeSamplingAvailable': false,
          'reportsDirectory': '/private/reports',
        },
        'stop' => null,
        _ => throw UnsupportedError(call.method),
      };
    });
    addTearDown(() => messenger.setMockMethodCallHandler(channel, null));

    await tester.pumpWidget(const MaterialApp(home: DiagnosticsPage()));
    await tester.pumpAndSettle();
    expect(calls, ['listReports']);
    expect(find.text('Foreground stall'), findsOneWidget);
    expect(find.text('saved-report'), findsOneWidget);

    expect(
      tester
          .widget<ListTile>(find.widgetWithText(ListTile, 'ANR test scenarios'))
          .enabled,
      isFalse,
    );
    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    expect(find.text('Collecting Java stacks only'), findsOneWidget);

    const scenarios = MethodChannel('stalltrace_example/scenarios');
    final released = Completer<void>();
    final scenarioCalls = <String>[];
    messenger.setMockMethodCallHandler(scenarios, (call) async {
      scenarioCalls.add(call.method);
      await released.future;
      return null;
    });
    addTearDown(() => messenger.setMockMethodCallHandler(scenarios, null));

    await tester.tap(find.text('ANR test scenarios'));
    await tester.pumpAndSettle();
    expect(find.text('Dart JSON decoding'), findsOneWidget);
    expect(find.text('Native FFI wait'), findsOneWidget);
    expect(scenarioCalls, isEmpty);
    await tester.tap(find.text('Java lock contention'));
    await tester.pumpAndSettle();
    expect(scenarioCalls, ['waitForLock']);
    expect(find.text('Tap here once during the freeze.'), findsOneWidget);
    released.complete();
    await tester.pumpAndSettle();
    await tester.tap(find.text('Back to reports'));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Refresh reports'));
    await tester.pumpAndSettle();
    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    expect(find.text('Collection is off'), findsOneWidget);
    expect(calls, [
      'listReports',
      'start',
      'listReports',
      'listReports',
      'listReports',
      'stop',
      'listReports',
    ]);
  });
}
