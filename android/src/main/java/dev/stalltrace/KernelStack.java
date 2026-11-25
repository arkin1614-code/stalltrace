package dev.stalltrace;

import android.system.ErrnoException;
import android.system.Os;
import android.system.StructStat;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.*;
import org.json.JSONArray;

/** Sample-local kernel mappings. Resolves file-backed AArch64 ELF segments without taking linker locks. */
final class KernelStack {
    static final class Mapping {
        final long lo, hi, offset, inode;
        final String permissions, device, path;
        Mapping(String[] fields) {
            String[] range = fields[0].split("-");
            lo = hex(range[0]);
            hi = hex(range[1]);
            permissions = fields[1];
            offset = hex(fields[2]);
            device = fields[3];
            inode = Long.parseLong(fields[4]);
            path = fields.length == 6 ? fields[5] : "";
        }
        String name() {
            return path.substring(path.lastIndexOf('/') + 1).replaceAll("\\s", "_");
        }
        boolean sameFile(Mapping other) {
            return inode != 0 && inode == other.inode && device.equals(other.device)
                && path.equals(other.path);
        }
    }
    final JSONArray mappings = new JSONArray();
    private final List<Mapping> maps;
    private final List<Mapping> targets = new ArrayList<>();
    private final String[] lines;
    // LR need not contain a user address. Preserve all 64 bits even on API 24.
    static long hex(String s) {
        if (s.length() == 16)
            return (Long.parseLong(s.substring(0, 8), 16) << 32) | Long.parseLong(s.substring(8), 16);
        return Long.parseLong(s, 16);
    }

    static List<Mapping> readMappings() {
        List<Mapping> list = new ArrayList<>();
        try (BufferedReader in = new BufferedReader(new FileReader("/proc/self/maps"))) {
            String line;
            while ((line = in.readLine()) != null) {
                try {
                    list.add(new Mapping(line.trim().split("\\s+", 6)));
                } catch (NumberFormatException malformed) { /* Ignore an unrepresentable address. */
                }
            }
        } catch (IOException unavailable) { /* Raw PCs remain available without mappings. */
        }
        return list;
    }

    private static Mapping executableAt(List<Mapping> list, long address) {
        for (Mapping m : list) {
            if (address >= m.lo && address < m.hi && m.permissions.indexOf('x') >= 0)
                return m;
        }
        return null;
    }

    KernelStack(String raw, List<Mapping> before) {
        lines = raw.split("\n");
        maps = readMappings();
        Set<Mapping> recorded = new HashSet<>();
        for (int i = 1; i < lines.length; i++) {
            long address = hex(lines[i].split(" ")[1]);
            Mapping match = executableAt(maps, address);
            Mapping previous = executableAt(before, address);
            boolean stable = match != null && previous != null && match.sameFile(previous)
                && match.lo == previous.lo && match.hi == previous.hi && match.offset == previous.offset;
            targets.add(stable ? match : null);
            if (match != null && recorded.add(match)) {
                mappings.put(Long.toHexString(match.lo) + "-" + Long.toHexString(match.hi) + " "
                    + Long.toHexString(match.offset) + " " + match.device + " " + match.inode + " "
                    + (stable ? "stable " : "unverified ") + match.name());
            }
        }
    }

    String resolve() {
        StringBuilder out = new StringBuilder(lines[0]).append('\n');
        Map<Mapping, Image> images = new HashMap<>();
        for (int i = 1; i < lines.length; i++) {
            String[] frame = lines[i].split(" ");
            Mapping m = targets.get(i - 1);
            if (m != null && !images.containsKey(m))
                images.put(m, image(m));
            Image image = images.get(m);
            long fileOffset = m == null ? 0 : m.offset + hex(frame[1]) - m.lo;
            out.append(frame[0]).append(' ');
            if (image == null || fileOffset < image.start || fileOffset >= image.end)
                out.append("? - 0");
            else
                out.append(image.name)
                    .append(' ')
                    .append(image.id)
                    .append(' ')
                    .append(Long.toHexString(image.vaddr + fileOffset - image.start));
            out.append('\n');
        }
        return out.toString();
    }

    private Image image(Mapping target) {
        if (!target.path.startsWith("/") || target.inode == 0)
            return null;
        try (RandomAccessFile file = new RandomAccessFile(target.path, "r")) {
            StructStat st = Os.fstat(file.getFD());
            if (!android.system.OsConstants.S_ISREG(st.st_mode))
                return null;
            String[] dev = target.device.split(":");
            long major = (st.st_dev >>> 8 & 0xfff) | (st.st_dev >>> 32 & 0xfffff000L);
            long minor = (st.st_dev & 255) | (st.st_dev >>> 12 & 0xffffff00L);
            if (st.st_ino != target.inode || major != hex(dev[0]) || minor != hex(dev[1]))
                return null;
            // An APK may contain multiple ELF files. Match both virtual and file placement
            // against the executable PT_LOAD of a mapped ELF header from that same inode.
            for (int i = maps.indexOf(target); i >= 0; --i) {
                Mapping header = maps.get(i);
                if (!header.sameFile(target) || header.offset > target.offset)
                    continue;
                Image found = Image.find(file, header, target);
                if (found != null)
                    return found;
            }
        } catch (IOException | ErrnoException
            | SecurityException unavailable) { /* Deleted or inaccessible image. */
        }
        return null;
    }

    private static final class Image {
        final long start, end, vaddr;
        final String name, id;
        Image(long start, long end, long vaddr, String name, String id) {
            this.start = start;
            this.end = end;
            this.vaddr = vaddr;
            this.name = name;
            this.id = id;
        }
        static ByteBuffer read(RandomAccessFile file, long offset, int size) throws IOException {
            if (offset < 0 || offset > file.length() - size)
                throw new EOFException();
            byte[] bytes = new byte[size];
            file.seek(offset);
            file.readFully(bytes);
            return ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
        }
        // Android maps uncompressed ZIP entries directly. Identify the local header
        // ending at the ELF data offset, even when a library has no DT_SONAME.
        static String moduleName(RandomAccessFile file, Mapping header) throws IOException {
            String fallback = header.name();
            if (!fallback.endsWith(".apk"))
                return fallback;
            int length = (int) Math.min(header.offset, 30 + 512 + 65535);
            ByteBuffer zip = read(file, header.offset - length, length);
            for (int pos = length - 30; pos >= 0; pos--) {
                if (zip.getInt(pos) != 0x04034b50 || zip.getShort(pos + 8) != 0)
                    continue;
                int nameLen = zip.getShort(pos + 26) & 0xffff, extra = zip.getShort(pos + 28) & 0xffff;
                if (nameLen > 512 || pos + 30 + nameLen + extra != length)
                    continue;
                String entry =
                    new String(zip.array(), pos + 30, nameLen, java.nio.charset.StandardCharsets.UTF_8);
                if (entry.matches("lib/arm64-v8a/[A-Za-z0-9_.+-]+\\.so"))
                    return entry.substring(entry.lastIndexOf('/') + 1);
            }
            return fallback;
        }
        static Image find(RandomAccessFile file, Mapping header, Mapping target) throws IOException {
            if (header.offset > file.length() - 64)
                return null;
            ByteBuffer elf = read(file, header.offset, 64);
            if (elf.getInt(0) != 0x464c457f || elf.get(4) != 2 || elf.get(5) != 1 || elf.getShort(18) != 183
                || elf.getShort(54) != 56)
                return null;
            int count = elf.getShort(56) & 0xffff;
            long phoff = elf.getLong(32);
            if (count == 0 || count > 256 || phoff < 0 || phoff > 1024 * 1024)
                return null;
            ByteBuffer ph = read(file, header.offset + phoff, count * 56);
            long bias = -1, start = -1, size = 0, vaddr = 0;
            for (int p = 0; p < ph.limit(); p += 56) {
                if (ph.getInt(p) == 1 && ph.getLong(p + 8) == 0)
                    bias = header.lo - ph.getLong(p + 16);
            }
            if (bias < 0)
                return null;
            for (int p = 0; p < ph.limit(); p += 56) {
                if (ph.getInt(p) != 1 || (ph.getInt(p + 4) & 1) == 0)
                    continue;
                long off = header.offset + ph.getLong(p + 8), addr = ph.getLong(p + 16),
                     len = ph.getLong(p + 32);
                if (target.offset - target.lo != off - (bias + addr)
                    || off >= target.offset + (target.hi - target.lo) || off + len <= target.offset)
                    continue;
                start = off;
                size = len;
                vaddr = addr;
                break;
            }
            if (start < 0)
                return null;
            for (int p = 0; p < ph.limit(); p += 56) {
                long len = ph.getLong(p + 32);
                if (ph.getInt(p) != 4 || len < 16 || len > 65536)
                    continue;
                ByteBuffer notes = read(file, header.offset + ph.getLong(p + 8), (int) len);
                while (notes.remaining() >= 12) {
                    int name = notes.getInt(), desc = notes.getInt(), type = notes.getInt();
                    if (name < 0 || desc < 0 || (long) name + desc > notes.remaining())
                        break;
                    int pos = notes.position(), data = pos + ((name + 3) & ~3),
                        next = data + ((desc + 3) & ~3);
                    if (next > notes.limit())
                        break;
                    if (type == 3 && name == 4 && notes.getInt(pos) == 0x00554e47 && desc > 0 && desc <= 64) {
                        StringBuilder id = new StringBuilder();
                        for (int j = 0; j < desc; j++) {
                            int b = notes.get(data + j) & 255;
                            id.append(Character.forDigit(b >>> 4, 16)).append(Character.forDigit(b & 15, 16));
                        }
                        return new Image(start, start + size, vaddr, moduleName(file, header), id.toString());
                    }
                    notes.position(next);
                }
            }
            return null;
        }
    }
}
