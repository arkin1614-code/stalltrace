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
}
