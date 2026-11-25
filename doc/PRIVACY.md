# Diagnostic data

Collection starts on application opt-in. The Android plugin stores reports in the app's private no-backup directory. Stack samples can contain module names, Build IDs, relative program counters, Java class and method names, PIDs, a process-session identifier and timestamps. System traces can include additional threads, process details, memory summaries and information specific to the device. Inspect and redact data before sharing.

The plugin has no upload client. A host can list existing reports even before starting the sampler and can delete one report with its companion files. Files already sent or copied elsewhere have their own lifetime. The example's Export button creates a separate copy in its external app directory.

A deleted historical report leaves an empty suppression marker keyed by exit timestamp and PID. It prevents importing the same Android record again while it is in the queried history window. A later successful import removes expired markers. Android manages the original system record. A report being written returns `report_active`; retry after the stall ends.
