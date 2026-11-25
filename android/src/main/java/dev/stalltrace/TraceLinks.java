package dev.stalltrace;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.text.ParsePosition;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.json.JSONException;
import org.json.JSONObject;

/** Conservative input-ANR links. Only the primary process dump is evidence for the subject. */
final class TraceLinks {
    private static final Pattern HEADER = Pattern.compile(
        "^----- pid (\\d+) at (\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2})\\.(\\d{3,9})([+-]\\d{4}) -----$");
    private static final class Dump {
        String id, process;
        int pid;
        long wall;
    }
    private static final class Stall {
        final Map<String, Object> row;
        final NavigableMap<Long, Long> samples = new TreeMap<>(); // wall milliseconds -> capture monotonic ns
        final List<Dump> dumps = new ArrayList<>();
        String process = "";
        int pid;
        long start = Long.MAX_VALUE, end = Long.MIN_VALUE, clockBase;
        boolean invalid, ambiguous;
        Stall(Map<String, Object> row) {
            this.row = row;
        }
        void read(File file) throws IOException {
            try (BufferedReader in = reader(file)) {
                String line;
                while ((line = in.readLine()) != null) {
                    JSONObject r;
                    try {
                        r = new JSONObject(line);
                    } catch (JSONException partial) {
                        continue;
                    }
                    String type = r.optString("type");
                    if (type.equals("ack") || type.equals("lifecycle_end")) {
                        long wall = r.optLong("wallMs"), up = r.optLong("uptimeMs");
                        if (wall > 0 && up > 0) {
                            clock(wall - up);
                            end = Math.max(end, wall);
                        }
                        continue;
                    }
                    if (!type.equals("stall_sample") && !type.equals("stall_raw"))
                        continue;
                    String name = r.optString("process");
                    int id = r.optInt("pid");
                    long begin = r.optLong("probeWallMs"), up = r.optLong("probeUptimeMs");
                    if (name.isEmpty() || id <= 0 || begin <= 0 || up <= 0) {
                        invalid = true;
                        continue;
                    }
                    if (pid != 0 && (pid != id || !process.equals(name)))
                        invalid = true;
                    pid = id;
                    process = name;
                    start = Math.min(start, begin);
                    clock(begin - up);
                    if (!r.optBoolean("pendingAfterNative"))
                        continue;
                    long stamp = r.optLong("sampleWallMs"), endNs = r.optLong("sampleEndMonoNs");
                    clock(stamp - endNs / 1000000);
                    String nativeText = r.optString(type.equals("stall_raw") ? "rawNative" : "native");
                    int space = nativeText.indexOf(' ');
                    if (space < 1)
                        continue;
                    try {
                        long mono = Long.parseLong(nativeText.substring(0, space));
                        if (mono < r.optLong("sampleStartMonoNs") || mono > endNs)
                            continue;
                        long at = stamp - (endNs - mono) / 1000000;
                        samples.put(at, mono);
                        end = Math.max(end, at);
                    } catch (NumberFormatException ignored) {
                    }
                }
            } catch (FileNotFoundException rotated) {
                invalid = true;
            }
        }
        void clock(long base) {
            if (clockBase != 0 && Math.abs(base - clockBase) > 250)
                invalid = true;
            clockBase = base;
        }
    }
    private static BufferedReader reader(File file) throws IOException {
        return new BufferedReader(new InputStreamReader(new FileInputStream(file), StandardCharsets.UTF_8));
    }
    private static Dump readDump(File dir, String id) throws IOException {
        File info = new File(dir, id + ".json"), trace = new File(dir, id + ".trace");
        if (!info.isFile() || !trace.isFile())
            return null;
        try (BufferedReader meta = reader(info); BufferedReader in = reader(trace)) {
            String json = meta.readLine();
            if (json == null)
                return null;
            JSONObject exit = new JSONObject(json);
            boolean input = false;
            String line;
            while ((line = in.readLine()) != null) {
                if (line.startsWith("Subject: Input dispatching timed out"))
                    input = true;
                if (!line.startsWith("----- pid "))
                    continue;
                Matcher m = HEADER.matcher(line);
                if (!input || !m.matches())
                    return null;
                Dump d = new Dump();
                d.pid = Integer.parseInt(m.group(1));
                d.id = id;
                d.process = exit.optString("process");
                if (d.process.isEmpty() || d.pid != exit.optInt("pid")
                    || !("Cmd line: " + d.process).equals(in.readLine()))
                    return null;
                String stamp = m.group(2) + "." + m.group(3).substring(0, 3) + m.group(4);
                SimpleDateFormat format = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSSZ", Locale.ROOT);
                format.setLenient(false);
                Date date = format.parse(stamp, new ParsePosition(0));
                if (date == null || date.getTime() > exit.optLong("exitWallMs"))
                    return null;
                d.wall = date.getTime();
                return d;
            }
        } catch (JSONException | NumberFormatException malformed) {
            return null;
        }
        return null;
    }

    @SuppressWarnings("unchecked")
    static void attach(File dir, List<Map<String, Object>> rows) throws IOException {
        List<Dump> dumps = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            row.put("traceAssociation", "none");
            if (row.get("kind").equals("processExit")) {
                Dump dump = readDump(dir, (String) row.get("id"));
                if (dump != null)
                    dumps.add(dump);
            }
        }
        if (dumps.isEmpty())
            return;
        List<Stall> stalls = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            if (!row.get("kind").equals("stall"))
                continue;
            Stall stall = new Stall(row);
            for (String name : (List<String>) row.get("files")) stall.read(new File(dir, name));
            if (!stall.invalid && !stall.samples.isEmpty())
                stalls.add(stall);
        }
        for (Dump dump : dumps) {
            List<Stall> candidates = new ArrayList<>();
            for (Stall s : stalls) {
                if (s.pid == dump.pid && s.process.equals(dump.process) && s.start <= dump.wall
                    && dump.wall <= s.end) {
                    candidates.add(s);
                    s.dumps.add(dump);
                }
            }
            if (candidates.size() > 1)
                for (Stall s : candidates) s.ambiguous = true;
        }
        for (Stall s : stalls) {
            if (s.dumps.isEmpty())
                continue;
            if (s.ambiguous || s.dumps.size() != 1) {
                s.row.put("traceAssociation", "ambiguous");
                continue;
            }
            Dump d = s.dumps.get(0);
            Long before = s.samples.floorKey(d.wall), after = s.samples.ceilingKey(d.wall);
            long at = before == null                                 ? after
                : after == null || d.wall - before <= after - d.wall ? before
                                                                     : after;
            s.row.put("traceAssociation", "matched");
            s.row.put("exitReportId", d.id);
            s.row.put("traceWallMs", d.wall);
            s.row.put("sampleDeltaMs", at - d.wall);
            s.row.put("sampleMonoNs", s.samples.get(at));
        }
    }
}
