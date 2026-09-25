package dev.wizardlauncher.legacy;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

final class LegacyVanilla {
    private static volatile LegacyVanilla instance;

    final Map<String, JsonObject> models = new LinkedHashMap<>();
    final Map<String, JsonObject> blockstates = new LinkedHashMap<>();
    final Map<String, String> renamedSounds = new LinkedHashMap<>();
    final Map<String, JsonObject> legacySounds = new LinkedHashMap<>();
    final Map<String, Set<String>> currentSoundFiles = new LinkedHashMap<>();
    final Set<Integer> bitmapChars = new HashSet<>();

    private LegacyVanilla(JsonObject root) {
        root.getAsJsonObject("models").entrySet().forEach(e -> models.put(e.getKey(), e.getValue().getAsJsonObject()));
        root.getAsJsonObject("blockstates").entrySet().forEach(e -> blockstates.put(e.getKey(), e.getValue().getAsJsonObject()));
        JsonObject sounds = root.getAsJsonObject("sounds");
        sounds.getAsJsonObject("renamed").entrySet().forEach(e -> renamedSounds.put(e.getKey(), e.getValue().getAsString()));
        for (Map.Entry<String, JsonElement> e : sounds.getAsJsonObject("events").entrySet()) {
            JsonObject event = e.getValue().getAsJsonObject();
            legacySounds.put(e.getKey(), event.getAsJsonObject("legacy"));
            Set<String> current = new LinkedHashSet<>();
            event.getAsJsonArray("current").forEach(n -> current.add(n.getAsString()));
            currentSoundFiles.put(e.getKey(), Collections.unmodifiableSet(current));
        }
        root.getAsJsonObject("font").get("bitmap_chars").getAsString().codePoints().forEach(bitmapChars::add);
    }

    static LegacyVanilla get() {
        LegacyVanilla v = instance;
        if (v == null) {
            synchronized (LegacyVanilla.class) {
                v = instance;
                if (v == null) {
                    v = instance = load();
                }
            }
        }
        return v;
    }

    private static LegacyVanilla load() {
        try (InputStream in = LegacyVanilla.class.getResourceAsStream("legacy-vanilla.json")) {
            if (in == null) {
                throw new IllegalStateException("legacy game data missing");
            }
            return new LegacyVanilla(JsonParser.parseString(new String(in.readAllBytes(), StandardCharsets.UTF_8)).getAsJsonObject());
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
