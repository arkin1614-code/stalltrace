package dev.stalltrace.stalltrace_example;

import java.util.UUID;
import java.util.List;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import io.flutter.embedding.android.FlutterActivity;
import io.flutter.embedding.engine.FlutterEngine;
import io.flutter.plugin.common.MethodChannel;
import java.io.*;

public class MainActivity extends FlutterActivity {
    private volatile int pauses;

    @Override protected void onPause() {
        pauses++;
        super.onPause();
    }

    @Override protected void onCreate(android.os.Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    private static native void waitOnPlatform();
    private static native boolean reserveTestSignal();
    @Override public void configureFlutterEngine(FlutterEngine engine) {
        super.configureFlutterEngine(engine);
        InventoryStore inventory = new InventoryStore(this);
        new MethodChannel(engine.getDartExecutor().getBinaryMessenger(), "anr/lab")
            .setMethodCallHandler((call, result) -> {
                if (call.method.equals("mark")) {
                    Log.i("AnrLab", String.valueOf(call.arguments));
                    result.success(null);
                } else if (call.method.equals("inventoryEdit")) {
                    inventory.contend(Boolean.TRUE.equals(call.argument("database")),
                        Boolean.TRUE.equals(call.argument("onMain")), result);
                } else if (call.method.equals("measureIdle")) {
                    final int initialPauses = pauses;
                    final String phase = (String) call.arguments;
                    new Thread(() -> {
                        try {
                            org.json.JSONObject metrics = IdleMeasurement.measure(
                                new File(getNoBackupFilesDir(), "stalltrace"));
                            metrics.put("valid", pauses == initialPauses &&
                                Math.abs(metrics.getLong("elapsedMs")-metrics.getLong("uptimeMs")) < 1000);
                            Log.i("AnrLab", "idle " + phase + " " + metrics);
                            result.success(null);
                        } catch (Exception e) { result.error("measurement_failed", e.toString(), null); }
                    }, "lab-idle-measure").start();
                } else if (call.method.equals("reserveTestSignal")) {
                    System.loadLibrary("stalltrace_fixture");
                    result.success(reserveTestSignal());
                } else if (call.method.equals("shortPlatformStall")) {
                    android.os.SystemClock.sleep(2700);
                    result.success(null);
                } else if (call.method.equals("slowLoad")) {
                    new Handler(Looper.getMainLooper()).post(() -> {
                        System.loadLibrary("stalltrace_slow");
                        result.success(null);
                    });
                } else if (call.method.equals("platformWait")) {
                    // Run in a later Android message, when Dart has yielded to the channel.
                    new Handler(Looper.getMainLooper()).post(() -> {
                        System.loadLibrary("stalltrace_fixture");
                        waitOnPlatform();
                        result.success(null);
                    });
                } else if (call.method.equals("export")) {
                    String directory = call.argument("directory");
                    org.json.JSONArray catalog = new org.json.JSONArray((List<?>)call.argument("catalog"));
                    List<String> names = call.argument("files");
                    new Thread(() -> {
                        try {
                            File root = getExternalFilesDir(null);
                            if (root == null) throw new IOException("External storage unavailable");
                            File folder = new File(new File(root, "export"), UUID.randomUUID().toString());
                            if (!folder.isDirectory() && !folder.mkdirs()) throw new IOException("mkdir");
                            try (FileOutputStream out = new FileOutputStream(new File(folder,"catalog.json"))) {
                                out.write(catalog.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
                            }
                            for (String name : names) {
                                if (!name.equals(new File(name).getName())) throw new IOException("Invalid file name");
                                File file = new File(directory, name);
                                if (!file.isFile()) continue;
                                try (InputStream in = new FileInputStream(file);
                                     OutputStream out = new FileOutputStream(new File(folder, name))) {
                                    byte[] buf = new byte[8192];
                                    for (int n; (n = in.read(buf)) != -1;) out.write(buf, 0, n);
                                }
                            }
                            result.success(folder.getAbsolutePath());
                        } catch (IOException e) { result.error("export", e.toString(), null); }
                    }, "lab-export").start();
                } else result.notImplemented();
            });
    }
}
