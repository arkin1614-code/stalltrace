# Stalltrace

Stalltrace captures a Flutter Android app's main-thread stack while its foreground UI is unresponsive. On ARM64 release builds, matching debug symbols can recover native functions and synchronous Dart AOT frames across an FFI call. A watchdog observation records a stall. Android exit records and system traces provide independent evidence of an ANR.

## Use the development package

Add a path dependency pointing to this checkout. The example app uses the same public API.

```yaml
dependencies:
  stalltrace:
    path: ../stalltrace
```

```dart
import 'package:flutter/widgets.dart';
import 'package:stalltrace/stalltrace.dart';

Future<StalltraceSession> enableDiagnostics() async {
  WidgetsFlutterBinding.ensureInitialized();
  return Stalltrace.start();
}
```

Call this from the root isolate of your Android app after opting in to diagnostics. The session exposes `nativeSamplingAvailable` and `reportsDirectory`. Repeated calls reuse the collector. Call `await Stalltrace.stop()` to suspend this engine's sampling. An in-progress write can finish, and the native signal reservation lasts for the process lifetime.

The plugin writes to the app's private no-backup directory. A `.jsonl` contains timestamped stall snapshots and Java frames. An `exit-*.json` describes a historical process exit and may refer to a separate `.trace`. History collection runs asynchronously after startup. A recovered ANR may have a trace attached to a later exit for a different reason. The host controls retention beyond the plugin's rolling limits and any export or upload. The example exports copies only when its Export button is used.

## Manage local reports

```dart
final reports = await Stalltrace.listReports();
for (final report in reports) {
  if (report.confirmedAnrExit) {
    // Android attributed this process exit to an ANR.
  }
}
// When the host or its user chooses to remove a specific report:
// await Stalltrace.deleteReport(report.id);
```

Listing returns opaque IDs, the evidence kind, active state and relative file names. A stall includes its rotated segments; an exit record includes its available trace. Deletion is idempotent and can return `report_active` during capture. [Data and deletion semantics](doc/PRIVACY.md)

## Read a stack

Build the host with split debug information and retain the exact ELF symbols for each release and ABI. Supply the unstripped ELF for each native module you want to decode. Run the following from the Stalltrace checkout with Python 3 and the LLVM tools from the Android NDK.

```sh
python3 tool/symbolize.py "$REPORT_FILE" --llvm "$LLVM_BIN" \
  --elf "libapp.so=$DART_SYMBOLS" --elf "libmy_plugin.so=$NATIVE_SYMBOLS"
```

The input may be a JSONL snapshot file or an Android ANR trace. `DART_SYMBOLS` points to the release's `app.android-arm64.symbols`. Also supply `--elf "libc.so=$DEVICE_LIBC"` for supported frameless libc calls. The decoder checks ELF Build IDs and expands available inline frames. Missing symbols and interrupted frame-pointer chains limit stack coverage.

## Android support

| Capability | Development version |
| --- | --- |
| Host | Android API 24+, standard lifecycle-aware Flutter Activity; Flutter 3.47+, Dart 3.13+ |
| Live sampling | Foreground main Looper; first snapshot after about 1.5 s, then every 2 s during a stall |
| Mixed Dart/native stack | ARM64 AOT when the root isolate runs on the platform thread; the host must reserve `SIGRTMIN + 6` |
| Reduced capability | If the sampler cannot initialize, Java sampling continues and the session reports the missing native capability |
| System exit history | API 30+; trace availability depends on the OS |
| Build tools exercised | Flutter 3.47.9, JDK 17, compile SDK 36, NDK 28.2.13676358, CMake 3.22.1 |

This version targets foreground input stalls. Split engine threads, background ANR types, other isolates and asynchronous Dart call chains need separate coverage.

[Run the release example and verify its stacks](example/README.md) · [Release plan for review (中文)](doc/RELEASE_PLAN.zh-CN.md) · [License](LICENSE)
