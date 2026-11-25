package dev.stalltrace;

import org.json.JSONException;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Catalog and history imports share a serial worker. Only the watchdog creates live stalls. */
final class ReportStore {
    static final ExecutorService IO = Executors.newSingleThreadExecutor(r -> new Thread(r, "stalltrace-store"));
    static volatile String activeStall;
    private static final String STALL = "[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}-[0-9]+";
    private static final String EXIT = "exit-[0-9]+-[0-9]+";
    private static final Pattern ID = Pattern.compile(STALL + "|" + EXIT);
    private static final Pattern FILE = Pattern.compile(
        "(" + STALL + ")(?:-tail[01])?\\.jsonl|(" + EXIT + ")\\.(?:json|trace)");

    static final class ActiveReport extends IOException {
        ActiveReport() { super("This stall is still being recorded"); }
    }

    static List<Map<String, Object>> list(File dir) throws IOException {
        Map<String, List<String>> groups = new TreeMap<>();
        for (File file : entries(dir)) {
            Matcher match = FILE.matcher(file.getName());
            if (!match.matches() || !file.isFile()) continue;
            String id = match.group(1) == null ? match.group(2) : match.group(1);
            if (!marker(dir, id).exists()) groups.computeIfAbsent(id, unused -> new ArrayList<>()).add(file.getName());
        }
        List<Map<String, Object>> reports = new ArrayList<>();
        for (Map.Entry<String, List<String>> group : groups.entrySet()) {
            String id = group.getKey();
            List<String> files = group.getValue();
            Collections.sort(files);
            boolean exit = id.startsWith("exit-");
            Map<String, Object> row = new HashMap<>();
            row.put("id", id);
            row.put("kind", exit ? "processExit" : "stall");
            row.put("confirmedAnrExit", exit && confirmedExit(new File(dir, id + ".json")));
            row.put("active", id.equals(activeStall));
            row.put("files", files);
            reports.add(row);
        }
        return reports;
    }

    private static boolean confirmedExit(File meta) throws IOException {
        if (!meta.isFile()) return false;
        try (BufferedReader in = new BufferedReader(new InputStreamReader(
                new FileInputStream(meta), StandardCharsets.UTF_8))) {
            String json = in.readLine();
            return json != null && new JSONObject(json).optBoolean("reasonAnr", false);
        } catch (JSONException incomplete) { return false; }
    }

    private static File[] entries(File dir) throws IOException {
        if (!dir.exists()) return new File[0];
        File[] files = dir.listFiles();
        if (files == null) throw new IOException("Cannot list diagnostic reports");
        return files;
    }

    static File marker(File dir, String id) { return new File(dir, "." + id + ".deleted"); }

    static boolean delete(File dir, String id) throws IOException {
        if (id == null || !ID.matcher(id).matches()) throw new IllegalArgumentException("Invalid report ID");
        if (id.equals(activeStall)) throw new ActiveReport();
        List<File> members = members(dir, id);
        boolean found = false;
        for (File file : members) found |= file.isFile();
        File marker = marker(dir, id);
        if (!found && !marker.exists()) return false;
        if (found && !marker.exists() && !marker.createNewFile()) throw new IOException("Cannot record deletion");
        removeMembers(members);
        if (!id.startsWith("exit-")) remove(marker);
        return found;
    }

    // A visible deletion survives app death and a subsequent OS history import.
    static void recover(File dir, Set<String> recentExits) throws IOException {
        for (File file : entries(dir)) {
            String name = file.getName();
            if (!name.startsWith(".") || !name.endsWith(".deleted") || name.length() < 10) continue;
            String id = name.substring(1, name.length() - ".deleted".length());
            if (!ID.matcher(id).matches()) continue;
            removeMembers(members(dir, id));
            if (!id.startsWith("exit-") || (recentExits != null && !recentExits.contains(id))) remove(file);
        }
    }

    private static List<File> members(File dir, String id) {
        List<File> files = new ArrayList<>();
        String[] suffixes = id.startsWith("exit-") ? new String[]{".json", ".trace", ".partial", ".metadata.tmp"}
                                                  : new String[]{".jsonl", "-tail0.jsonl", "-tail1.jsonl"};
        for (String suffix : suffixes) files.add(new File(dir, id + suffix));
        return files;
    }

    private static void removeMembers(List<File> members) throws IOException {
        for (File file : members) remove(file);
    }

    private static void remove(File file) throws IOException {
        if (file.exists() && !file.delete()) throw new IOException("Cannot remove diagnostic file");
    }
}
