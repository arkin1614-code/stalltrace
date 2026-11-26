package dev.stalltrace;

import static org.junit.Assert.*;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import org.json.JSONObject;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class TraceLinksTest {
    @Rule public TemporaryFolder folder = new TemporaryFolder();
    private static final long T = 1704067200000L;
    private static final String EXIT = "exit-1704067210000-7";
    private static final String PREFIX = "00000000-0000-0000-0000-000000000001-";

    private void write(String name, String data) throws Exception {
        Files.write(new File(folder.getRoot(), name).toPath(), data.getBytes(StandardCharsets.UTF_8));
    }
    private void stall(int probe, long start, long stop) throws Exception {
        long mono = (start + 2500) * 1000000;
        JSONObject sample = new JSONObject()
                                .put("type", "stall_sample")
                                .put("pid", 7)
                                .put("process", "app")
                                .put("probeWallMs", T + start)
                                .put("probeUptimeMs", 1000 + start)
                                .put("sampleWallMs", T + start + 1500)
                                .put("sampleStartMonoNs", mono - 10)
                                .put("sampleEndMonoNs", mono)
                                .put("pendingAfterNative", true)
                                .put("native", mono + " 7 0\npc libc.so aa 0\n");
        JSONObject end =
            new JSONObject().put("type", "ack").put("wallMs", T + stop).put("uptimeMs", 1000 + stop);
        write(PREFIX + probe + ".jsonl", sample + "\n" + end + "\n");
    }
    private void dump(String primary) throws Exception {
        write(EXIT + ".json",
            new JSONObject()
                .put("process", "app")
                .put("pid", 7)
                .put("exitWallMs", T + 10000)
                .put("reasonAnr", false)
                .toString());
        write(EXIT + ".trace",
            "Subject: Input dispatching timed out (window)\n\n"
                + "----- pid 7 at 2024-01-01 00:00:04.100000000+0000 -----\nCmd line: " + primary + "\n");
    }
    private Map<String, Object> row(int n) throws Exception {
        return ReportStore.list(folder.getRoot())
            .stream()
            .filter(r -> r.get("id").equals(PREFIX + n))
            .findFirst()
            .get();
    }
    @Test
    public void recoveredTraceIdentifiesOnlyOneOfTwoStalls() throws Exception {
        stall(1, 0, 2000);
        stall(2, 3000, 6000);
        dump("app");
        assertEquals("none", row(1).get("traceAssociation"));
        Map<String, Object> linked = row(2);
        assertEquals("matched", linked.get("traceAssociation"));
        assertEquals(EXIT, linked.get("exitReportId"));
        assertEquals(400L, linked.get("sampleDeltaMs"));
        assertEquals(5500000000L, linked.get("sampleMonoNs"));
    }
    @Test
    public void selectsEarlierDartStackAcrossNativeTransition() throws Exception {
        stall(1, 0, 7000);
        File samples = new File(folder.getRoot(), PREFIX + "1.jsonl");
        JSONObject row = new JSONObject(Files.readAllLines(samples.toPath()).get(0));
        StringBuilder data = new StringBuilder();
        long[] offsets = {2731, 4821}; // 1369 ms before vs. 721 ms after the dump
        String[] stacks = {
            "pc libapp.so aa 37\nlr libapp.so aa ff\ncall libapp.so aa 22\ncall libapp.so aa 33",
            "pc libc.so cc 44\ncall libfixture.so dd 55\ncall libapp.so aa 66"};
        for (int i = 0; i < offsets.length; i++) {
            long mono = (offsets[i] + 1000) * 1000000;
            row.put("sampleWallMs", T + offsets[i])
                .put("sampleStartMonoNs", mono - 1)
                .put("sampleEndMonoNs", mono)
                .put("native", mono + " 7 0\n" + stacks[i] + "\n");
            data.append(row).append('\n');
        }
        write(samples.getName(), data.toString());
        dump("app");
        String main = "\"main\" prio=5\n  | sysTid=7\n  native: #00 pc 0038 a (BuildId: aa)\n"
            + "  native: #01 pc 0022 a (BuildId: aa)\n  native: #02 pc 0033 a (BuildId: aa)\n\n";
        Files.write(new File(folder.getRoot(), EXIT + ".trace").toPath(),
            main.getBytes(StandardCharsets.UTF_8), java.nio.file.StandardOpenOption.APPEND);
        assertEquals("matchingFrames", row(1).get("sampleSelection"));
        assertEquals(-1369L, row(1).get("sampleDeltaMs"));
        dump("app");
        assertEquals("nearestTime", row(1).get("sampleSelection"));
        assertEquals(721L, row(1).get("sampleDeltaMs"));
    }
    @Test
    public void deeperReturnAddressCanSeparateTwoCallsIntoTheSameFfiFunction() throws Exception {
        stall(1, 0, 7000);
        File file = new File(folder.getRoot(), PREFIX + "1.jsonl");
        JSONObject sample = new JSONObject(Files.readAllLines(file.toPath()).get(0));
        StringBuilder rows = new StringBuilder();
        for (long at : new long[]{2750, 4750}) {
            long mono = (1000 + at) * 1_000_000;
            String site = at == 2750 ? "aa" : "bb";
            sample.put("sampleWallMs", T + at).put("sampleStartMonoNs", mono - 10).put("sampleEndMonoNs", mono)
                .put("native", mono + " 7 0\npc libc.so cc 0\nlr libffi.so dd ff\ncall libffi.so dd 1\ncall libapp.so ee 2"
                    + "\ncall libapp.so ee 3\ncall libapp.so ee " + site + "\ncall libapp.so ee ee\ncall libapp.so ee ff");
            rows.append(sample).append('\n');
        }
        write(file.getName(), rows.toString());
        dump("app");
        String main = "\"main\" prio=5 tid=1\n | sysTid=7\n native: #00 pc 0 c (BuildId: cc)\n"
            + " native: #01 pc ff d (BuildId: dd)\n native: #02 pc 1 d (BuildId: dd)\n"
            + " native: #03 pc 2 e (BuildId: ee)\n native: #04 pc 3 e (BuildId: ee)\n native: #05 pc aa e (BuildId: ee)\n";
        Files.write(new File(folder.getRoot(), EXIT + ".trace").toPath(), main.getBytes(StandardCharsets.UTF_8),
            java.nio.file.StandardOpenOption.APPEND);
        assertEquals("matchingFrames", row(1).get("sampleSelection"));
        assertEquals(-1350L, row(1).get("sampleDeltaMs"));
        // The nearer walk ended at that depth; it never observed a contradictory return address.
        write(file.getName(), rows.toString().replace("\\ncall libapp.so ee bb\\ncall libapp.so ee ee\\ncall libapp.so ee ff", ""));
        assertEquals(650L, row(1).get("sampleDeltaMs"));
        // A deeper suffix cannot rescue a mismatch at the first two saved calls.
        write(file.getName(), rows.toString().replace("libffi.so dd 1","libffi.so ff 1"));
        assertEquals("nearestTime", row(1).get("sampleSelection"));
    }
    private static final String FIRST = "app.Work.run(Work.java:12)";
    private static final String SECOND = "app.Work.run(Work.java:18)";
    private static final String CALLER = "app.Edit.save(Edit.java:7)";
    private void javaNeighbors(String first, String second, String caller, String dumpFrame,
                               boolean pending, long javaDurationNs, boolean split) throws Exception {
        stall(1, 0, 7000);
        File file = new File(folder.getRoot(), PREFIX + "1.jsonl");
        JSONObject sample = new JSONObject(Files.readAllLines(file.toPath()).get(0))
            .put("session", PREFIX.substring(0, PREFIX.length() - 1)).put("probe", 1);
        StringBuilder records = new StringBuilder();
        for (long at : new long[]{2750, 4750}) {
            long mono = (1000 + at) * 1_000_000;
            sample.put("sampleWallMs", T + at).put("sampleStartMonoNs", mono - 1)
                .put("sampleEndMonoNs", mono).put("native", mono + " 7 0\npc libc.so aa 0");
            JSONObject java = new JSONObject().put("type", "java_sample").put("monoNs", mono + 1000)
                .put("endMonoNs", mono + 1000 + javaDurationNs).put("pendingAfterJava", pending)
                .put("frames", new org.json.JSONArray(Arrays.asList(at == 2750 ? first : second, caller)));
            records.append(sample).append('\n');
            if (split) write(PREFIX + "1-tail0.jsonl", java + "\n");
            else records.append(java).append('\n');
        }
        write(file.getName(), records.toString());
        dump("app");
        Files.write(new File(folder.getRoot(), EXIT + ".trace").toPath(),
            ("\"main\" prio=5 tid=1\n | sysTid=7\n  at " + dumpFrame + "\n  at " + caller + "\n\n")
                .getBytes(StandardCharsets.UTF_8), java.nio.file.StandardOpenOption.APPEND);
    }
    @Test
    public void choosesFartherJavaObservationAtTheSameLine() throws Exception {
        javaNeighbors(FIRST, SECOND, CALLER, FIRST, true, 10, false);
        assertEquals("matchingJavaSite", row(1).get("sampleSelection"));
        assertEquals(-1350L, row(1).get("sampleDeltaMs"));
        javaNeighbors(FIRST, FIRST, CALLER, FIRST, true, 10, false);
        assertEquals(650L, row(1).get("sampleDeltaMs"));
        String r8 = "a.b(r8-map-id-1234abcd:11)";
        javaNeighbors(r8, r8.replace(":11", ":12"), CALLER, r8, true, 10, false);
        assertEquals(-1350L, row(1).get("sampleDeltaMs"));
    }
    @Test
    public void refusesLateRecoveredCrossSegmentAndUnpositionedJavaEvidence() throws Exception {
        javaNeighbors(FIRST, SECOND, CALLER, FIRST, false, 10, false);
        assertEquals("nearestTime", row(1).get("sampleSelection"));
        javaNeighbors(FIRST, SECOND, CALLER, FIRST, true, 300_000_000, false);
        assertEquals("nearestTime", row(1).get("sampleSelection"));
        javaNeighbors(FIRST, SECOND, CALLER, FIRST, true, 10, true);
        assertEquals("nearestTime", row(1).get("sampleSelection"));
        String loop = "android.os.Looper.loop(Looper.java:12)";
        javaNeighbors(loop, loop, loop, loop, true, 10, false);
        assertEquals("nearestTime", row(1).get("sampleSelection"));
        javaNeighbors(FIRST, FIRST, CALLER, SECOND, true, 10, false);
        assertEquals("nearestTime", row(1).get("sampleSelection"));
        String missing = "app.Work.run(Unknown Source)";
        javaNeighbors(missing, missing, CALLER, missing, true, 10, false);
        assertEquals("nearestTime", row(1).get("sampleSelection"));
    }
    @Test
    public void fatalGapRequiresTheLastOpenSampleAndItsOwnPositionedJavaObservation() throws Exception {
        javaNeighbors(FIRST, SECOND, CALLER, FIRST, true, 10, false);
        File file = new File(folder.getRoot(), PREFIX + "1.jsonl");
        List<String> lines = Files.readAllLines(file.toPath());
        write(file.getName(), lines.get(0) + "\n" + lines.get(1) + "\n");
        JSONObject meta = new JSONObject().put("process","app").put("pid",7)
            .put("exitWallMs", T + 4500).put("reasonAnr",true);
        write(EXIT + ".json", meta.toString());
        assertEquals("matchingJavaSite", row(1).get("sampleSelection"));
        write(EXIT + ".json", meta.put("reasonAnr",false).toString());
        assertEquals("none", row(1).get("traceAssociation"));
        write(EXIT + ".json", meta.put("reasonAnr",true).toString());
        write(file.getName(), lines.get(0) + "\n" + lines.get(1).replace(FIRST, SECOND) + "\n");
        assertEquals("none", row(1).get("traceAssociation"));
        write(file.getName(), lines.get(0) + "\n" + lines.get(1) + "\n{\"type\":\"ack\"}\n");
        assertEquals("none", row(1).get("traceAssociation"));
    }
    @Test
    public void fatalDumpJustAfterLastSampleNeedsOpenProbeAndMatchingFrames() throws Exception {
        stall(1, 7000, 8800);
        File samples = new File(folder.getRoot(), PREFIX + "1.jsonl");
        List<String> lines = Files.readAllLines(samples.toPath());
        String open = lines.get(0).replace("pc libc.so aa 0", "pc libc.so aa 0\\ncall a bb 12\\ncall a bb 34") + "\n";
        write(samples.getName(), open);
        dump("app");
        File trace = new File(folder.getRoot(), EXIT + ".trace");
        String header = String.join("\n", Files.readAllLines(trace.toPath())).replace("00:00:04.100", "00:00:09.100");
        String main = "\n\"main\" prio=5\n | sysTid=7\n native: #00 pc 00 a (BuildId: aa)\n"
            + " native: #01 pc 12 a (BuildId: bb)\n native: #02 pc 34 a (BuildId: bb)\n\n";
        write(trace.getName(), header + main);
        assertEquals("none", row(1).get("traceAssociation")); // Recovered ANR in a different exit.
        JSONObject exit = new JSONObject().put("process", "app").put("pid", 7)
            .put("exitWallMs", T + 10000).put("reasonAnr", true);
        write(EXIT + ".json", exit.toString());
        assertEquals("matchingFrames", row(1).get("sampleSelection"));
        assertEquals(-600L, row(1).get("sampleDeltaMs"));
        write(EXIT + ".json", exit.put("exitWallMs", T + 11601).toString());
        assertEquals("none", row(1).get("traceAssociation")); // Death too long after the dump.
        write(EXIT + ".json", exit.put("exitWallMs", T + 14000).toString());
        write(trace.getName(), header.replace("09.100", "12.000") + main);
        assertEquals("none", row(1).get("traceAssociation")); // Latest sample too old.
        write(EXIT + ".json", exit.put("exitWallMs", T + 10000).toString());
        write(trace.getName(), header + main.replace("pc 34", "pc 35"));
        assertEquals("none", row(1).get("traceAssociation"));
        write(trace.getName(), header + main);
        write(samples.getName(), open + lines.get(1) + "\n");
        assertEquals("none", row(1).get("traceAssociation"));
        write(samples.getName(), open);
        stall(2, 8900, 11000);
        assertEquals("none", row(1).get("traceAssociation")); // Another probe already started.
        assertEquals("matched", row(2).get("traceAssociation"));
    }
    @Test
    public void overlapsStayAmbiguousAndProcessMismatchStaysUnlinked() throws Exception {
        stall(1, 0, 6000);
        stall(2, 3000, 6000);
        dump("app");
        assertEquals("ambiguous", row(1).get("traceAssociation"));
        assertEquals("ambiguous", row(2).get("traceAssociation"));
        dump("other");
        assertEquals("none", row(2).get("traceAssociation"));
    }
}
