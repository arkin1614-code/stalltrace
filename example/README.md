# Android release example

The example consumes the local Stalltrace plugin through a path dependency. Its Dart CPU and synchronous native FFI workloads each block for 18 s. Short stall runs for about 2.3 s. Export copies the plugin's local diagnostics to this example's external files directory.

From this directory, on a dedicated ARM64 Android emulator with ADB on your path:

```sh
flutter pub get
flutter build apk --release --target-platform android-arm64 --split-debug-info=build/symbols
adb -s "$ANDROID_SERIAL" install -r build/app/outputs/flutter-apk/app-release.apk
python3 ../tool/device_run.py --serial "$ANDROID_SERIAL" --ffi-input-delay 7 --out build/session
```

The runner taps each button, sends a second touch to the stalled window, records the package's system ANR events, then restarts the example to export exit history. It also retrieves that emulator's libc for offline CFI checks. Use a new output directory on each run. The complete acceptance test uses a system image that supplies ANR traces; the reference image is Google ARM64 API 37.

Point `LLVM_BIN` at the NDK's host LLVM bin directory. From a fresh build, run:

```sh
native_elf=$(find build/app/intermediates/cxx/RelWithDebInfo -path '*/obj/arm64-v8a/libstalltrace_fixture.so' -print -quit)
python3 ../tool/verify.py --run build/session --llvm "$LLVM_BIN" \
  --dart build/symbols/app.android-arm64.symbols --native "$native_elf" \
  --report build/session/report.json
```

For both positive cases the verifier requires a matching PID/TID, real system event, at least two live samples containing the source-located fixture frames in order, a hit within 1.5 s of the event, and an independently decoded system trace. The short case must capture the fixture and recover without a system ANR. Raw files, device ELFs and release symbols are kept under the ignored build directory.
