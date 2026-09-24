package dev.wizardlauncher.legacy;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Predicate;
import java.util.stream.Stream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

public final class PackExporter {
    public static final String REPORT = "wizard-legacy-report.txt";

    private PackExporter() {
    }

    public static Overlay export(Path input, Path output, List<String> extraRules, Predicate<String> vanilla) throws IOException {
        try (FilePackView view = FilePackView.open(input)) {
            int format = view.exists("pack.mcmeta") ? LegacyTranslator.readFormat(view.read("pack.mcmeta")) : -1;
            if (format < 0) {
                throw new IOException("not a resource pack (pack.mcmeta missing or unreadable)");
            }
            Overlay overlay = LegacyTranslator.translate(view, format, extraRules, vanilla);
            write(view, overlay, output);
            return overlay;
        }
    }

    private static void write(FilePackView view, Overlay overlay, Path output) throws IOException {
        boolean asZip = output.getFileName().toString().toLowerCase().endsWith(".zip");
        Path tmp = output.resolveSibling(output.getFileName() + ".tmp");
        deleteTree(tmp);
        if (output.getParent() != null) {
            Files.createDirectories(output.getParent());
        }
        Set<String> written = new HashSet<>();
        try (Sink sink = asZip ? new ZipSink(tmp) : new DirSink(tmp)) {
            for (Map.Entry<String, byte[]> e : overlay.files().entrySet()) {
                sink.put(e.getKey(), e.getValue());
                written.add(e.getKey());
            }
            for (Map.Entry<String, String> e : overlay.aliases().entrySet()) {
                if (written.add(e.getKey())) {
                    try (InputStream in = view.open(e.getValue())) {
                        sink.put(e.getKey(), in.readAllBytes());
                    }
                }
            }
            for (String path : view.paths()) {
                if (written.add(path)) {
                    try (InputStream in = view.open(path)) {
                        sink.put(path, in.readAllBytes());
                    }
                }
            }
            sink.put(REPORT, overlay.report().getBytes(StandardCharsets.UTF_8));
        } catch (IOException | RuntimeException e) {
            deleteTree(tmp);
            throw e;
        }
        deleteTree(output);
        Files.move(tmp, output, StandardCopyOption.ATOMIC_MOVE);
    }

    static void deleteTree(Path path) throws IOException {
        if (!Files.exists(path)) {
            return;
        }
        try (Stream<Path> walk = Files.walk(path)) {
            for (Path p : walk.sorted(Comparator.reverseOrder()).toList()) {
                Files.deleteIfExists(p);
            }
        }
    }

    private interface Sink extends AutoCloseable {
        void put(String path, byte[] data) throws IOException;

        @Override
        void close() throws IOException;
    }

    private static final class ZipSink implements Sink {
        private final ZipOutputStream zip;

        ZipSink(Path file) throws IOException {
            zip = new ZipOutputStream(Files.newOutputStream(file));
        }

        @Override
        public void put(String path, byte[] data) throws IOException {
            if (!FilePackView.safePath(path)) {
                throw new IOException("unsafe output path " + path);
            }
            ZipEntry entry = new ZipEntry(path);
            entry.setTime(0);
            zip.putNextEntry(entry);
            zip.write(data);
            zip.closeEntry();
        }

        @Override
        public void close() throws IOException {
            zip.close();
        }
    }

    private static final class DirSink implements Sink {
        private final Path root;

        DirSink(Path root) throws IOException {
            this.root = Files.createDirectories(root);
        }

        @Override
        public void put(String path, byte[] data) throws IOException {
            if (!FilePackView.safePath(path)) {
                throw new IOException("unsafe output path " + path);
            }
            Path target = root.resolve(path);
            Files.createDirectories(target.getParent());
            try (OutputStream out = Files.newOutputStream(target)) {
                out.write(data);
            }
        }

        @Override
        public void close() {
        }
    }
}
