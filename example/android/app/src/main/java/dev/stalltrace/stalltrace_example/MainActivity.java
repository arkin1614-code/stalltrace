package dev.stalltrace.stalltrace_example;

import java.util.UUID;
import android.util.Log;
import io.flutter.embedding.android.FlutterActivity;
import io.flutter.embedding.engine.FlutterEngine;
import io.flutter.plugin.common.MethodChannel;
import java.io.*;

public class MainActivity extends FlutterActivity {
    @Override public void configureFlutterEngine(FlutterEngine engine) {
        super.configureFlutterEngine(engine);
        new MethodChannel(engine.getDartExecutor().getBinaryMessenger(), "anr/lab")
            .setMethodCallHandler((call, result) -> {
                if (call.method.equals("mark")) {
                    Log.i("AnrLab", String.valueOf(call.arguments));
                    result.success(null);
                } else if (call.method.equals("export") && call.arguments instanceof String) {
                    new Thread(() -> {
                        try {
                            File root = getExternalFilesDir(null);
                            if (root == null) throw new IOException("External storage unavailable");
                            File folder = new File(new File(root, "export"), UUID.randomUUID().toString());
                            if (!folder.isDirectory() && !folder.mkdirs()) throw new IOException("mkdir");
                            File[] files = new File((String)call.arguments).listFiles();
                            if (files == null) throw new IOException("list");
                            for (File file : files) {
                                String name = file.getName();
                                if (!(name.endsWith(".jsonl") || name.endsWith(".json") || name.endsWith(".trace"))) continue;
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
