package dev.wizardlauncher.legacy;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class Rules {
    public record Copy(String from, String to) {
    }

    public record Split(String from, String to, Map<String, Set<String>> when, Set<String> removeProperties, Map<String, String> set) {
    }

    public final List<Copy> copyFiles = new ArrayList<>();
    public final Map<String, String> modelRenames = new LinkedHashMap<>();
    public final Map<String, String> textureRenames = new LinkedHashMap<>();
    public final List<Split> splits = new ArrayList<>();
    public final Map<String, String> langKeys = new LinkedHashMap<>();
    public final Map<String, Map<String, JsonElement>> states = new LinkedHashMap<>();
    public final Map<String, List<JsonObject>> items = new LinkedHashMap<>();

    public Rules merge(Rules other) {
        copyFiles.addAll(other.copyFiles);
        modelRenames.putAll(other.modelRenames);
        textureRenames.putAll(other.textureRenames);
        splits.addAll(other.splits);
        langKeys.putAll(other.langKeys);
        other.states.forEach((k, v) -> states.computeIfAbsent(k, x -> new LinkedHashMap<>()).putAll(v));
        other.items.forEach((k, v) -> items.computeIfAbsent(k, x -> new ArrayList<>()).addAll(v));
        return this;
    }

    public boolean isEmpty() {
        return copyFiles.isEmpty() && modelRenames.isEmpty() && textureRenames.isEmpty() && splits.isEmpty()
            && langKeys.isEmpty() && states.isEmpty() && items.isEmpty();
    }

    public static Rules builtin(int sourceFormat) {
        return builtin(sourceFormat, LegacyTranslator.TARGET_FORMAT);
    }

    public static Rules builtin(int sourceFormat, int targetFormat) {
        try (InputStream in = Rules.class.getResourceAsStream("builtin-rules.json")) {
            if (in == null) {
                throw new IllegalStateException("built-in rules missing");
            }
            JsonObject root = JsonParser.parseString(new String(in.readAllBytes(), StandardCharsets.UTF_8)).getAsJsonObject();
            Rules rules = new Rules();
            for (JsonElement stage : root.getAsJsonArray("stages")) {
                JsonObject o = stage.getAsJsonObject();
                int minTarget = o.has("min_target") ? o.get("min_target").getAsInt() : 0;
                if (sourceFormat <= o.get("max_format").getAsInt() && targetFormat >= minTarget) {
                    rules.merge(parse(o, "built-in"));
                }
            }
            return rules;
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    public static Rules parse(String text, String origin) {
        JsonObject root;
        try {
            root = JsonParser.parseString(text).getAsJsonObject();
        } catch (RuntimeException e) {
            throw new IllegalArgumentException(origin + ": not a JSON object (" + e.getMessage() + ")");
        }
        int format = root.has("format") ? root.get("format").getAsInt() : 1;
        if (format != 1) {
            throw new IllegalArgumentException(origin + ": unsupported rules format " + format);
        }
        return parse(root, origin);
    }

    private static Rules parse(JsonObject root, String origin) {
        Rules r = new Rules();
        for (JsonElement e : array(root, "copy_files")) {
            JsonObject o = e.getAsJsonObject();
            r.copyFiles.add(new Copy(checkedPath(str(o, "from", origin), origin), checkedPath(str(o, "to", origin), origin)));
        }
        JsonObject renames = root.has("rename_references") ? root.getAsJsonObject("rename_references") : new JsonObject();
        renames(renames, "models", r.modelRenames);
        renames(renames, "textures", r.textureRenames);
        for (JsonElement e : array(root, "split_blockstates")) {
            JsonObject o = e.getAsJsonObject();
            Map<String, Set<String>> when = new LinkedHashMap<>();
            if (o.has("when")) {
                for (Map.Entry<String, JsonElement> w : o.getAsJsonObject("when").entrySet()) {
                    Set<String> values = new LinkedHashSet<>();
                    for (String v : w.getValue().getAsString().split("\\|")) {
                        values.add(v.trim());
                    }
                    when.put(w.getKey(), values);
                }
            }
            Set<String> remove = new LinkedHashSet<>();
            for (JsonElement p : array(o, "remove_properties")) {
                remove.add(p.getAsString());
            }
            Map<String, String> set = new LinkedHashMap<>();
            if (o.has("set")) {
                o.getAsJsonObject("set").entrySet().forEach(s -> set.put(s.getKey(), s.getValue().getAsString()));
            }
            r.splits.add(new Split(id(str(o, "from", origin)), id(str(o, "to", origin)), when, remove, set));
        }
        if (root.has("lang_keys")) {
            root.getAsJsonObject("lang_keys").entrySet().forEach(e -> r.langKeys.put(e.getKey(), e.getValue().getAsString()));
        }
        if (root.has("states")) {
            for (Map.Entry<String, JsonElement> block : root.getAsJsonObject("states").entrySet()) {
                if (!block.getValue().isJsonObject()) {
                    throw new IllegalArgumentException(origin + ": states." + block.getKey() + " must be an object of variant -> model");
                }
                Map<String, JsonElement> variants = new LinkedHashMap<>();
                for (Map.Entry<String, JsonElement> v : block.getValue().getAsJsonObject().entrySet()) {
                    VariantKey.parse(v.getKey());
                    variants.put(v.getKey(), v.getValue());
                }
                r.states.put(id(block.getKey()), variants);
            }
        }
        if (root.has("items")) {
            for (Map.Entry<String, JsonElement> item : root.getAsJsonObject("items").entrySet()) {
                if (!item.getValue().isJsonArray()) {
                    throw new IllegalArgumentException(origin + ": items." + item.getKey() + " must be an array of overrides");
                }
                List<JsonObject> list = new ArrayList<>();
                for (JsonElement o : item.getValue().getAsJsonArray()) {
                    JsonObject obj = o.getAsJsonObject().deepCopy();
                    if (!obj.has("model") || !obj.has("predicate")) {
                        throw new IllegalArgumentException(origin + ": items." + item.getKey() + " entries need 'predicate' and 'model'");
                    }
                    obj.addProperty("model", id(obj.get("model").getAsString()));
                    list.add(obj);
                }
                r.items.put(id(item.getKey()), list);
            }
        }
        return r;
    }

    private static void renames(JsonObject parent, String key, Map<String, String> into) {
        if (parent.has(key)) {
            parent.getAsJsonObject(key).entrySet().forEach(e -> into.put(id(e.getKey()), id(e.getValue().getAsString())));
        }
    }

    private static Iterable<JsonElement> array(JsonObject o, String name) {
        return o.has(name) ? o.getAsJsonArray(name) : new JsonArray();
    }

    private static String str(JsonObject o, String name, String origin) {
        JsonElement e = o.get(name);
        if (e == null || !e.isJsonPrimitive()) {
            throw new IllegalArgumentException(origin + ": missing '" + name + "'");
        }
        return e.getAsString();
    }

    private static String checkedPath(String path, String origin) {
        if (!FilePackView.safePath(path) || !path.startsWith("assets/")) {
            throw new IllegalArgumentException(origin + ": unsafe path '" + path + "'");
        }
        return path;
    }

    public static String id(String raw) {
        return raw.indexOf(':') >= 0 ? raw : "minecraft:" + raw;
    }
}
