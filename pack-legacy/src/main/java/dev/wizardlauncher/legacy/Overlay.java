package dev.wizardlauncher.legacy;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class Overlay {
    private final Map<String, byte[]> files = new LinkedHashMap<>();
    private final Map<String, String> aliases = new LinkedHashMap<>();
    private final List<String> info = new ArrayList<>();
    private final List<String> warnings = new ArrayList<>();
    private final int sourceFormat;

    Overlay(int sourceFormat) {
        this.sourceFormat = sourceFormat;
    }

    void put(String path, byte[] data) {
        files.put(path, data);
    }

    void alias(String target, String source) {
        aliases.put(target, source);
    }

    void info(String message) {
        info.add(message);
    }

    void warn(String message) {
        warnings.add(message);
    }

    public int sourceFormat() {
        return sourceFormat;
    }

    public Map<String, byte[]> files() {
        return Collections.unmodifiableMap(files);
    }

    public Map<String, String> aliases() {
        return Collections.unmodifiableMap(aliases);
    }

    public byte[] file(String path) {
        return files.get(path);
    }

    public String aliasOf(String path) {
        return aliases.get(path);
    }

    public List<String> info() {
        return Collections.unmodifiableList(info);
    }

    public List<String> warnings() {
        return Collections.unmodifiableList(warnings);
    }

    public boolean isEmpty() {
        return files.isEmpty() && aliases.isEmpty();
    }

    public String report() {
        StringBuilder b = new StringBuilder();
        b.append("Wizard Legacy Packs - pack_format ").append(sourceFormat).append(" read as ").append(LegacyTranslator.TARGET_FORMAT).append('\n');
        b.append("rewritten: ").append(files.size()).append(", aliased: ").append(aliases.size()).append(", warnings: ").append(warnings.size()).append("\n\n");
        if (!info.isEmpty()) {
            b.append("Changes:\n");
            info.forEach(i -> b.append("  - ").append(i).append('\n'));
            b.append('\n');
        }
        if (warnings.isEmpty()) {
            b.append("Nothing needs attention.\n");
        } else {
            b.append("Needs attention:\n");
            warnings.forEach(w -> b.append("  ! ").append(w).append('\n'));
        }
        return b.toString();
    }
}
