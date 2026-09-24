package dev.wizardlauncher.legacy;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Stream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

public final class FilePackView implements PackView, AutoCloseable {
    private final ZipFile zip;
    private final Path dir;
    private final String prefix;
    private final List<String> paths;
    private final Set<String> pathSet;

    private FilePackView(ZipFile zip, Path dir, String prefix, List<String> paths) {
        this.zip = zip;
        this.dir = dir;
        this.prefix = prefix;
        this.paths = paths;
        this.pathSet = new HashSet<>(paths);
    }

    public static FilePackView open(Path input) throws IOException {
        if (Files.isDirectory(input)) {
            Path root = findRoot(input);
            List<String> paths = new ArrayList<>();
            try (Stream<Path> walk = Files.walk(root)) {
                walk.filter(Files::isRegularFile)
                    .map(p -> root.relativize(p).toString().replace('\\', '/'))
                    .filter(FilePackView::safePath)
                    .sorted()
                    .forEach(paths::add);
            }
            return new FilePackView(null, root, "", paths);
        }
        ZipFile zip = new ZipFile(input.toFile());
        List<String> names = new ArrayList<>();
        Enumeration<? extends ZipEntry> entries = zip.entries();
        while (entries.hasMoreElements()) {
            ZipEntry e = entries.nextElement();
            if (!e.isDirectory()) {
                names.add(e.getName());
            }
        }
        String prefix = "";
        if (!names.contains("pack.mcmeta")) {
            prefix = names.stream()
                .filter(n -> n.endsWith("/pack.mcmeta"))
                .min(Comparator.comparingLong(n -> n.chars().filter(c -> c == '/').count()))
                .map(n -> n.substring(0, n.length() - "pack.mcmeta".length()))
                .orElse("");
        }
        List<String> paths = new ArrayList<>();
        for (String n : names) {
            if (n.startsWith(prefix)) {
                String p = n.substring(prefix.length());
                if (safePath(p)) {
                    paths.add(p);
                }
            }
        }
        paths.sort(null);
        return new FilePackView(zip, null, prefix, paths);
    }

    private static Path findRoot(Path dir) throws IOException {
        if (Files.isRegularFile(dir.resolve("pack.mcmeta"))) {
            return dir;
        }
        try (Stream<Path> children = Files.list(dir)) {
            List<Path> dirs = children.filter(Files::isDirectory).toList();
            if (dirs.size() == 1 && Files.isRegularFile(dirs.get(0).resolve("pack.mcmeta"))) {
                return dirs.get(0);
            }
        }
        return dir;
    }

    public static boolean safePath(String path) {
        if (path.isEmpty() || path.startsWith("/") || path.indexOf('\\') >= 0 || path.indexOf(':') >= 0) {
            return false;
        }
        for (String part : path.split("/", -1)) {
            if (part.isEmpty() || part.equals(".") || part.equals("..")) {
                return false;
            }
        }
        return true;
    }

    public List<String> paths() {
        return paths;
    }

    @Override
    public boolean exists(String path) {
        return pathSet.contains(path);
    }

    public InputStream open(String path) throws IOException {
        if (!exists(path)) {
            throw new IOException("not in pack: " + path);
        }
        if (zip != null) {
            return zip.getInputStream(zip.getEntry(prefix + path));
        }
        return Files.newInputStream(dir.resolve(path));
    }

    @Override
    public byte[] read(String path) throws IOException {
        try (InputStream in = open(path)) {
            return in.readAllBytes();
        }
    }

    @Override
    public Set<String> namespaces() {
        Set<String> out = new TreeSet<>();
        for (String p : paths) {
            if (p.startsWith("assets/")) {
                int slash = p.indexOf('/', 7);
                if (slash > 7) {
                    out.add(p.substring(7, slash));
                }
            }
        }
        return out;
    }

    @Override
    public List<String> list(String namespace, String directory) {
        String start = "assets/" + namespace + "/" + directory + "/";
        List<String> out = new ArrayList<>();
        for (String p : paths) {
            if (p.startsWith(start)) {
                out.add(p);
            }
        }
        return out;
    }

    @Override
    public void close() throws IOException {
        if (zip != null) {
            zip.close();
        }
    }
}
