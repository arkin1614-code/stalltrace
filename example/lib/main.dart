import 'dart:ffi';

import 'package:stalltrace/stalltrace.dart';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

const channel = MethodChannel('anr/lab');
final _wait = DynamicLibrary.open('libstalltrace_fixture.so')
    .lookupFunction<Void Function(Int32), void Function(int)>(
      'fixture_native_entry',
    );
final _tid = DynamicLibrary.process()
    .lookupFunction<Int32 Function(), int Function()>('gettid');

@pragma('vm:never-inline')
int fixtureDartCompute(int milliseconds) {
  final timer = Stopwatch()..start();
  var result = 1;
  do {
    for (var i = 0; i < 32768; i++) {
      result = (result * 48271 + i) & 0x7fffffff;
    }
  } while (timer.elapsedMilliseconds < milliseconds);
  return result;
}

@pragma('vm:never-inline')
int fixtureDartCaller(int ms) => fixtureDartCompute(ms) + 1;

@pragma('vm:never-inline')
int fixtureDartFfiCaller(int ms) {
  _wait(ms);
  return ms + 1;
}

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final session = await Stalltrace.start();
  runApp(MaterialApp(home: Lab(session: session)));
}

class Lab extends StatefulWidget {
  const Lab({required this.session, super.key});
  final StalltraceSession session;
  @override
  State<Lab> createState() => _LabState();
}

class _LabState extends State<Lab> {
  String status = 'Ready';
  bool busy = false;

  Future<void> run(String name) async {
    if (busy) return;
    setState(() {
      busy = true;
      status = name;
    });
    try {
      if (name == 'Export') {
        status = (await channel.invokeMethod<String>(
          'export',
          widget.session.reportsDirectory,
        ))!;
        await channel.invokeMethod<void>('mark', 'exported $status');
      } else {
        await channel.invokeMethod<void>(
          'mark',
          'begin $name dartTid=${_tid()}',
        );
        final value = name == 'Native FFI'
            ? fixtureDartFfiCaller(18000)
            : fixtureDartCaller(name == 'Short stall' ? 2300 : 18000);
        await channel.invokeMethod<void>('mark', 'end $name result=$value');
        status = 'Ready';
      }
    } finally {
      if (mounted) {
        setState(() {
          busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Stalltrace example')),
    body: Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (final name in [
            'Dart CPU',
            'Native FFI',
            'Short stall',
            'Export',
          ])
            Padding(
              padding: const EdgeInsets.all(6),
              child: FilledButton(
                onPressed: busy ? null : () => run(name),
                child: Text(name),
              ),
            ),
          Text(status, textAlign: TextAlign.center),
        ],
      ),
    ),
  );
}
