package dev.wizardlauncher.legacy;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.stream.JsonReader;
import java.io.IOException;
import java.io.StringReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Deque;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.Predicate;

public final class LegacyTranslator {
    public static final int TARGET_FORMAT = 15;
    public static final int OLDEST_FORMAT = 4;
    public static final int REVISION = 4;
    public static final String PACK_RULES = "wizard-states.json";
    public static final Set<String> VANILLA_TEXTURE_ROOTS = Set.of(
        "entity", "misc", "environment", "gui", "font", "painting", "mob_effect", "particle",
        "map", "models", "colormap", "effect", "trims");

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();

    private final PackView pack;
    private final int sourceFormat;
    private final Rules rules;
    private final Overlay overlay;
    private final Map<String, JsonElement> json = new LinkedHashMap<>();
    private final Map<String, Set<String>> modelTextures = new LinkedHashMap<>();
    private final Set<String> modelRefs = new LinkedHashSet<>();
    private final Predicate<String> vanilla;
    private FontUpgrader fonts;
    private boolean defaultFontHasLegacyPages;
    private static final String DEFAULT_FONT = "assets/minecraft/font/default.json";
    private static final String SOUNDS = "assets/minecraft/sounds.json";

    private LegacyTranslator(PackView pack, int sourceFormat, Rules rules, Predicate<String> vanilla) {
        this.pack = pack;
        this.sourceFormat = sourceFormat;
        this.rules = rules;
        this.overlay = new Overlay(sourceFormat);
        this.vanilla = vanilla;
    }

    public static boolean needsTranslation(int format) {
        return format >= OLDEST_FORMAT && format < TARGET_FORMAT;
    }

    public static int readFormat(byte[] packMcmeta) {
        try {
            JsonObject root = parse(new String(packMcmeta, StandardCharsets.UTF_8)).getAsJsonObject();
            return root.getAsJsonObject("pack").get("pack_format").getAsInt();
        } catch (RuntimeException e) {
            return -1;
        }
    }

    public static Overlay translate(PackView pack, int sourceFormat, List<String> extraRules, Predicate<String> vanillaAsset) throws IOException {
        Rules rules = Rules.builtin(sourceFormat);
        List<String> applied = new ArrayList<>();
        if (pack.exists(PACK_RULES)) {
            rules.merge(Rules.parse(pack.readText(PACK_RULES), PACK_RULES));
            applied.add(PACK_RULES);
        }
        int i = 0;
        for (String text : extraRules) {
            rules.merge(Rules.parse(text, "rules #" + (++i)));
            applied.add("rules #" + i);
        }
        LegacyTranslator t = new LegacyTranslator(pack, sourceFormat, rules, vanillaAsset);
        applied.forEach(a -> t.overlay.info("applied state rules from " + a));
        t.run();
        return t.overlay;
    }

    public static Overlay translate(PackView pack, int sourceFormat) throws IOException {
        return translate(pack, sourceFormat, List.of(), null);
    }

    private void run() throws IOException {
        applyCopies();
        fonts = new FontUpgrader(pack, overlay);
        for (String ns : pack.namespaces()) {
            for (String path : pack.list(ns, "models")) {
                if (path.endsWith(".json")) {
                    edit(path, root -> {
                        boolean changed = remapModel(root.getAsJsonObject());
                        modelTextures.put(path, texturesOf(root));
                        modelRefs.addAll(modelRefsOf(root));
                        return changed;
                    });
                }
            }
            for (String path : pack.list(ns, "blockstates")) {
                if (path.endsWith(".json")) {
                    edit(path, root -> {
                        boolean changed = remapBlockstate(root, path);
                        modelRefs.addAll(blockstateRefs(root));
                        return changed;
                    });
                }
            }
            for (String path : pack.list(ns, "lang")) {
                if (path.endsWith(".json")) {
                    edit(path, root -> renameLangKeys(root.getAsJsonObject()));
                }
            }
            if (sourceFormat < TARGET_FORMAT) {
                for (String path : pack.list(ns, "font")) {
                    if (path.endsWith(".json")) {
                        edit(path, root -> {
                            boolean converted = fonts.convert(path, root.getAsJsonObject());
                            if (converted && path.equals(DEFAULT_FONT)) {
                                defaultFontHasLegacyPages = true;
                            }
                            return converted;
                        });
                    }
                }
            }
            if (sourceFormat <= 6) {
                for (String path : pack.list(ns, "shaders")) {
                    if (path.contains("/shaders/program/") && (path.endsWith(".vsh") || path.endsWith(".fsh"))) {
                        upgradeShader(path, path.endsWith(".fsh"));
                    }
                }
            }
        }
        applySplits();
        applyDefinedStates();
        applyItemOverrides();
        restoreLegacyModels();
        upgradeSounds();
        if (!defaultFontHasLegacyPages) {
            addImplicitGlyphPages();
        }
        if (sourceFormat < 12) {
            generateAtlas();
        }
        packMeta();
        resolveAliasedRewrites();
        for (Map.Entry<String, JsonElement> e : json.entrySet()) {
            overlay.put(e.getKey(), GSON.toJson(e.getValue()).getBytes(StandardCharsets.UTF_8));
        }
    }

    private boolean provided(String path) {
        return json.containsKey(path) || overlay.file(path) != null || overlay.aliasOf(path) != null || pack.exists(path);
    }

    private void applyCopies() {
        for (Rules.Copy copy : rules.copyFiles) {
            if (pack.exists(copy.from()) && !pack.exists(copy.to()) && overlay.aliasOf(copy.to()) == null) {
                overlay.alias(copy.to(), copy.from());
                overlay.info(copy.from() + " -> " + copy.to());
            }
        }
    }

    private void resolveAliasedRewrites() {
        for (Map.Entry<String, String> alias : new LinkedHashMap<>(overlay.aliases()).entrySet()) {
            JsonElement rewritten = json.get(alias.getValue());
            if (rewritten != null && !json.containsKey(alias.getKey())) {
                json.put(alias.getKey(), rewritten.deepCopy());
            }
        }
    }

    interface Edit {
        boolean apply(JsonElement root) throws IOException;
    }

    private JsonElement readJson(String path) {
        JsonElement cached = json.get(path);
        if (cached != null) {
            return cached;
        }
        String source = overlay.aliasOf(path) != null ? overlay.aliasOf(path) : path;
        String text;
        try {
            text = pack.readText(source);
        } catch (IOException e) {
            overlay.warn(path + ": could not be read, left as it is (" + truncate(e.getMessage()) + ")");
            return null;
        }
        if (text.startsWith("﻿")) {
            text = text.substring(1);
        }
        try {
            return parse(text);
        } catch (RuntimeException e) {
            overlay.warn(path + ": invalid JSON, left unchanged (" + truncate(e.getMessage()) + ")");
            return null;
        }
    }

    @SuppressWarnings("deprecation")
    static JsonElement parse(String text) {
        JsonReader reader = new JsonReader(new StringReader(text));
        reader.setLenient(true);
        return JsonParser.parseReader(reader);
    }

    private static String truncate(String s) {
        return s == null ? "" : s.length() > 120 ? s.substring(0, 120) : s;
    }

    private void edit(String path, Edit edit) throws IOException {
        JsonElement root = readJson(path);
        if (root == null) {
            return;
        }
        try {
            if (edit.apply(root)) {
                json.put(path, root);
            }
        } catch (IOException | RuntimeException e) {
            overlay.warn(path + ": could not be read as expected (" + truncate(e.getMessage()) + ")");
        }
    }

    private void packMeta() throws IOException {
        if (!pack.exists("pack.mcmeta")) {
            return;
        }
        JsonElement root = readJson("pack.mcmeta");
        if (root == null || !root.isJsonObject()) {
            return;
        }
        JsonObject o = root.getAsJsonObject();
        JsonObject p = o.has("pack") ? o.getAsJsonObject("pack") : new JsonObject();
        p.addProperty("pack_format", TARGET_FORMAT);
        o.add("pack", p);
        JsonObject tag = new JsonObject();
        tag.addProperty("source_pack_format", sourceFormat);
        o.add("wizard_legacy_packs", tag);
        json.put("pack.mcmeta", o);
        overlay.info("pack.mcmeta: pack_format " + sourceFormat + " read as " + TARGET_FORMAT);
    }

    private String renameModelRef(String ref) {
        if (ref.startsWith("#")) {
            return ref;
        }
        String id = Res.normalize(ref);
        if (provided(Res.modelFile(id))) {
            return ref;
        }
        return rules.modelRenames.getOrDefault(id, ref);
    }

    private String renameTextureRef(String ref) {
        if (ref.startsWith("#")) {
            return ref;
        }
        String id = Res.normalize(ref);
        if (provided(Res.textureFile(id))) {
            return ref;
        }
        return rules.textureRenames.getOrDefault(id, ref);
    }

    private boolean remapModel(JsonObject model) {
        if (rules.modelRenames.isEmpty() && rules.textureRenames.isEmpty()) {
            return false;
        }
        boolean changed = false;
        if (model.has("parent") && model.get("parent").isJsonPrimitive()) {
            String parent = model.get("parent").getAsString();
            String renamed = renameModelRef(parent);
            if (!renamed.equals(parent)) {
                model.addProperty("parent", renamed);
                changed = true;
            }
        }
        if (model.has("textures") && model.get("textures").isJsonObject()) {
            JsonObject textures = model.getAsJsonObject("textures");
            for (Map.Entry<String, JsonElement> e : new ArrayList<>(textures.entrySet())) {
                if (!e.getValue().isJsonPrimitive()) {
                    continue;
                }
                String renamed = renameTextureRef(e.getValue().getAsString());
                if (!renamed.equals(e.getValue().getAsString())) {
                    textures.addProperty(e.getKey(), renamed);
                    changed = true;
                }
            }
        }
        if (model.has("overrides") && model.get("overrides").isJsonArray()) {
            for (JsonElement o : model.getAsJsonArray("overrides")) {
                JsonObject override = o.getAsJsonObject();
                if (!override.has("model")) {
                    continue;
                }
                String target = override.get("model").getAsString();
                String renamed = renameModelRef(target);
                if (!renamed.equals(target)) {
                    override.addProperty("model", renamed);
                    changed = true;
                }
            }
        }
        return changed;
    }

    private boolean remapBlockstate(JsonElement root, String path) {
        if (rules.modelRenames.isEmpty()) {
            return false;
        }
        boolean[] changed = {false};
        JsonObject obj = root.getAsJsonObject();
        if (obj.has("variants")) {
            for (Map.Entry<String, JsonElement> e : obj.getAsJsonObject("variants").entrySet()) {
                visitModelHolder(e.getValue(), changed);
            }
        }
        if (obj.has("multipart")) {
            for (JsonElement c : obj.getAsJsonArray("multipart")) {
                JsonObject kase = c.getAsJsonObject();
                if (kase.has("apply")) {
                    visitModelHolder(kase.get("apply"), changed);
                }
            }
        }
        if (changed[0]) {
            overlay.info(path + ": model references updated");
        }
        return changed[0];
    }

    private void visitModelHolder(JsonElement e, boolean[] changed) {
        List<JsonElement> list = new ArrayList<>();
        if (e.isJsonArray()) {
            e.getAsJsonArray().forEach(list::add);
        } else {
            list.add(e);
        }
        for (JsonElement item : list) {
            if (!item.isJsonObject() || !item.getAsJsonObject().has("model")) {
                continue;
            }
            JsonObject m = item.getAsJsonObject();
            String ref = m.get("model").getAsString();
            String renamed = renameModelRef(ref);
            if (!renamed.equals(ref)) {
                m.addProperty("model", renamed);
                changed[0] = true;
            }
        }
    }

    private boolean renameLangKeys(JsonObject root) {
        boolean changed = false;
        for (Map.Entry<String, String> e : rules.langKeys.entrySet()) {
            if (root.has(e.getKey()) && !root.has(e.getValue())) {
                root.add(e.getValue(), root.get(e.getKey()));
                changed = true;
            }
        }
        return changed;
    }

    private void upgradeShader(String path, boolean fragment) {
        String source;
        try {
            source = pack.readText(path);
        } catch (IOException e) {
            overlay.warn(path + ": could not be read, left as it is (" + truncate(e.getMessage()) + ")");
            return;
        }
        ShaderUpgrader.Result result = ShaderUpgrader.upgrade(source, fragment);
        if (result.problem() != null) {
            overlay.warn(path + ": " + result.problem());
        }
        if (result.changed()) {
            overlay.put(path, result.source().getBytes(StandardCharsets.UTF_8));
            overlay.info(path + ": upgraded to GLSL 150");
        }
    }

    private void applySplits() throws IOException {
        Map<String, List<Rules.Split>> byFrom = new LinkedHashMap<>();
        for (Rules.Split s : rules.splits) {
            byFrom.computeIfAbsent(s.from(), k -> new ArrayList<>()).add(s);
        }
        for (Map.Entry<String, List<Rules.Split>> group : byFrom.entrySet()) {
            String from = group.getKey();
            String fromPath = Res.blockstateFile(from);
            if (!provided(fromPath)) {
                continue;
            }
            JsonElement rootEl = readJson(fromPath);
            if (rootEl == null || !rootEl.isJsonObject()) {
                continue;
            }
            JsonObject root = rootEl.getAsJsonObject();
            if (!root.has("variants")) {
                overlay.warn(fromPath + ": multipart blockstate could not be split automatically");
                continue;
            }
            Map<String, JsonObject> outputs = new LinkedHashMap<>();
            JsonObject leftover = new JsonObject();
            for (Map.Entry<String, JsonElement> e : root.getAsJsonObject("variants").entrySet()) {
                VariantKey parsed = VariantKey.parse(e.getKey());
                Rules.Split rule = null;
                for (Rules.Split s : group.getValue()) {
                    if (parsed.matches(s.when())) {
                        rule = s;
                        break;
                    }
                }
                if (rule == null) {
                    leftover.add(e.getKey(), e.getValue());
                    continue;
                }
                Map<String, String> props = new LinkedHashMap<>(parsed.properties());
                rule.removeProperties().forEach(props::remove);
                props.putAll(rule.set());
                outputs.computeIfAbsent(rule.to(), k -> new JsonObject()).add(new VariantKey(props).toString(), e.getValue());
            }
            if (outputs.isEmpty()) {
                continue;
            }
            for (Map.Entry<String, JsonObject> out : outputs.entrySet()) {
                String target = out.getKey();
                String targetPath = Res.blockstateFile(target);
                if (!target.equals(from) && pack.exists(targetPath)) {
                    overlay.warn(targetPath + " already exists in the pack; not overwritten by the " + from + " split");
                    continue;
                }
                JsonObject merged;
                if (target.equals(from)) {
                    merged = leftover.deepCopy();
                    out.getValue().entrySet().forEach(v -> merged.add(v.getKey(), v.getValue()));
                } else {
                    merged = out.getValue();
                }
                JsonObject doc = new JsonObject();
                doc.add("variants", merged);
                remapBlockstate(doc, targetPath);
                json.put(targetPath, doc);
                overlay.info(fromPath + ": split into " + targetPath + " (" + merged.size() + " variant(s))");
            }
            if (!outputs.containsKey(from)) {
                JsonObject doc = new JsonObject();
                doc.add("variants", leftover);
                json.put(fromPath, doc);
            }
        }
    }

    private void applyDefinedStates() throws IOException {
        for (Map.Entry<String, Map<String, JsonElement>> block : rules.states.entrySet()) {
            String path = Res.blockstateFile(block.getKey());
            JsonObject root = null;
            if (provided(path)) {
                JsonElement el = readJson(path);
                root = el != null && el.isJsonObject() ? el.getAsJsonObject() : null;
            }
            if (root == null) {
                root = new JsonObject();
                root.add("variants", new JsonObject());
            }
            if (root.has("variants")) {
                JsonObject variants = root.getAsJsonObject("variants");
                block.getValue().forEach((key, model) -> variants.add(VariantKey.parse(key).toString(), model.deepCopy()));
            } else {
                JsonArray multipart = root.has("multipart") ? root.getAsJsonArray("multipart") : new JsonArray();
                root.add("multipart", multipart);
                block.getValue().forEach((key, model) -> {
                    JsonObject when = new JsonObject();
                    VariantKey.parse(key).properties().forEach(when::addProperty);
                    JsonObject kase = new JsonObject();
                    kase.add("when", when);
                    kase.add("apply", model.deepCopy());
                    multipart.add(kase);
                });
            }
            json.put(path, root);
            overlay.info(path + ": " + block.getValue().size() + " state(s) defined by rules");
        }
    }

    private void applyItemOverrides() throws IOException {
        for (Map.Entry<String, List<JsonObject>> item : rules.items.entrySet()) {
            String ns = Res.namespace(item.getKey());
            String name = Res.path(item.getKey());
            String path = "assets/" + ns + "/models/item/" + name + ".json";
            JsonObject model = null;
            if (provided(path)) {
                JsonElement el = readJson(path);
                model = el != null && el.isJsonObject() ? el.getAsJsonObject() : null;
            }
            if (model == null) {
                model = new JsonObject();
                model.addProperty("parent", "minecraft:item/generated");
                JsonObject textures = new JsonObject();
                textures.addProperty("layer0", ns + ":item/" + name);
                model.add("textures", textures);
                overlay.warn(path + ": created with a generated parent - if " + item.getKey() + " is a block item, add its model to the pack");
            }
            List<JsonObject> list = new ArrayList<>();
            if (model.has("overrides")) {
                model.getAsJsonArray("overrides").forEach(o -> list.add(o.getAsJsonObject()));
            }
            item.getValue().forEach(o -> list.add(o.deepCopy()));
            list.sort(Comparator.comparingDouble(o -> {
                JsonObject predicate = o.getAsJsonObject("predicate");
                return predicate != null && predicate.has("custom_model_data") ? predicate.get("custom_model_data").getAsDouble() : 0.0;
            }));
            JsonArray sorted = new JsonArray();
            list.forEach(sorted::add);
            model.add("overrides", sorted);
            json.put(path, model);
            overlay.info(path + ": " + item.getValue().size() + " item override(s) defined by rules");
        }
    }

    private void generateAtlas() throws IOException {
        Map<String, Set<String>> textures = new LinkedHashMap<>(modelTextures);
        for (Map.Entry<String, JsonElement> e : json.entrySet()) {
            if (e.getKey().matches("^assets/[^/]+/models/.+\\.json$")) {
                textures.put(e.getKey(), texturesOf(e.getValue()));
            }
        }
        Set<String> referenced = new TreeSet<>();
        textures.values().forEach(referenced::addAll);
        Set<String> singles = new TreeSet<>();
        Set<String> directories = new TreeSet<>();
        for (String id : referenced) {
            String texturePath = Res.path(id);
            int slash = texturePath.indexOf('/');
            String top = slash > 0 ? texturePath.substring(0, slash) : "";
            if (top.equals("block") || top.equals("item")) {
                continue;
            }
            if (top.isEmpty() || VANILLA_TEXTURE_ROOTS.contains(top)) {
                singles.add(id);
            } else {
                directories.add(top);
            }
            if (vanilla != null && !provided(Res.textureFile(id)) && !vanilla.test(Res.textureFile(id))) {
                overlay.warn("texture " + id + " is used by a model but exists neither in the pack nor in the game");
            }
        }
        if (singles.isEmpty() && directories.isEmpty()) {
            return;
        }
        String atlasPath = "assets/minecraft/atlases/blocks.json";
        JsonObject atlas = null;
        if (provided(atlasPath)) {
            JsonElement el = readJson(atlasPath);
            atlas = el != null && el.isJsonObject() ? el.getAsJsonObject() : null;
        }
        if (atlas == null) {
            atlas = new JsonObject();
        }
        JsonArray sources = atlas.has("sources") ? atlas.getAsJsonArray("sources") : new JsonArray();
        atlas.add("sources", sources);
        for (String dir : directories) {
            JsonObject s = new JsonObject();
            s.addProperty("type", "directory");
            s.addProperty("source", dir);
            s.addProperty("prefix", dir + "/");
            sources.add(s);
        }
        for (String id : singles) {
            JsonObject s = new JsonObject();
            s.addProperty("type", "single");
            s.addProperty("resource", id);
            sources.add(s);
        }
        json.put(atlasPath, atlas);
        overlay.info(atlasPath + ": " + directories.size() + " folder(s) and " + singles.size()
            + " texture(s) added to the block atlas");
    }

    private void restoreLegacyModels() {
        LegacyVanilla legacy = LegacyVanilla.get();
        Set<String> wanted = new LinkedHashSet<>(modelRefs);
        for (Map.Entry<String, JsonElement> e : json.entrySet()) {
            if (e.getKey().matches("^assets/[^/]+/blockstates/.+\\.json$")) {
                wanted.addAll(blockstateRefs(e.getValue()));
            } else if (e.getKey().matches("^assets/[^/]+/models/.+\\.json$")) {
                wanted.addAll(modelRefsOf(e.getValue()));
            }
        }
        for (Map.Entry<String, JsonObject> block : legacy.blockstates.entrySet()) {
            String path = "assets/minecraft/blockstates/" + block.getKey() + ".json";
            if (provided(path)) {
                continue;
            }
            Set<String> used = blockstateRefs(block.getValue());
            boolean ownModels = used.stream().anyMatch(id -> legacy.models.containsKey(Res.path(id)) && provided(Res.modelFile(id)));
            if (!ownModels) {
                continue;
            }
            json.put(path, block.getValue().deepCopy());
            wanted.addAll(used);
            overlay.info(path + ": 1.16.5 block states restored so the pack's own models are shown");
        }
        Deque<String> queue = new ArrayDeque<>(wanted);
        Set<String> seen = new HashSet<>();
        List<String> restored = new ArrayList<>();
        while (!queue.isEmpty()) {
            String id = Res.normalize(queue.poll());
            if (!seen.add(id) || !id.startsWith("minecraft:")) {
                continue;
            }
            JsonObject model = legacy.models.get(Res.path(id));
            if (model == null || provided(Res.modelFile(id))) {
                continue;
            }
            JsonObject copy = model.deepCopy();
            remapModel(copy);
            json.put(Res.modelFile(id), copy);
            restored.add(Res.path(id));
            queue.addAll(modelRefsOf(copy));
        }
        if (!restored.isEmpty()) {
            overlay.info(restored.size() + " model(s) that 1.20.1 no longer ships were restored from 1.16.5: "
                + String.join(", ", restored.subList(0, Math.min(12, restored.size()))) + (restored.size() > 12 ? ", ..." : ""));
        }
    }

    private void upgradeSounds() {
        LegacyVanilla legacy = LegacyVanilla.get();
        JsonObject sounds = null;
        if (provided(SOUNDS)) {
            JsonElement el = readJson(SOUNDS);
            if (el == null || !el.isJsonObject()) {
                return;
            }
            sounds = el.getAsJsonObject();
        }
        boolean changed = false;
        if (sounds != null) {
            for (Map.Entry<String, String> r : legacy.renamedSounds.entrySet()) {
                if (sounds.has(r.getKey()) && !sounds.has(r.getValue())) {
                    sounds.add(r.getValue(), sounds.remove(r.getKey()));
                    overlay.info(SOUNDS + ": sound event " + r.getKey() + " -> " + r.getValue());
                    changed = true;
                }
            }
        }
        for (Map.Entry<String, JsonObject> e : legacy.legacySounds.entrySet()) {
            String event = e.getKey();
            if (sounds != null && sounds.has(event)) {
                continue;
            }
            Set<String> current = legacy.currentSoundFiles.get(event);
            JsonArray kept = new JsonArray();
            boolean own = false;
            JsonArray entries = e.getValue().has("sounds") ? e.getValue().getAsJsonArray("sounds") : new JsonArray();
            for (JsonElement entry : entries) {
                String name = soundName(entry);
                if (name == null) {
                    continue;
                }
                boolean inPack = pack.exists("assets/" + Res.namespace(name) + "/sounds/" + Res.path(name) + ".ogg");
                boolean inGame = current.contains(Res.path(name));
                if (inPack && !inGame) {
                    own = true;
                }
                if (inPack || inGame) {
                    kept.add(entry.deepCopy());
                }
            }
            if (!own) {
                continue;
            }
            if (sounds == null) {
                sounds = new JsonObject();
            }
            JsonObject definition = e.getValue().deepCopy();
            definition.addProperty("replace", true);
            definition.add("sounds", kept);
            sounds.add(event, definition);
            overlay.info(SOUNDS + ": " + event + " plays the pack's 1.16.5 sound files again");
            changed = true;
        }
        if (changed) {
            json.put(SOUNDS, sounds);
        }
    }

    private static String soundName(JsonElement entry) {
        if (entry.isJsonPrimitive()) {
            return Res.normalize(entry.getAsString());
        }
        if (entry.isJsonObject() && entry.getAsJsonObject().has("name")) {
            JsonObject o = entry.getAsJsonObject();
            if (o.has("type") && "event".equals(o.get("type").getAsString())) {
                return null;
            }
            return Res.normalize(o.get("name").getAsString());
        }
        return null;
    }

    private void addImplicitGlyphPages() throws IOException {
        Set<Integer> taken = new HashSet<>(LegacyVanilla.get().bitmapChars);
        if (pack.exists(DEFAULT_FONT)) {
            JsonElement own = readJson(DEFAULT_FONT);
            if (own != null && own.isJsonObject() && own.getAsJsonObject().has("providers")) {
                for (JsonElement p : own.getAsJsonObject().getAsJsonArray("providers")) {
                    if (!p.isJsonObject()) {
                        continue;
                    }
                    JsonObject provider = p.getAsJsonObject();
                    if (provider.has("chars") && provider.get("chars").isJsonArray()) {
                        provider.getAsJsonArray("chars").forEach(r -> r.getAsString().codePoints().filter(c -> c != 0).forEach(taken::add));
                    }
                    if (provider.has("advances") && provider.get("advances").isJsonObject()) {
                        provider.getAsJsonObject("advances").keySet().forEach(k -> k.codePoints().forEach(taken::add));
                    }
                }
            }
        }
        List<JsonObject> pages = fonts.implicitPages(taken::contains);
        if (pages.isEmpty()) {
            return;
        }
        JsonObject font = null;
        if (provided(DEFAULT_FONT)) {
            JsonElement el = readJson(DEFAULT_FONT);
            font = el != null && el.isJsonObject() ? el.getAsJsonObject() : null;
        }
        if (font == null) {
            font = new JsonObject();
        }
        JsonArray providers = font.has("providers") && font.get("providers").isJsonArray() ? font.getAsJsonArray("providers") : new JsonArray();
        pages.forEach(providers::add);
        font.add("providers", providers);
        json.put(DEFAULT_FONT, font);
    }

    private static Set<String> modelRefsOf(JsonElement model) {
        Set<String> out = new LinkedHashSet<>();
        if (model == null || !model.isJsonObject()) {
            return out;
        }
        JsonObject o = model.getAsJsonObject();
        if (o.has("parent") && o.get("parent").isJsonPrimitive()) {
            out.add(Res.normalize(o.get("parent").getAsString()));
        }
        if (o.has("overrides") && o.get("overrides").isJsonArray()) {
            for (JsonElement override : o.getAsJsonArray("overrides")) {
                if (override.isJsonObject() && override.getAsJsonObject().has("model")) {
                    out.add(Res.normalize(override.getAsJsonObject().get("model").getAsString()));
                }
            }
        }
        return out;
    }

    private static Set<String> blockstateRefs(JsonElement blockstate) {
        Set<String> out = new LinkedHashSet<>();
        if (blockstate == null || !blockstate.isJsonObject()) {
            return out;
        }
        JsonObject o = blockstate.getAsJsonObject();
        List<JsonElement> holders = new ArrayList<>();
        if (o.has("variants") && o.get("variants").isJsonObject()) {
            o.getAsJsonObject("variants").entrySet().forEach(e -> holders.add(e.getValue()));
        }
        if (o.has("multipart") && o.get("multipart").isJsonArray()) {
            for (JsonElement c : o.getAsJsonArray("multipart")) {
                if (c.isJsonObject() && c.getAsJsonObject().has("apply")) {
                    holders.add(c.getAsJsonObject().get("apply"));
                }
            }
        }
        for (JsonElement h : holders) {
            List<JsonElement> list = new ArrayList<>();
            if (h.isJsonArray()) {
                h.getAsJsonArray().forEach(list::add);
            } else {
                list.add(h);
            }
            for (JsonElement item : list) {
                if (item.isJsonObject() && item.getAsJsonObject().has("model")) {
                    out.add(Res.normalize(item.getAsJsonObject().get("model").getAsString()));
                }
            }
        }
        return out;
    }

    private static Set<String> texturesOf(JsonElement model) {
        Set<String> out = new LinkedHashSet<>();
        if (model == null || !model.isJsonObject()) {
            return out;
        }
        JsonElement textures = model.getAsJsonObject().get("textures");
        if (textures == null || !textures.isJsonObject()) {
            return out;
        }
        for (Map.Entry<String, JsonElement> e : textures.getAsJsonObject().entrySet()) {
            if (e.getValue().isJsonPrimitive() && !e.getValue().getAsString().startsWith("#")) {
                out.add(Res.normalize(e.getValue().getAsString()));
            }
        }
        return out;
    }
}
