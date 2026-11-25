## 0.1.0-dev.5

* Associate a fatal input ANR dumped just after the final sample when the last open probe and primary system stack share matching frames. Both the sample-to-dump and dump-to-exit gaps must be within 2.5 seconds.
* Record the foreground ANR kill and process death in the Android fixture runner for devices that terminate the example during the workload.

## 0.1.0-dev.4

* Check the two neighboring samples against the primary Android dump when selecting a live stack. Expose frame-supported and time-only selection through the Dart API.
* Add configurable two-phase workloads, OEM dialog handling, resuming interrupted test runs and an opt-in 20-minute foreground idle comparison to the example.

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
