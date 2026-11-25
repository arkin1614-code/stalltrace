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
