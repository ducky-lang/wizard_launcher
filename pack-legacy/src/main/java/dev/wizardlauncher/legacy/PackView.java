package dev.wizardlauncher.legacy;

import java.io.IOException;
import java.util.List;
import java.util.Set;

public interface PackView {
    boolean exists(String path);

    byte[] read(String path) throws IOException;

    Set<String> namespaces();

    List<String> list(String namespace, String directory);

    default String readText(String path) throws IOException {
        return new String(read(path), java.nio.charset.StandardCharsets.UTF_8);
    }
}
