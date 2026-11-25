## 0.1.0-dev.3

* Persist raw ARM64 registers (PC and LR), return addresses and their executable mappings before resolving build-matched ELF frames. Sampling works during a blocked dynamic library constructor.
* Expose unique and ambiguous input-ANR trace associations, the signed sample-to-dump time difference and the selected capture timestamp.
* Apply retention to complete reports at startup and after capture.
* Verify source ranges, false Dart attribution, two-phase stalls, interrupted loader stalls and legacy libc locations in the example.

## 0.1.0-dev.2

* Added typed report discovery and deletion, including rotated samples and suppression of deleted Android exit records.
* Added a version field to new persisted records and Android report-store regression tests.
* Added CI for static checks, Flutter tests, native report-store tests, an obfuscated ARM64 example build and package validation.
* Added a native-only JNI workload, an interrupted-write device check and a lifecycle integration test for occupied signals.

## 0.1.0-dev.1

* Extracted the Android foreground watchdog, ARM64 sampler and historical ANR traces into a Flutter plugin.
* Added the opt-in Dart API, the standalone symbolizer and an Android example with three reproducible workloads.
