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

/// Where the evidence in a report came from.
enum StalltraceReportKind {
  /// Timestamped samples from an overdue main-thread heartbeat.
  stall,

  /// A historical OS exit record and any associated system trace.
  processExit,
}

/// Result of matching a foreground stall to an Android input-ANR dump.
enum StalltraceTraceAssociation { none, matched, ambiguous }

/// Evidence used to choose a live sample adjacent to the Android dump.
enum StalltraceSampleSelection {
  matchingFrames,
  nearestTime,

  /// Adjacent main-thread Java observation matches a positioned APK frame and its call context.
  matchingJavaSite,
}

/// A snapshot of one report's metadata. File names are relative to the session directory.
final class StalltraceReport {
  const StalltraceReport._(
    this.id,
    this.kind,
    this.confirmedAnrExit,
    this.active,
    this.files,
    this.traceAssociation,
    this.exitReportId,
    this.traceWallMs,
    this.sampleDeltaMs,
    this.sampleMonoNs,
    this.sampleSelection,
  );

  /// Opaque identifier accepted by [Stalltrace.deleteReport].
  final String id;

  /// Distinguishes an observed stall from Android exit history.
  final StalltraceReportKind kind;

  /// Android explicitly attributed the process exit to an ANR.
  /// False also covers recovered ANRs whose trace is attached to a later exit.
  final bool confirmedAnrExit;

  /// Whether the watchdog was writing this report when the list was produced.
  final bool active;

  /// Includes any rotated stall segments or accompanying system trace.
  final List<String> files;

  /// Matched only when both the stall and the primary system dump are unique.
  final StalltraceTraceAssociation traceAssociation;

  /// ID of the process exit containing the matched dump.
  final String? exitReportId;

  /// Wall-clock time of the system dump in milliseconds since the Unix epoch.
  final int? traceWallMs;

  /// Signed difference between selected capture and dump, in milliseconds.
  final int? sampleDeltaMs;

  /// Monotonic timestamp identifying the selected native sample in the JSONL.
  final int? sampleMonoNs;

  /// Matching frames require the same leaf module and two consecutive saved call sites.
  /// A Java site match describes the managed call site observed shortly after native capture.
  /// A time-only match may span a change in workload. Older plugins omit this field.
  final StalltraceSampleSelection? sampleSelection;

  static StalltraceReport _decode(Object? value) {
    if (value case {
      'id': final String id,
      'kind': final String kind,
      'confirmedAnrExit': final bool confirmed,
      'active': final bool active,
      'files': final List<Object?> files,
    }) {
      final type = switch (kind) {
        'stall' => StalltraceReportKind.stall,
        'processExit' => StalltraceReportKind.processExit,
        _ => throw const FormatException('Unknown Stalltrace report kind'),
      };
      final association = switch (value['traceAssociation']) {
        null || 'none' => StalltraceTraceAssociation.none,
        'ambiguous' => StalltraceTraceAssociation.ambiguous,
        'matched' => StalltraceTraceAssociation.matched,
        _ => throw const FormatException('Unknown trace association'),
      };
      if (association == StalltraceTraceAssociation.matched &&
          (value['exitReportId'] is! String ||
              value['traceWallMs'] is! int ||
              value['sampleDeltaMs'] is! int ||
              value['sampleMonoNs'] is! int)) {
        throw const FormatException('Incomplete trace association');
      }
      if (files.every((file) => file is String)) {
        return StalltraceReport._(
          id,
          type,
          confirmed,
          active,
          List<String>.unmodifiable(files.cast<String>()),
          association,
          value['exitReportId'] as String?,
          value['traceWallMs'] as int?,
          value['sampleDeltaMs'] as int?,
          value['sampleMonoNs'] as int?,
          switch (value['sampleSelection']) {
            'matchingFrames' => StalltraceSampleSelection.matchingFrames,
            'matchingJavaSite' => StalltraceSampleSelection.matchingJavaSite,
            'nearestTime' => StalltraceSampleSelection.nearestTime,
            null => null,
            _ => throw const FormatException('Unknown sample selection'),
          },
        );
      }
    }
    throw const FormatException('Invalid Stalltrace report');
  }
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

  /// Lists stored reports without starting the collector. The list is a snapshot.
  static Future<List<StalltraceReport>> listReports() async {
    final rows = await _channel.invokeListMethod<Object?>('listReports');
    if (rows == null) {
      throw const FormatException('Missing Stalltrace report list');
    }
    return List.unmodifiable(rows.map(StalltraceReport._decode));
  }

  /// Deletes a report and its companion files. Returns false if already absent.
  /// An active report throws a [PlatformException] with code `report_active`.
  static Future<bool> deleteReport(String id) async {
    final deleted = await _channel.invokeMethod<bool>('deleteReport', id);
    if (deleted == null) throw const FormatException('Missing deletion result');
    return deleted;
  }

  /// Stops requesting foreground sampling for this engine.
  ///
  /// Existing files remain available. The process retains native resources for
  /// subsequent starts. A sample already in progress may finish writing.
  static Future<void> stop() => _channel.invokeMethod<void>('stop');
}
