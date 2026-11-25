package dev.stalltrace;

import android.app.Activity;
import android.content.Context;
import androidx.lifecycle.Lifecycle;
import androidx.lifecycle.LifecycleEventObserver;
import androidx.lifecycle.LifecycleOwner;
import io.flutter.embedding.engine.plugins.FlutterPlugin;
import io.flutter.embedding.engine.plugins.activity.ActivityAware;
import io.flutter.embedding.engine.plugins.activity.ActivityPluginBinding;
import io.flutter.plugin.common.MethodCall;
import io.flutter.plugin.common.MethodChannel;
import java.io.File;
import java.io.IOException;
import java.util.HashMap;
import java.util.Map;

/** Sampling runs while at least one opted-in engine has a resumed host. */
public final class StalltracePlugin implements FlutterPlugin, ActivityAware, MethodChannel.MethodCallHandler {
    private Context context;
    private MethodChannel channel;
    private Lifecycle lifecycle;
    private boolean optedIn;
    private boolean contributing;
    private boolean unsupportedHost;
    private final LifecycleEventObserver observer = (owner, event) -> updateDemand();

    @Override public void onAttachedToEngine(FlutterPluginBinding binding) {
        context = binding.getApplicationContext();
        channel = new MethodChannel(binding.getBinaryMessenger(), "dev.stalltrace/collector");
        channel.setMethodCallHandler(this);
    }

    @Override public void onMethodCall(MethodCall call, MethodChannel.Result result) {
        if (call.method.equals("start")) {
            if (unsupportedHost) {
                result.error("unsupported_activity", "The Android activity must implement LifecycleOwner.", null);
                return;
            }
            try {
                boolean nativeReady = AnrMonitor.install(context);
                optedIn = true;
                updateDemand();
                Map<String, Object> state = new HashMap<>();
                state.put("nativeSamplingAvailable", nativeReady);
                state.put("reportsDirectory", AnrMonitor.directory(context).getAbsolutePath());
                result.success(state);
            } catch (IllegalStateException error) {
                result.error("start_failed", error.getMessage(), null);
            }
        } else if (call.method.equals("stop")) {
            optedIn = false;
            updateDemand();
            result.success(null);
        } else if (call.method.equals("listReports") || call.method.equals("deleteReport")) {
            File dir = AnrMonitor.directory(context);
            ReportStore.IO.execute(() -> {
                try {
                    if (call.method.equals("listReports")) result.success(ReportStore.list(dir));
                    else result.success(ReportStore.delete(dir, call.arguments instanceof String ? (String)call.arguments : null));
                } catch (IllegalArgumentException invalid) {
                    result.error("invalid_report_id", invalid.getMessage(), null);
                } catch (ReportStore.ActiveReport active) {
                    result.error("report_active", active.getMessage(), null);
                } catch (IOException failure) {
                    result.error("report_io", failure.getMessage(), null);
                }
            });
        } else result.notImplemented();
    }

    private void updateDemand() {
        boolean resumed = optedIn && lifecycle != null &&
                lifecycle.getCurrentState().isAtLeast(Lifecycle.State.RESUMED);
        if (resumed == contributing) return;
        contributing = resumed;
        AnrMonitor.updateForegroundOwners(resumed ? 1 : -1);
    }

    @Override public void onAttachedToActivity(ActivityPluginBinding binding) {
        Activity activity = binding.getActivity();
        unsupportedHost = !(activity instanceof LifecycleOwner);
        if (!unsupportedHost) {
            lifecycle = ((LifecycleOwner) activity).getLifecycle();
            lifecycle.addObserver(observer);
        }
        updateDemand();
    }

    @Override public void onDetachedFromActivity() {
        if (lifecycle != null) lifecycle.removeObserver(observer);
        lifecycle = null;
        unsupportedHost = false;
        updateDemand();
    }

    @Override public void onDetachedFromActivityForConfigChanges() { onDetachedFromActivity(); }
    @Override public void onReattachedToActivityForConfigChanges(ActivityPluginBinding binding) {
        onAttachedToActivity(binding);
    }
    @Override public void onDetachedFromEngine(FlutterPluginBinding binding) {
        optedIn = false;
        onDetachedFromActivity();
        channel.setMethodCallHandler(null);
        context = null;
    }
}
