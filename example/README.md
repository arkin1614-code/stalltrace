# Android release example

The example consumes the local Stalltrace plugin through a path dependency. Its Dart CPU, synchronous FFI and Android-only JNI workloads each block for 18 s. The JNI case must contain the native wait and its caller with zero application Dart frames. Short stall runs for about 2.3 s. Export copies the plugin's local diagnostics to this example's external files directory.

From this directory, on a dedicated ARM64 Android device with ADB on your path:

```sh
flutter pub get
flutter build apk --release --obfuscate --target-platform android-arm64 --split-debug-info=build/symbols
adb -s "$ANDROID_SERIAL" install -r build/app/outputs/flutter-apk/app-release.apk
python3 ../tool/device_run.py --serial "$ANDROID_SERIAL" --ffi-input-delay 7 --check-interrupted --out build/session
```

The runner taps each button, sends a second touch to the stalled window, records the package's system ANR events, then restarts the example to export exit history. It also retrieves that device's libc for offline CFI checks. Use a new output directory on each run. The complete acceptance test uses a system image that supplies ANR traces; the reference image is Google ARM64 API 37.

Point `LLVM_BIN` at the NDK's host LLVM bin directory. From a fresh build, run:

```sh
native_elf=$(find build/app/intermediates/cxx/RelWithDebInfo -path '*/obj/arm64-v8a/libstalltrace_fixture.so' -print -quit)
python3 ../tool/verify.py --run build/session --llvm "$LLVM_BIN" \
  --dart build/symbols/app.android-arm64.symbols --native "$native_elf" \
  --slow "$(dirname "$native_elf")/libstalltrace_slow.so" --report build/session/report.json
```

For each ANR case on API 30+ the verifier requires a matching PID/TID, real system event, at least two live samples containing the source-located fixture frames in order, a hit within 1.5 s of the event, and an independently decoded system trace. The short case must capture the fixture and recover without a system ANR. Raw files, device ELFs and release symbols are kept under the ignored build directory.


On API 24–29 the verifier checks event, PID, time and live stack evidence. Android's historical trace API starts at API 30. The optional interrupted case force-stops the example during Dart computation, then checks a build-matched sample persisted before recovery. The account of process termination is kept separate from the system ANR classification.

Switch early and Switch late each run Dart for 11 seconds, then enter synchronous FFI for 13 seconds. The runner sends input in each phase on separate runs. The verifier checks both phases, the expected stack close to the system event, and the catalog's selection against the primary system dump on API 30+. The short stall and Dart CPU run in the same process to check unique association. Native-only samples are individually checked for false Dart frames. Function names must match the example source file and line range.

With `--check-interrupted`, the runner also kills the example 36 seconds into a slow native constructor. Acceptance requires source-located constructor samples persisted in two report segments and a preceding raw record for each. Legacy devices supply libc from `/system/lib64` and newer releases can use the runtime APEX. Each trial installs/runs only the example and stops its processes. Keep a physical test device unlocked, with USB debugging authorized.

To exercise a conflicting signal owner, repeated start/stop and deletion through the public Dart API, run on a dedicated ARM64 device:

```sh
flutter test -d "$ANDROID_SERIAL" integration_test/capture_lifecycle_test.dart
```
