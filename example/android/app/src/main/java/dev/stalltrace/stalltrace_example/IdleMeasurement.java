package dev.stalltrace.stalltrace_example;

import android.os.FileObserver;
import android.os.Process;
import android.os.SystemClock;
import java.io.*;
import org.json.JSONObject;

/** Example-only measurements. The main thread does no periodic benchmarking work. */
final class IdleMeasurement {
    private static final long WINDOW_MS = 600_000;
    private static final class Disk extends FileObserver {
        final File dir;
        long peak, events;
        Disk(File dir) {
            super(dir.getPath(), CREATE | DELETE | MODIFY | MOVED_FROM | MOVED_TO);
            this.dir = dir;
        }
        synchronized long sample() {
            long bytes = 0;
            File[] entries = dir.listFiles();
            if (entries == null)
                throw new IllegalStateException("Cannot read report directory");
            for (File f : entries)
                if (f.isFile())
                    bytes += f.length();
            peak = Math.max(bytes, peak);
            return bytes;
        }
        @Override
        public synchronized void onEvent(int event, String path) {
            events++;
            sample();
        }
    }

    private static long counter(File dir, String file, String name) throws IOException {
        try (BufferedReader input = new BufferedReader(new FileReader(new File(dir, file)))) {
            String line;
            while ((line = input.readLine()) != null) {
                if (name == null)
                    return Long.parseLong(line.trim().split("\\s+")[0]);
                String[] parts = line.split(":", 2);
                if (parts.length == 2 && name.equals(parts[0].trim()))
                    return Long.parseLong(parts[1].trim());
            }
        }
        return -1;
    }
    private static File watchdog() throws IOException {
        File[] threads = new File("/proc/self/task").listFiles();
        if (threads != null)
            for (File thread : threads) {
                try (BufferedReader name = new BufferedReader(new FileReader(new File(thread, "comm")))) {
                    if ("anr-watchdog".equals(name.readLine()))
                        return thread;
                } catch (FileNotFoundException exited) { /* Another thread may have exited. */
                }
            }
        throw new IOException("Missing watchdog thread");
    }

    static JSONObject measure(File dir) throws Exception {
        Thread.sleep(5000); // Allow state changes and the button frame to settle.
        File main = new File("/proc/self/task", Integer.toString(Process.myPid()));
        File watch = watchdog();
        Disk disk = new Disk(dir);
        disk.startWatching();
        try {
            long startBytes = disk.sample(), startMain = counter(main, "schedstat", null);
            long mainWake = counter(main, "sched", "nr_wakeups"),
                 watchWake = counter(watch, "sched", "nr_wakeups");
            long cpu = Process.getElapsedCpuTime(), elapsed = SystemClock.elapsedRealtime(),
                 up = SystemClock.uptimeMillis();
            Thread.sleep(WINDOW_MS);
            long elapsedMs = SystemClock.elapsedRealtime() - elapsed, upMs = SystemClock.uptimeMillis() - up;
            long deltaCpu = Process.getElapsedCpuTime() - cpu, endMain = counter(main, "schedstat", null);
            long endMW = counter(main, "sched", "nr_wakeups"), endWW = counter(watch, "sched", "nr_wakeups");
            JSONObject out =
                new JSONObject()
                    .put("elapsedMs", elapsedMs)
                    .put("uptimeMs", upMs)
                    .put("pid", Process.myPid())
                    .put("processCpuMs", deltaCpu)
                    .put("mainCpuNs", endMain - startMain)
                    .put("mainWakeups", mainWake < 0 || endMW < mainWake ? JSONObject.NULL : endMW - mainWake)
                    .put("watchdogWakeups",
                        watchWake < 0 || endWW < watchWake ? JSONObject.NULL : endWW - watchWake);
            synchronized (disk) {
                return out.put("reportBytesStart", startBytes)
                    .put("reportBytesEnd", disk.sample())
                    .put("peakObservedReportBytes", disk.peak)
                    .put("reportDirectoryEvents", disk.events);
            }
        } finally {
            disk.stopWatching();
        }
    }
}
