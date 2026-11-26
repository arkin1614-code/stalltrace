import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:stalltrace/stalltrace.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  const fixture = MethodChannel('anr/lab');

  testWidgets('occupied signal, repeated starts/stops, and report deletion', (
    tester,
  ) async {
    await tester.pumpWidget(const SizedBox());
    expect(await fixture.invokeMethod<bool>('reserveTestSignal'), isTrue);
    final session = await Stalltrace.start();
    expect(session.nativeSamplingAvailable, isFalse);
    expect((await Stalltrace.start()).nativeSamplingAvailable, isFalse);
    await Stalltrace.stop();
    await Stalltrace.stop();

    Future<Set<String>> stalls() async => {
      for (final row in await Stalltrace.listReports())
        if (row.kind == StalltraceReportKind.stall) row.id,
    };
    final baseline = await stalls();
    await fixture.invokeMethod<void>('shortPlatformStall');
    expect(await stalls(), baseline);

    await Stalltrace.start();
    await fixture.invokeMethod<void>('shortPlatformStall');
    final id = (await stalls()).difference(baseline).single;
    late StalltraceReport report;
    final deadline = DateTime.now().add(const Duration(seconds: 5));
    do {
      report = (await Stalltrace.listReports()).singleWhere((r) => r.id == id);
      if (!report.active) break;
      await Future<void>.delayed(const Duration(milliseconds: 100));
    } while (DateTime.now().isBefore(deadline));
    expect(report.active, isFalse);
    final file = File('${session.reportsDirectory}/${report.files.first}');
    final rows = (await file.readAsLines())
        .map((line) => jsonDecode(line) as Map)
        .toList();
    final samples = rows.where((r) => r['type'] == 'stall_sample');
    expect(samples, isNotEmpty);
    expect(
      samples.every((r) => r['native'] == null && r['schemaVersion'] == 1),
      isTrue,
    );
    expect(
      rows.any(
        (r) => r['type'] == 'java_sample' && (r['frames'] as List).isNotEmpty,
      ),
      isTrue,
    );
    expect(await Stalltrace.deleteReport(id), isTrue);
    expect(await Stalltrace.deleteReport(id), isFalse);
    expect(await file.exists(), isFalse);
    await Stalltrace.stop();
  });
}
