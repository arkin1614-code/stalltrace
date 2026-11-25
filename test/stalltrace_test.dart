import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stalltrace/stalltrace.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('dev.stalltrace/collector');
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;

  tearDown(() => messenger.setMockMethodCallHandler(channel, null));

  test(
    'exposes the native capability and the app-private storage path',
    () async {
      final methods = <String>[];
      messenger.setMockMethodCallHandler(channel, (call) async {
        methods.add(call.method);
        if (call.method == 'stop') return null;
        return {
          'nativeSamplingAvailable': false,
          'reportsDirectory': '/private/reports',
        };
      });
      final session = await Stalltrace.start();
      expect(session.nativeSamplingAvailable, isFalse);
      expect(session.reportsDirectory, '/private/reports');
      await Stalltrace.stop();
      expect(methods, ['start', 'stop']);
    },
  );

  test('rejects incomplete capability data', () async {
    messenger.setMockMethodCallHandler(channel, (call) async => {});
    await expectLater(Stalltrace.start(), throwsFormatException);
  });
  test(
    'decodes a recovered input ANR independently of the exit reason',
    () async {
      messenger.setMockMethodCallHandler(
        channel,
        (call) async => [
          {
            'id': 'stall',
            'kind': 'stall',
            'confirmedAnrExit': false,
            'active': false,
            'files': ['s.jsonl'],
            'traceAssociation': 'matched',
            'exitReportId': 'exit',
            'traceWallMs': 2000,
            'sampleDeltaMs': -50,
            'sampleSelection': 'matchingFrames',
            'sampleMonoNs': 12345,
          },
        ],
      );
      final report = (await Stalltrace.listReports()).single;
      expect(report.traceAssociation, StalltraceTraceAssociation.matched);
      expect(report.confirmedAnrExit, isFalse);
      expect(report.exitReportId, 'exit');
      expect(report.sampleDeltaMs, -50);
      expect(report.sampleSelection, StalltraceSampleSelection.matchingFrames);
      expect(report.sampleMonoNs, 12345);
    },
  );

  test(
    'lists grouped evidence and deletes by opaque ID without starting',
    () async {
      final methods = <String>[];
      messenger.setMockMethodCallHandler(channel, (call) async {
        methods.add(call.method);
        if (call.method == 'listReports') {
          return [
            {
              'id': 'id',
              'kind': 'processExit',
              'confirmedAnrExit': false,
              'active': false,
              'files': ['exit.json', 'exit.trace'],
            },
          ];
        }
        expect(call.arguments, 'id');
        return true;
      });
      final report = (await Stalltrace.listReports()).single;
      expect(report.kind, StalltraceReportKind.processExit);
      expect(report.confirmedAnrExit, isFalse);
      expect(report.files, ['exit.json', 'exit.trace']);
      expect(await Stalltrace.deleteReport(report.id), isTrue);
      expect(methods, ['listReports', 'deleteReport']);
    },
  );
}
