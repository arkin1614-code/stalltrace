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

    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    expect(find.text('Collecting Java stacks only'), findsOneWidget);

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
      'stop',
      'listReports',
    ]);
  });
}
