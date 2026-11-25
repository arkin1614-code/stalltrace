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
        boolean nearAnrExit;
        List<String> prefix = Collections.emptyList();
    }

    // Two consecutive saved return PCs guard against shared libc waits and generic event-loop frames.
    private static final int MAX_NEIGHBOR_MS = 2500;
    private static final Pattern NATIVE =
        Pattern.compile("native: #(\\d+) pc ([0-9a-fA-F]+)\\s+.*?\\(BuildId: ([0-9a-fA-F]+)\\)");
    private static String frameKey(String id, String pc) {
        return id.toLowerCase(Locale.ROOT) + "/" + pc.toLowerCase(Locale.ROOT).replaceFirst("^0+(?!$)", "");
    }
    private static List<String> capturePrefix(String sample) {
        String[] lines = sample.split("\\n", 7);
        List<String> frames = new ArrayList<>(3);
        for (int i = 1; i < lines.length && frames.size() < 3; i++) {
            String[] words = lines[i].split(" ");
            if (words.length != 4)
                break;
            if (words[0].equals("lr"))
                continue; // LR may be scratch until verified offline with CFI.
            String kind = frames.isEmpty() ? "pc" : "call";
            if (!words[0].equals(kind) || words[2].equals("-"))
                break;
            frames.add(frameKey(words[2], words[3]));
        }
        return frames;
    }
    private static List<String> primaryPrefix(BufferedReader in, int pid) throws IOException {
        boolean main = false, target = false;
        List<String> frames = new ArrayList<>(4);
        Pattern tid = Pattern.compile("\\bsysTid=" + pid + "\\b");
        String line;
        while ((line = in.readLine()) != null) {
            if (line.startsWith("----- "))
                break;
            if (line.startsWith("\"")) {
                if (main)
                    break;
                main = line.startsWith("\"main\"");
                continue;
            }
            if (!main)
                continue;
            if (line.trim().isEmpty())
                break;
            if (tid.matcher(line).find())
                target = true;
            if (!line.contains("native: #"))
                continue;
            Matcher m = NATIVE.matcher(line);
            if (!target || !m.find() || Integer.parseInt(m.group(1)) != frames.size())
                break;
            frames.add(frameKey(m.group(3), m.group(2)));
            if (frames.size() == 4)
                break;
        }
        return frames;
    }
    private static boolean matchesFrames(List<String> sample, List<String> dump) {
        if (sample == null || sample.size() < 3 || dump.size() < 3)
            return false;
        if (!sample.get(0).split("/")[0].equals(dump.get(0).split("/")[0]))
            return false;
        for (int i = 1; i + 1 < dump.size(); i++) {
            if (sample.get(1).equals(dump.get(i)) && sample.get(2).equals(dump.get(i + 1)))
                return true;
        }
        return false;
    }
    private static final class Stall {
        final Map<String, Object> row;
        final NavigableMap<Long, Long> samples = new TreeMap<>(); // wall milliseconds -> capture monotonic ns
        final Map<Long, List<String>> prefixes = new HashMap<>();
        final List<Dump> dumps = new ArrayList<>();
        String process = "";
        int pid;
        long start = Long.MAX_VALUE, end = Long.MIN_VALUE, clockBase;
        boolean invalid, ambiguous, ended;
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
                        ended = true;
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
                    if (!r.optBoolean("pendingAfterNative")) {
                        ended = true;
                        continue;
                    }
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
                        if (type.equals("stall_sample"))
                            prefixes.put(mono, capturePrefix(nativeText));
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
                d.nearAnrExit = exit.optBoolean("reasonAnr") && exit.optLong("exitWallMs") - d.wall <= MAX_NEIGHBOR_MS;
                d.prefix = primaryPrefix(in, d.pid);
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
            if (!stall.invalid && stall.start != Long.MAX_VALUE)
                stalls.add(stall);
        }
        for (Dump dump : dumps) {
            List<Stall> candidates = new ArrayList<>();
            long latestStart = Long.MIN_VALUE;
            for (Stall s : stalls) {
                if (s.pid != dump.pid || !s.process.equals(dump.process) || s.start > dump.wall)
                    continue;
                latestStart = Math.max(latestStart, s.start);
                if (!s.samples.isEmpty() && dump.wall <= s.end)
                    candidates.add(s);
            }
            // The system may kill the process between watchdog samples. Bridge only a
            // short, frame-supported gap from the last open probe to the fatal dump.
            if (candidates.isEmpty() && dump.nearAnrExit) {
                for (Stall s : stalls) {
                    if (s.pid != dump.pid || !s.process.equals(dump.process) || s.start != latestStart ||
                        s.ended || s.samples.isEmpty())
                        continue;
                    Map.Entry<Long, Long> last = s.samples.lastEntry();
                    if (dump.wall - last.getKey() <= MAX_NEIGHBOR_MS &&
                        matchesFrames(s.prefixes.get(last.getValue()), dump.prefix))
                        candidates.add(s);
                }
            }
            for (Stall s : candidates) s.dumps.add(dump);
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
            long at = before != null ? before : after;
            if (after != null && Math.abs(after - d.wall) < Math.abs(at - d.wall))
                at = after;
            Long supported = null;
            for (Long candidate : Arrays.asList(before, after)) {
                if (candidate == null || Math.abs(candidate - d.wall) > MAX_NEIGHBOR_MS)
                    continue;
                if (!matchesFrames(s.prefixes.get(s.samples.get(candidate)), d.prefix))
                    continue;
                if (supported == null || Math.abs(candidate - d.wall) < Math.abs(supported - d.wall))
                    supported = candidate;
            }
            if (supported != null)
                at = supported;
            s.row.put("sampleSelection", supported != null ? "matchingFrames" : "nearestTime");
            s.row.put("traceAssociation", "matched");
            s.row.put("exitReportId", d.id);
            s.row.put("traceWallMs", d.wall);
            s.row.put("sampleDeltaMs", at - d.wall);
            s.row.put("sampleMonoNs", s.samples.get(at));
        }
    }
}
