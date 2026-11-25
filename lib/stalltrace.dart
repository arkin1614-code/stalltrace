import 'package:flutter/services.dart';

/// Capabilities and storage location for the current Android process.
final class StalltraceSession {
  const StalltraceSession._(
    this.nativeSamplingAvailable,
    this.reportsDirectory,
  );

  /// Whether the ARM64 native sampler could claim its signal at startup.
  final bool nativeSamplingAvailable;

  /// Absolute path to snapshots and exit records in Android no-backup storage.
  ///
  /// Newly written JSONL files can grow while a stall is in progress.
  final String reportsDirectory;
}

/// Foreground Android main-thread stall collection.
abstract final class Stalltrace {
  static const _channel = MethodChannel('dev.stalltrace/collector');

  /// Enables this engine's collector while its host activity is resumed.
  ///
  /// Repeated starts share the process-wide sampler. Call after
  /// WidgetsFlutterBinding.ensureInitialized() on Android.
  static Future<StalltraceSession> start() async {
    final state = await _channel.invokeMapMethod<String, Object?>('start');
    if (state == null ||
        state['nativeSamplingAvailable'] is! bool ||
        state['reportsDirectory'] is! String) {
      throw const FormatException('Invalid Stalltrace start response');
    }
    return StalltraceSession._(
      state['nativeSamplingAvailable']! as bool,
      state['reportsDirectory']! as String,
    );
  }

  /// Stops requesting foreground sampling for this engine.
  ///
  /// Existing files remain available. The process retains native resources for
  /// subsequent starts. A sample already in progress may finish writing.
  static Future<void> stop() => _channel.invokeMethod<void>('stop');
}
