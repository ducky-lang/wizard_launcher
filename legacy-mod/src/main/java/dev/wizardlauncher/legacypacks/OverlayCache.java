package dev.wizardlauncher.legacypacks;

import dev.wizardlauncher.legacy.LegacyTranslator;
import dev.wizardlauncher.legacy.Overlay;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.stream.Stream;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.server.packs.PackResources;

final class OverlayCache {
    private static final Map<String, Overlay> MEMORY = new ConcurrentHashMap<>();
    private static final ExecutorService EXECUTOR = Executors.newFixedThreadPool(2, r -> {
        Thread t = new Thread(r, "WizardLegacyPacks");
        t.setDaemon(true);
        t.setPriority(Thread.NORM_PRIORITY - 1);
        return t;
    });

    private OverlayCache() {
    }

    static CompletableFuture<Overlay> load(PackResources pack, ResourcesView view, int format, List<String> rules) {
        return CompletableFuture.supplyAsync(() -> {
            try {
                return resolve(pack, view, format, rules);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }, EXECUTOR);
    }

    private static Overlay resolve(PackResources pack, ResourcesView view, int format, List<String> rules) throws IOException {
        long started = System.nanoTime();
        String packId = pack.packId();
        String key = key(packId, format, rules);
        if (key != null) {
            Overlay hit = MEMORY.get(key);
            String source = "memory";
            if (hit == null) {
                hit = readDisk(packId, key);
                source = "disk";
            }
            if (hit != null) {
                MEMORY.put(key, hit);
                logReady(packId, format, hit, started, " (" + source + " cache)");
                return hit;
            }
        }
        Overlay fresh = LegacyTranslator.translate(view, format, rules, null, Compat.TARGET_FORMAT);
        logReady(packId, format, fresh, started, "");
        writeReport(packId, fresh);
        if (key != null) {
            MEMORY.keySet().removeIf(k -> k.startsWith(packId + "\n"));
            MEMORY.put(key, fresh);
            writeDisk(packId, key, fresh);
        }
        return fresh;
    }

    private static void logReady(String packId, int format, Overlay overlay, long started, String suffix) {
        LegacyPacks.LOGGER.info("Reading '{}' (pack_format {}) natively: {} file(s) adapted, {} alias(es), {} note(s) in {} ms{}",
            packId, format, overlay.files().size(), overlay.aliases().size(), overlay.warnings().size(),
            (System.nanoTime() - started) / 1_000_000, suffix);
    }

    private static String key(String packId, int format, List<String> rules) throws IOException {
        Path source = sourceOf(packId);
        if (source == null) {
            return null;
        }
        MessageDigest digest = sha256();
        update(digest, "rev" + LegacyTranslator.REVISION + "|format" + format + "|target" + Compat.TARGET_FORMAT);
        for (String r : rules) {
            update(digest, r);
        }
        if (Files.isDirectory(source)) {
            List<String> entries = new ArrayList<>();
            try (Stream<Path> walk = Files.walk(source)) {
                walk.forEach(p -> {
                    try {
                        BasicFileAttributes a = Files.readAttributes(p, BasicFileAttributes.class);
                        if (a.isRegularFile()) {
                            entries.add(source.relativize(p) + "|" + a.size() + "|" + a.lastModifiedTime().toMillis());
                        }
                    } catch (IOException ignored) {
                    }
                });
            }
            entries.sort(null);
            entries.forEach(e -> update(digest, e));
        } else {
            BasicFileAttributes a = Files.readAttributes(source, BasicFileAttributes.class);
            update(digest, a.size() + "|" + a.lastModifiedTime().toMillis());
        }
        return packId + "\n" + HexFormat.of().formatHex(digest.digest());
    }

    private static Path sourceOf(String packId) {
        if (!packId.startsWith("file/")) {
            return null;
        }
        String name = packId.substring(5);
        if (name.isEmpty() || name.contains("..") || name.contains("/") || name.contains("\\")) {
            return null;
        }
        Path path = FabricLoader.getInstance().getGameDir().resolve("resourcepacks").resolve(name);
        return Files.exists(path) ? path : null;
    }

    private static Path cacheDir() {
        return FabricLoader.getInstance().getGameDir().resolve("cache").resolve("wizard-legacy-packs");
    }

    private static String fileStem(String packId) {
        MessageDigest digest = sha256();
        update(digest, packId);
        return HexFormat.of().formatHex(digest.digest()).substring(0, 16);
    }

    private static Path cacheFile(String packId, String key) {
        String fingerprint = key.substring(key.indexOf('\n') + 1).substring(0, 24);
        return cacheDir().resolve(fileStem(packId) + "-" + fingerprint + ".overlay");
    }

    private static Overlay readDisk(String packId, String key) {
        Path file = cacheFile(packId, key);
        if (!Files.isRegularFile(file)) {
            return null;
        }
        try (InputStream in = Files.newInputStream(file)) {
            return Overlay.read(in);
        } catch (Exception e) {
            LegacyPacks.LOGGER.warn("Ignoring the unreadable cached copy of '{}': {}", packId, e.toString());
            try {
                Files.deleteIfExists(file);
            } catch (IOException ignored) {
            }
            return null;
        }
    }

    private static void writeDisk(String packId, String key, Overlay overlay) {
        Path file = cacheFile(packId, key);
        Path tmp = file.resolveSibling(file.getFileName() + ".tmp");
        try {
            Files.createDirectories(file.getParent());
            try (OutputStream out = Files.newOutputStream(tmp)) {
                overlay.write(out);
            }
            Files.move(tmp, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            String stem = fileStem(packId) + "-";
            try (Stream<Path> old = Files.list(file.getParent())) {
                old.filter(p -> p.getFileName().toString().startsWith(stem) && !p.equals(file))
                    .forEach(p -> {
                        try {
                            Files.deleteIfExists(p);
                        } catch (IOException ignored) {
                        }
                    });
            }
        } catch (Exception e) {
            LegacyPacks.LOGGER.warn("Could not cache the adapted copy of '{}': {}", packId, e.toString());
            try {
                Files.deleteIfExists(tmp);
            } catch (IOException ignored) {
            }
        }
    }

    private static void writeReport(String packId, Overlay overlay) {
        try {
            Path dir = FabricLoader.getInstance().getGameDir().resolve("logs").resolve("wizard-legacy-packs");
            Files.createDirectories(dir);
            String name = packId.replaceAll("[^A-Za-z0-9._-]", "_") + ".txt";
            Files.writeString(dir.resolve(name), overlay.report(), StandardCharsets.UTF_8);
        } catch (Exception e) {
            LegacyPacks.LOGGER.debug("Could not write the pack report", e);
        }
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private static void update(MessageDigest digest, String text) {
        digest.update(text.getBytes(StandardCharsets.UTF_8));
        digest.update((byte) 0);
    }
}
