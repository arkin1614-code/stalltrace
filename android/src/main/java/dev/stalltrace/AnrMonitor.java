package dev.stalltrace;

import android.app.ActivityManager;
import android.app.ApplicationExitInfo;
import android.content.Context;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.os.SystemClock;
import android.util.Log;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

/** One monitor per process. Install on the main thread; enable across foreground resumes. */
final class AnrMonitor {
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final AtomicLong EPOCH = new AtomicLong();
    private static volatile boolean active;
    private static boolean started;
    private static boolean nativeReady;
    private static int foregroundOwners;
    private static native boolean installSampler();
    private static native String sample();

    static synchronized boolean install(Context context) {
        if (started) return nativeReady;
        try {
            System.loadLibrary("stalltrace");
            nativeReady = installSampler();
        } catch (UnsatisfiedLinkError unavailable) {
            Log.i("Stalltrace", "Native sampler unavailable for this process ABI");
        }
        File dir = directory(context);
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IllegalStateException("Cannot create reports directory");
        started = true;
        Thread monitor = new Thread(() -> run(dir, nativeReady, Build.VERSION.SDK_INT >= 28 ? android.app.Application.getProcessName() : context.getPackageName()), "anr-watchdog");
        monitor.setDaemon(true);
        monitor.start();
        Context app = context.getApplicationContext();
        ReportStore.IO.execute(() -> {
            try {
                ReportStore.recover(dir, null);
                ReportStore.trimStalls(dir);
                if (Build.VERSION.SDK_INT >= 30) ExitHistory.collect(app, dir);
            } catch (IOException failure) { Log.e("Stalltrace", "recover reports", failure); }
        });
        return nativeReady;
    }

    static synchronized void updateForegroundOwners(int delta) {
        foregroundOwners += delta;
        boolean required = foregroundOwners > 0;
        if (active == required) return;
        active = required;
        EPOCH.incrementAndGet();
        AnrMonitor.class.notifyAll();
    }

    private static synchronized void awaitForeground() throws InterruptedException {
        while (!active) AnrMonitor.class.wait();
    }

    static File directory(Context context) { return new File(context.getNoBackupFilesDir(), "stalltrace"); }

    private static void run(File dir, boolean nativeReady, String process) {
        String session = UUID.randomUUID().toString();
        AtomicLong ack = new AtomicLong();
        long sequence = 0;
        while (true) {
            try { awaitForeground(); } catch (InterruptedException stopped) { return; }
            long epoch = EPOCH.get(), id = ++sequence;
            long begin = SystemClock.uptimeMillis(), wall = System.currentTimeMillis();
            MAIN.post(() -> ack.set(id));
            int count = 0;
            long nextSample = begin + 1500;
            File file = new File(dir, session + "-" + id + ".jsonl");
            while (active && EPOCH.get() == epoch && ack.get() != id) {
                SystemClock.sleep(100);
                long age = SystemClock.uptimeMillis() - begin;
                if (!active || EPOCH.get() != epoch || ack.get() == id) break;
                if (SystemClock.uptimeMillis() < nextSample) continue;
                nextSample = SystemClock.uptimeMillis() + 2000;
                // Keep the opening 16 samples and two rotating 16-sample segments.
                if (count >= 16 && count % 16 == 0) {
                    file = new File(dir, session + "-" + id + "-tail" + ((count / 16) % 2) + ".jsonl");
                    if (file.exists() && !file.delete()) break;
                }
                if (count == 0) ReportStore.activeStall = session + "-" + id;
                count++;
                try {
                    List<KernelStack.Mapping> mapsBefore = nativeReady ? KernelStack.readMappings() : null;
                    long before = System.nanoTime();
                    String raw = nativeReady ? sample() : null;
                    long end = System.nanoTime(), sampleWall = System.currentTimeMillis();
                    boolean pendingAfter = ack.get() != id && EPOCH.get() == epoch;
                    JSONObject row = new JSONObject();
                    row.put("type", "stall_sample").put("session", session).put("probe", id)
                        .put("process", process).put("pid", Process.myPid()).put("probeWallMs", wall)
                        .put("probeUptimeMs", begin).put("ageMs", age)
                        .put("sampleWallMs", sampleWall)
                        .put("sampleStartMonoNs", before).put("sampleEndMonoNs", end)
                        .put("pendingAfterNative", pendingAfter);
                    if (raw != null) {
                        KernelStack capture = new KernelStack(raw, mapsBefore);
                        row.put("type", "stall_raw").put("rawNative", raw).put("mappings", capture.mappings);
                        append(file, row);
                        row.remove("rawNative"); row.remove("mappings");
                        row.put("type", "stall_sample").put("native", capture.resolve());
                    } else row.put("native", JSONObject.NULL);
                    append(file, row);
                    if (pendingAfter) {
                        JSONArray stack = new JSONArray();
                        long javaStart = System.nanoTime();
                        for (StackTraceElement frame : Looper.getMainLooper().getThread().getStackTrace())
                            stack.put(frame.toString());
                        append(file, new JSONObject().put("type", "java_sample")
                            .put("monoNs", javaStart).put("endMonoNs", System.nanoTime())
                            .put("pendingAfterJava", ack.get() != id && EPOCH.get() == epoch)
                            .put("frames", stack));
                    }
                } catch (Exception e) { Log.e("Stalltrace", "sample write", e); }
            }
            if (count > 0) {
                try {
                    append(file, new JSONObject().put("type", ack.get() == id ? "ack" : "lifecycle_end")
                        .put("wallMs", System.currentTimeMillis()).put("uptimeMs", SystemClock.uptimeMillis()));
                    ReportStore.IO.execute(() -> {
                        try { ReportStore.trimStalls(dir); } catch (IOException e) { Log.e("Stalltrace", "trim", e); }
                    });
                } catch (Exception e) { Log.e("Stalltrace", "end write", e); }
                finally { ReportStore.activeStall = null; }
            }
            SystemClock.sleep(250);
        }
    }

    private static void append(File file, JSONObject row) throws IOException, org.json.JSONException {
        row.put("schemaVersion", 1);
        try (FileOutputStream out = new FileOutputStream(file, true)) {
            out.write((row + "\n").getBytes(StandardCharsets.UTF_8));
            out.getFD().sync();
        }
    }

    private static class ExitHistory {
        static void collect(Context app, File dir) {
            try {
                ActivityManager am = (ActivityManager)app.getSystemService(Context.ACTIVITY_SERVICE);
                List<ApplicationExitInfo> history = am.getHistoricalProcessExitReasons(null, 0, 12);
                Set<String> recent = new HashSet<>();
                for (ApplicationExitInfo x : history) recent.add("exit-" + x.getTimestamp() + "-" + x.getPid());
                ReportStore.recover(dir, recent);
                for (ApplicationExitInfo x : history) {
                    if (!android.app.Application.getProcessName().equals(x.getProcessName())) continue;
                    String key = "exit-" + x.getTimestamp() + "-" + x.getPid();
                    if (ReportStore.marker(dir, key).exists()) continue;
                    File meta = new File(dir, key + ".json"), trace = new File(dir, key + ".trace");
                    boolean truncated = false;
                    // Other exit reasons can retain an earlier recovered ANR trace.
                    // Native crash streams may be protobuf tombstones; omit them from ANR collection.
                    if (!trace.exists() && x.getReason() != ApplicationExitInfo.REASON_CRASH_NATIVE) {
                        try (InputStream in = x.getTraceInputStream()) {
                            if (in != null) {
                                File tmp = new File(dir, key + ".partial");
                                try (FileOutputStream out = new FileOutputStream(tmp)) {
                                    byte[] buf = new byte[8192];
                                    int left = 2 * 1024 * 1024;
                                    while (left > 0) {
                                        int n = in.read(buf, 0, Math.min(buf.length, left));
                                        if (n < 0) break;
                                        out.write(buf, 0, n); left -= n;
                                    }
                                    truncated = left == 0 && in.read() != -1;
                                    out.getFD().sync();
                                }
                                if (!tmp.renameTo(trace)) throw new IOException("trace rename");
                            }
                        } catch (IOException e) { Log.w("Stalltrace", "trace unavailable", e); }
                    }
                    // A size at the cap can be ambiguous after interrupted metadata writes.
                    JSONObject info = new JSONObject().put("schemaVersion", 1).put("pid", x.getPid()).put("process", x.getProcessName())
                        .put("exitWallMs", x.getTimestamp()).put("reason", x.getReason())
                        .put("reasonAnr", x.getReason() == ApplicationExitInfo.REASON_ANR)
                        .put("traceFile", trace.exists() ? trace.getName() : JSONObject.NULL)
                        .put("atSizeLimit", truncated || trace.length() == 2 * 1024 * 1024);
                    File tmp = new File(dir, key + ".metadata.tmp");
                    try (FileOutputStream out = new FileOutputStream(tmp)) {
                        out.write(info.toString().getBytes(StandardCharsets.UTF_8));
                    }
                    if (!tmp.renameTo(meta)) throw new IOException("metadata rename");
                }
                ReportStore.trimExits(dir);
            } catch (Exception e) { Log.e("Stalltrace", "exit history", e); }
        }
    }
}
