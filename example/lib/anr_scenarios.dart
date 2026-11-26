import 'dart:convert';
import 'dart:ffi';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

const _duration = Duration(seconds: 18);
const _platform = MethodChannel('stalltrace_example/scenarios');

@pragma('vm:never-inline')
void decodeCatalogOnUiIsolate() {
  final page = jsonEncode([
    for (var i = 0; i < 256; i++) {'sku': 'item-$i', 'quantity': i},
  ]);
  final clock = Stopwatch()..start();
  while (clock.elapsed < _duration) {
    jsonDecode(page);
  }
}

@pragma('vm:never-inline')
void waitInNativePoll() {
  final poll = DynamicLibrary.open('libc.so')
      .lookupFunction<
        Int32 Function(Pointer<Void>, Uint32, Int32),
        int Function(Pointer<Void>, int, int)
      >('poll');
  final clock = Stopwatch()..start();
  // Signals can interrupt poll. Repeat the remaining wait against a monotonic deadline.
  var remaining = _duration.inMilliseconds;
  while (remaining > 0) {
    poll(nullptr, 0, remaining);
    remaining = _duration.inMilliseconds - clock.elapsedMilliseconds;
  }
}

enum _Scenario {
  dart('Dart JSON decoding', 'Repeatedly parse a catalog on the UI isolate.'),
  native('Native FFI wait', 'Wait synchronously in Android libc from Dart.'),
  lock(
    'Java lock contention',
    'Block the Android main thread on a lock held by a worker.',
  );

  const _Scenario(this.title, this.description);
  final String title;
  final String description;

  Future<void> run() async {
    switch (this) {
      case dart:
        decodeCatalogOnUiIsolate();
      case native:
        waitInNativePoll();
      case lock:
        await _platform.invokeMethod<void>('waitForLock');
    }
  }
}

class AnrScenariosPage extends StatefulWidget {
  const AnrScenariosPage({super.key});

  @override
  State<AnrScenariosPage> createState() => _AnrScenariosPageState();
}

class _AnrScenariosPageState extends State<AnrScenariosPage> {
  _Scenario? _scenario;
  bool _running = false;
  String? _error;

  Future<void> _start(_Scenario scenario) async {
    if (_scenario != null) return;
    setState(() {
      _scenario = scenario;
      _running = true;
    });
    await WidgetsBinding.instance.endOfFrame;
    if (!mounted) return;
    try {
      await scenario.run();
    } catch (error) {
      _error = error.toString();
    } finally {
      if (mounted) setState(() => _running = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('ANR test scenarios')),
    body: ListView(
      children: [
        if (_scenario == null) ...[
          const ListTile(
            title: Text('Each test freezes this app for about 18 seconds.'),
            subtitle: Text(
              'Choose a test, then tap the frozen page once to trigger an input timeout. '
              'Keep the app in the foreground. If Android closes it, reopen it, '
              'enable collection and refresh Local reports.',
            ),
          ),
          for (final scenario in _Scenario.values)
            ListTile(
              title: Text(scenario.title),
              subtitle: Text(scenario.description),
              trailing: const Icon(Icons.play_arrow),
              onTap: () => _start(scenario),
            ),
        ] else ...[
          ListTile(
            title: Text(_scenario!.title),
            subtitle: Text(
              _running
                  ? 'Tap here once during the freeze.'
                  : _error ?? 'The app is responsive again.',
            ),
          ),
          if (!_running)
            ListTile(
              title: const Text('Back to reports'),
              onTap: () => Navigator.of(context).pop(),
            ),
        ],
      ],
    ),
  );
}
