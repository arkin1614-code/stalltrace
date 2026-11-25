package dev.stalltrace;

import org.junit.After;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import static org.junit.Assert.*;

public class ReportStoreTest {
    @Rule public TemporaryFolder temp = new TemporaryFolder();
    private static final String STALL = "00000000-0000-0000-0000-000000000001-7";
    private static final String EXIT = "exit-12345-96";

    private File write(String name, String body) throws IOException {
        File file = new File(temp.getRoot(), name);
        Files.write(file.toPath(), body.getBytes(StandardCharsets.UTF_8));
        return file;
    }
    @After public void resetActive() { ReportStore.activeStall = null; }

    @Test public void preservesNonAddressLinkRegisterBits() {
        assertEquals(-1L,KernelStack.hex("ffffffffffffffff"));
        assertEquals(0x123456789abL,KernelStack.hex("123456789ab"));
    }

    @Test public void listsLegacySegmentsAndConfirmedExits() throws Exception {
        write(STALL + ".jsonl", "{}\n"); write(STALL + "-tail0.jsonl", "{}\n");
        write(EXIT + ".json", "{\"reasonAnr\":true}"); write(EXIT + ".trace", "trace");
        write("unknown.json", "{}"); write(EXIT + ".partial", "unfinished");
        List<Map<String,Object>> rows = ReportStore.list(temp.getRoot());
        assertEquals(2, rows.size());
        assertEquals(2, ((List<?>)rows.get(0).get("files")).size());
        assertEquals(false, rows.get(0).get("confirmedAnrExit"));
        assertEquals(true, rows.get(1).get("confirmedAnrExit"));
        assertEquals(2, ((List<?>)rows.get(1).get("files")).size());
    }

    @Test public void refusesActiveOrInvalidIdsAndDeletesAllSegments() throws Exception {
        File main = write(STALL + ".jsonl", ""), tail = write(STALL + "-tail1.jsonl", "");
        ReportStore.activeStall = STALL;
        assertThrows(ReportStore.ActiveReport.class, () -> ReportStore.delete(temp.getRoot(), STALL));
        assertThrows(IllegalArgumentException.class, () -> ReportStore.delete(temp.getRoot(), "../" + STALL));
        assertTrue(main.exists());
        ReportStore.activeStall = null;
        assertTrue(ReportStore.delete(temp.getRoot(), STALL));
        assertFalse(tail.exists()); assertFalse(main.exists());
        assertFalse(ReportStore.delete(temp.getRoot(), STALL));
    }

    @Test public void resumesInterruptedDeletionAndSuppressesHistoricalReimport() throws Exception {
        File trace = write(EXIT + ".trace", "trace");
        File marker = ReportStore.marker(temp.getRoot(), EXIT);
        assertTrue(marker.createNewFile());
        assertTrue(ReportStore.list(temp.getRoot()).isEmpty());
        ReportStore.recover(temp.getRoot(), Collections.singleton(EXIT));
        assertFalse(trace.exists()); assertTrue(marker.exists());
        ReportStore.recover(temp.getRoot(), Collections.emptySet());
        assertFalse(marker.exists());
    }

    @Test public void recoveredAnrTraceAloneDoesNotChangeExitReason() throws Exception {
        write(EXIT + ".json", "{\"reasonAnr\":false}"); write(EXIT + ".trace", "trace");
        assertEquals(false, ReportStore.list(temp.getRoot()).get(0).get("confirmedAnrExit"));
        assertTrue(ReportStore.delete(temp.getRoot(), EXIT));
        assertTrue(ReportStore.list(temp.getRoot()).isEmpty());
        assertTrue(ReportStore.marker(temp.getRoot(), EXIT).exists());
    }

    @Test public void trimRetainsOrEvictsEverySegmentTogether() throws Exception {
        for (String suffix : new String[]{".jsonl", "-tail0.jsonl", "-tail1.jsonl"})
            assertTrue(write(STALL+suffix, "{}").setLastModified(1000));
        for (int i=0; i<24; i++)
            assertTrue(write(UUID.randomUUID()+"-1.jsonl","{}").setLastModified(2000+i*1000));
        ReportStore.activeStall = STALL;
        ReportStore.trimStalls(temp.getRoot());
        assertTrue(new File(temp.getRoot(),STALL+".jsonl").isFile());
        assertEquals(24,ReportStore.list(temp.getRoot()).size());
        ReportStore.activeStall = null;
        write(UUID.randomUUID()+"-1.jsonl","{}");
        ReportStore.trimStalls(temp.getRoot());
        for (String suffix : new String[]{".jsonl","-tail0.jsonl","-tail1.jsonl"})
            assertFalse(new File(temp.getRoot(),STALL+suffix).exists());
    }
}
