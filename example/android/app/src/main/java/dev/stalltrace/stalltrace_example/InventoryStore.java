package dev.stalltrace.stalltrace_example;

import android.content.Context;
import android.database.DatabaseUtils;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.os.SystemClock;
import android.util.Log;
import io.flutter.plugin.common.MethodChannel;
import java.util.concurrent.CompletableFuture;
import java.util.function.IntSupplier;
/** Example-only contention between a stock sync and a user saving an edit. */
final class InventoryStore extends SQLiteOpenHelper {
    private final Object inventoryLock = new Object();
    private final Handler main = new Handler(Looper.getMainLooper());
    private int stockRevision;

    InventoryStore(Context context) {
        super(context.getApplicationContext(), "scenario-stock.db", null, 1);
    }

    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE stock(sku INTEGER PRIMARY KEY, quantity INTEGER NOT NULL)");
        db.execSQL("INSERT INTO stock VALUES(1, 0)");
    }

    @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        throw new IllegalStateException("Unexpected fixture schema");
    }

    private void startEdit(boolean database, boolean onMain, CompletableFuture<Integer> result) {
        // Both variants perform the same edit. Only the thread assigned to the caller changes.
        new Thread(() -> {
            try {
                if (database) {
                    SQLiteDatabase db = getWritableDatabase();
                    db.beginTransaction();
                    try {
                        db.execSQL("UPDATE stock SET quantity=0 WHERE sku=1");
                        submitEdit(() -> saveStockQuantity(db), onMain, result);
                        SystemClock.sleep(16000);
                        db.setTransactionSuccessful();
                    } finally {
                        db.endTransaction();
                    }
                } else {
                    syncInventory(onMain, result);
                }
            } catch (RuntimeException failure) {
                result.completeExceptionally(failure);
            }
        }, "inventory-sync").start();
    }

    private void syncInventory(boolean onMain, CompletableFuture<Integer> result) {
        synchronized (inventoryLock) {
            stockRevision = 0;
            submitEdit(this::applyPriceUpdate, onMain, result);
            SystemClock.sleep(16000);
        }
    }

    private int applyPriceUpdate() {
        synchronized (inventoryLock) {
            return ++stockRevision;
        }
    }

    private int saveStockQuantity(SQLiteDatabase db) {
        db.execSQL("UPDATE stock SET quantity=quantity+1 WHERE sku=1");
        return (int) DatabaseUtils.longForQuery(db, "SELECT quantity FROM stock WHERE sku=1", null);
    }

    private void submitEdit(IntSupplier edit, boolean onMain, CompletableFuture<Integer> result) {
        Log.i("ScenarioLab", "holder tid=" + Process.myTid());
        Runnable caller = () -> {
            Log.i("ScenarioLab", "writer tid=" + Process.myTid());
            try {
                result.complete(edit.getAsInt());
            } catch (RuntimeException failure) {
                result.completeExceptionally(failure);
            }
        };
        if (onMain) main.post(caller);
        else new Thread(caller, "inventory-edit").start();
    }

    void contend(boolean database, boolean onMain, MethodChannel.Result reply) {
        CompletableFuture<Integer> result = new CompletableFuture<>();
        result.whenComplete((value, error) -> {
            if (error == null) reply.success(value);
            else reply.error("edit_failed", error.toString(), null);
        });
        startEdit(database, onMain, result);
    }

    void waitAtTwoLines() {
        Log.i("ScenarioLab", "writer tid=" + Process.myTid());
        SystemClock.sleep(5550);
        Log.i("ScenarioLab", "phase second tid=" + Process.myTid());
        SystemClock.sleep(9000);
    }
}
