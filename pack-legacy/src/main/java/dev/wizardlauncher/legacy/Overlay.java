package dev.wizardlauncher.legacy;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;

public final class Overlay {
    private final Map<String, byte[]> files = new LinkedHashMap<>();
    private final Map<String, String> aliases = new LinkedHashMap<>();
    private final List<String> info = new ArrayList<>();
    private final List<String> warnings = new ArrayList<>();
    private final int sourceFormat;
    private final int targetFormat;

    Overlay(int sourceFormat, int targetFormat) {
        this.sourceFormat = sourceFormat;
        this.targetFormat = targetFormat;
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

    public int targetFormat() {
        return targetFormat;
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
        b.append("Wizard Legacy Packs - pack_format ").append(sourceFormat).append(" read as ").append(targetFormat).append('\n');
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

    public void write(OutputStream out) throws IOException {
        JsonObject index = new JsonObject();
        index.addProperty("revision", LegacyTranslator.REVISION);
        index.addProperty("source_format", sourceFormat);
        index.addProperty("target_format", targetFormat);
        JsonArray names = new JsonArray();
        files.keySet().forEach(names::add);
        index.add("files", names);
        JsonObject aliasJson = new JsonObject();
        aliases.forEach(aliasJson::addProperty);
        index.add("aliases", aliasJson);
        JsonArray infoJson = new JsonArray();
        info.forEach(infoJson::add);
        index.add("info", infoJson);
        JsonArray warnJson = new JsonArray();
        warnings.forEach(warnJson::add);
        index.add("warnings", warnJson);
        ZipOutputStream zip = new ZipOutputStream(out);
        zip.putNextEntry(new ZipEntry("overlay.json"));
        zip.write(index.toString().getBytes(StandardCharsets.UTF_8));
        zip.closeEntry();
        int i = 0;
        for (byte[] data : files.values()) {
            zip.putNextEntry(new ZipEntry("f/" + i++));
            zip.write(data);
            zip.closeEntry();
        }
        zip.finish();
    }

    public static Overlay read(InputStream in) throws IOException {
        ZipInputStream zip = new ZipInputStream(in);
        JsonObject index = null;
        Map<Integer, byte[]> blobs = new HashMap<>();
        for (ZipEntry e; (e = zip.getNextEntry()) != null; ) {
            byte[] data = zip.readAllBytes();
            if (e.getName().equals("overlay.json")) {
                index = JsonParser.parseString(new String(data, StandardCharsets.UTF_8)).getAsJsonObject();
            } else if (e.getName().startsWith("f/")) {
                blobs.put(Integer.parseInt(e.getName().substring(2)), data);
            }
        }
        if (index == null || index.get("revision").getAsInt() != LegacyTranslator.REVISION) {
            throw new IOException("stale or incomplete overlay");
        }
        Overlay overlay = new Overlay(index.get("source_format").getAsInt(),
            index.has("target_format") ? index.get("target_format").getAsInt() : LegacyTranslator.TARGET_FORMAT);
        JsonArray names = index.getAsJsonArray("files");
        for (int i = 0; i < names.size(); i++) {
            byte[] data = blobs.get(i);
            if (data == null) {
                throw new IOException("overlay is missing " + names.get(i).getAsString());
            }
            overlay.files.put(names.get(i).getAsString(), data);
        }
        for (Map.Entry<String, JsonElement> a : index.getAsJsonObject("aliases").entrySet()) {
            overlay.aliases.put(a.getKey(), a.getValue().getAsString());
        }
        index.getAsJsonArray("info").forEach(i -> overlay.info.add(i.getAsString()));
        index.getAsJsonArray("warnings").forEach(w -> overlay.warnings.add(w.getAsString()));
        return overlay;
    }
}
