package dev.stalltrace.example;

import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

import io.flutter.embedding.android.FlutterActivity;
import io.flutter.embedding.engine.FlutterEngine;
import io.flutter.plugin.common.MethodChannel;

public class MainActivity extends FlutterActivity {
    @Override
    public void configureFlutterEngine(FlutterEngine engine) {
        super.configureFlutterEngine(engine);
        new MethodChannel(engine.getDartExecutor().getBinaryMessenger(), "stalltrace_example/scenarios")
            .setMethodCallHandler((call, result) -> {
                if (call.method.equals("waitForLock")) {
                    startLockContention(result);
                } else {
                    result.notImplemented();
                }
            });
    }

    private void startLockContention(MethodChannel.Result result) {
        Object lock = new Object();
        Handler main = new Handler(Looper.getMainLooper());
        new Thread(() -> {
            synchronized (lock) {
                // Schedule the main-thread waiter only after the worker owns the lock.
                main.post(() -> waitForWorker(lock, result));
                SystemClock.sleep(18_000);
            }
        }, "example-lock-owner").start();
    }

    private void waitForWorker(Object lock, MethodChannel.Result result) {
        synchronized (lock) {
            result.success(null);
        }
    }
}
