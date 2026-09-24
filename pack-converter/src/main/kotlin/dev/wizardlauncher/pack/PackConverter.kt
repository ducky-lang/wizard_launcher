package dev.wizardlauncher.pack

import com.google.gson.GsonBuilder
import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.google.gson.Strictness
import com.google.gson.stream.JsonReader
import java.io.StringReader
import java.nio.file.Files
import java.nio.file.Path

/**
 * Converts a Minecraft 1.16.5 resource pack (pack_format 6) so that 1.20.1
 * (pack_format 15) loads and renders it the way 1.16.5 did.
 *
 * What 1.16.5 -> 1.20.1 broke, and what is done about each:
 *
 *  | Change in the game                                    | Fix                                        |
 *  |-------------------------------------------------------|--------------------------------------------|
 *  | 1.19.3 texture atlases: only `block/` and `item/`     | generate `atlases/blocks.json` listing     |
 *  |   textures are stitched for models any more           |   every other texture a model uses         |
 *  | 1.17 grass_path -> dirt_path, squid, cauldron split,  | copy files / rewrite references /          |
 *  |   1.19.4 split enchantment glint                      |   split blockstates (see [StateRules])     |
 *  | 1.20 removed the `legacy_unicode` font provider       | [FontConverter]: bitmap pages + spaces     |
 *  | 1.17 post shaders need GLSL 150 core                  | [ShaderUpgrader]                           |
 *  | pack_format 6 is flagged "incompatible"               | pack.mcmeta rewritten to 15                |
 *
 * On top of the version table, a pack (or the launcher) can *define block
 * states and item models* declaratively - see [StateRules].
 *
 * Everything the converter could not translate ends up in
 * `wizard-conversion-report.txt` inside the output pack.
 */
class PackConverter(
    private val vanilla: VanillaAssets? = null,
    private val log: (String) -> Unit = {},
) {
    private val gson = GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create()

    fun convert(input: Path, output: Path, extraRules: List<Path> = emptyList()): ConversionReport {
        val report = ConversionReport()
        PackSource.open(input).use { source ->
            val rules = loadRules(source, extraRules, report)
            val sink = PackSink(output)
            try {
                Job(source, sink, rules, report).run()
                sink.put(REPORT, report.render().toByteArray())
                sink.close()
            } catch (t: Throwable) {
                sink.abort()
                throw t
            }
        }
        log("Converted pack: ${report.filesRewritten} rewritten, ${report.warnings.size} warning(s)")
        return report
    }

    private fun loadRules(source: PackSource, extra: List<Path>, report: ConversionReport): StateRules {
        var rules = StateRules.builtin()
        if (source.exists(PACK_RULES)) {
            rules += StateRules.parse(source.read(PACK_RULES).toString(Charsets.UTF_8), PACK_RULES)
            report.info("applied state rules from $PACK_RULES")
        }
        for (file in extra) {
            rules += StateRules.parse(Files.readString(file), file.fileName.toString())
            report.info("applied state rules from ${file.fileName}")
        }
        return rules
    }

    private inner class Job(
        val source: PackSource,
        val sink: PackSink,
        val rules: StateRules,
        val report: ConversionReport,
    ) {
        /** output path -> source path (identity, plus the aliases copy rules add). */
        val files = LinkedHashMap<String, String>()
        /** JSON documents that were changed or created, by output path. */
        val json = LinkedHashMap<String, JsonElement>()
        val bytes = LinkedHashMap<String, ByteArray>()

        fun run() {
            source.paths.forEach { files[it] = it }
            applyCopies()
            files.keys.toList().forEach(::rewrite)
            applySplits()
            applyDefinedStates()
            applyItemOverrides()
            generateAtlas()
            write()
        }

        fun providedByPack(path: String) = path in files || path in json || path in bytes

        // ------------------------------------------------------------ copies
        fun applyCopies() {
            for ((from, to) in rules.copyFiles) {
                if (from in files && to !in files) {
                    files[to] = files.getValue(from)
                    report.info("$from -> $to")
                }
            }
        }

        // ------------------------------------------------------------ per-file rewrites
        fun rewrite(path: String) {
            val lower = path.lowercase()
            when {
                path == "pack.mcmeta" -> editJson(path) { root -> fixPackMeta(root.asJsonObject) }
                Regex("^assets/[^/]+/blockstates/.+\\.json$").matches(path) ->
                    editJson(path) { root -> remapBlockstate(root, path) }
                Regex("^assets/[^/]+/models/.+\\.json$").matches(path) ->
                    editJson(path) { root -> remapModel(root.asJsonObject) }
                Regex("^assets/[^/]+/lang/.+\\.json$").matches(path) ->
                    editJson(path) { root -> renameLangKeys(root.asJsonObject) }
                Regex("^assets/[^/]+/font/.+\\.json$").matches(path) -> {
                    val fonts = FontConverter(source, report)
                    editJson(path) { root -> fonts.convert(path, root.asJsonObject) }
                    fonts.generated.forEach { (p, b) -> bytes[p] = b }
                }
                Regex("^assets/[^/]+/shaders/program/.+\\.(vsh|fsh)$").matches(path) -> upgradeShader(path, lower.endsWith(".fsh"))
                Regex("^assets/[^/]+/shaders/core/.+").matches(path) ->
                    report.warn("$path: 1.16 had no core shaders; this file is ignored by 1.20.1 unless written for it")
            }
        }

        fun readJson(path: String): JsonElement? {
            json[path]?.let { return it }
            val text = source.read(files.getValue(path)).toString(Charsets.UTF_8).removePrefix("﻿")
            return try {
                // 1.16's loader tolerated comments and trailing commas in
                // many places; parse leniently so they are normalised away.
                JsonParser.parseReader(JsonReader(StringReader(text)).apply { setStrictness(Strictness.LENIENT) })
            } catch (e: Exception) {
                report.warn("$path: invalid JSON, copied unchanged (${e.message?.take(120)})")
                null
            }
        }

        /** Runs [edit] on a parsed copy; keeps it only if [edit] reports a change. */
        fun editJson(path: String, edit: (JsonElement) -> Boolean) {
            val root = readJson(path) ?: return
            if (edit(root)) json[path] = root
        }

        fun fixPackMeta(root: JsonObject): Boolean {
            val pack = root.getAsJsonObject("pack") ?: JsonObject().also { root.add("pack", it) }
            val old = pack.get("pack_format")?.asInt
            pack.addProperty("pack_format", TARGET_FORMAT)
            root.add("wizard_launcher", JsonObject().apply {
                addProperty("converted_from_pack_format", old ?: -1)
                addProperty("target", "1.20.1")
            })
            report.info("pack.mcmeta: pack_format $old -> $TARGET_FORMAT")
            return true
        }

        fun renameModelRef(ref: String): String {
            if (ref.startsWith("#")) return ref
            val id = Res.normalize(ref)
            if (providedByPack(Res.modelFile(id))) return ref
            return rules.modelRenames[id] ?: ref
        }

        fun renameTextureRef(ref: String): String {
            if (ref.startsWith("#")) return ref
            val id = Res.normalize(ref)
            if (providedByPack(Res.textureFile(id))) return ref
            return rules.textureRenames[id] ?: ref
        }

        fun remapModel(model: JsonObject): Boolean {
            var changed = false
            model.get("parent")?.asString?.let { parent ->
                val renamed = renameModelRef(parent)
                if (renamed != parent) { model.addProperty("parent", renamed); changed = true }
            }
            model.getAsJsonObject("textures")?.let { textures ->
                for ((key, value) in textures.entrySet().toList()) {
                    if (!value.isJsonPrimitive) continue
                    val renamed = renameTextureRef(value.asString)
                    if (renamed != value.asString) { textures.addProperty(key, renamed); changed = true }
                }
            }
            model.getAsJsonArray("overrides")?.forEach { o ->
                val override = o.asJsonObject
                val target = override.get("model")?.asString ?: return@forEach
                val renamed = renameModelRef(target)
                if (renamed != target) { override.addProperty("model", renamed); changed = true }
            }
            return changed
        }

        fun remapBlockstate(root: JsonElement, path: String): Boolean {
            var changed = false
            fun visitModelHolder(e: JsonElement) {
                val list = if (e.isJsonArray) e.asJsonArray.toList() else listOf(e)
                list.filter { it.isJsonObject }.map { it.asJsonObject }.forEach { m ->
                    val ref = m.get("model")?.asString ?: return@forEach
                    val renamed = renameModelRef(ref)
                    if (renamed != ref) { m.addProperty("model", renamed); changed = true }
                }
            }
            val obj = root.asJsonObject
            obj.getAsJsonObject("variants")?.entrySet()?.forEach { (_, v) -> visitModelHolder(v) }
            obj.getAsJsonArray("multipart")?.forEach { case ->
                case.asJsonObject.get("apply")?.let(::visitModelHolder)
            }
            if (changed) report.info("$path: model references updated")
            return changed
        }

        fun renameLangKeys(root: JsonObject): Boolean {
            var changed = false
            for ((old, new) in rules.langKeys) {
                val value = root.get(old) ?: continue
                if (!root.has(new)) { root.add(new, value); changed = true }
            }
            return changed
        }

        fun upgradeShader(path: String, fragment: Boolean) {
            val text = source.read(files.getValue(path)).toString(Charsets.UTF_8)
            val result = ShaderUpgrader.upgrade(text, fragment)
            result.problem?.let { report.warn("$path: $it") }
            if (result.changed) {
                bytes[path] = result.source.toByteArray()
                report.info("$path: upgraded to GLSL 150")
            }
        }

        // ------------------------------------------------------------ blockstate splits
        fun applySplits() {
            for ((from, splits) in rules.splits.groupBy { it.from }) {
                val fromPath = Res.blockstateFile(from)
                if (fromPath !in files) continue
                val root = readJson(fromPath)?.asJsonObject ?: continue
                val variants = root.getAsJsonObject("variants")
                if (variants == null) {
                    report.warn("$fromPath: multipart blockstate could not be split automatically")
                    continue
                }
                val outputs = LinkedHashMap<String, JsonObject>()
                val leftover = JsonObject()
                for ((key, value) in variants.entrySet()) {
                    val parsed = VariantKey.parse(key)
                    val rule = splits.firstOrNull { parsed.matches(it.`when`) }
                    if (rule == null) { leftover.add(key, value); continue }
                    val props = parsed.properties.filterKeys { it !in rule.removeProperties } + rule.set
                    outputs.getOrPut(rule.to) { JsonObject() }.add(VariantKey(props).toString(), value)
                }
                if (outputs.isEmpty()) continue
                for ((target, newVariants) in outputs) {
                    val targetPath = Res.blockstateFile(target)
                    if (target != from && targetPath in files && source.exists(targetPath)) {
                        report.warn("$targetPath already exists in the pack; not overwritten by the $from split")
                        continue
                    }
                    val merged = if (target == from) leftover.deepCopy().also { l ->
                        newVariants.entrySet().forEach { (k, v) -> l.add(k, v) }
                    } else newVariants
                    json[targetPath] = JsonObject().apply { add("variants", merged) }
                    if (targetPath !in files) files[targetPath] = targetPath
                    json[targetPath]?.let { remapBlockstate(it, targetPath) }
                    report.info("$fromPath: split into $targetPath (${merged.size()} variant(s))")
                }
                if (from !in outputs.keys) {
                    json[fromPath] = JsonObject().apply { add("variants", leftover) }
                }
            }
        }

        // ------------------------------------------------------------ user-defined states
        fun applyDefinedStates() {
            for ((block, defined) in rules.states) {
                val path = Res.blockstateFile(block)
                val root = (if (path in files || path in json) readJson(path)?.asJsonObject else null)
                    ?: JsonObject().apply { add("variants", JsonObject()) }
                val variants = root.getAsJsonObject("variants")
                if (variants != null) {
                    defined.forEach { (key, model) -> variants.add(VariantKey.parse(key).toString(), model.deepCopy()) }
                } else {
                    val multipart = root.getAsJsonArray("multipart") ?: JsonArray().also { root.add("multipart", it) }
                    defined.forEach { (key, model) ->
                        multipart.add(JsonObject().apply {
                            add("when", JsonObject().apply {
                                VariantKey.parse(key).properties.forEach { (k, v) -> addProperty(k, v) }
                            })
                            add("apply", model.deepCopy())
                        })
                    }
                }
                json[path] = root
                if (path !in files) files[path] = path
                report.info("$path: ${defined.size} state(s) defined by rules")
            }
        }

        fun applyItemOverrides() {
            for ((item, overrides) in rules.items) {
                val (ns, name) = Res.split(item)
                val path = "assets/$ns/models/item/$name.json"
                val model = (if (path in files || path in json) readJson(path)?.asJsonObject else null)
                    ?: JsonObject().apply {
                        addProperty("parent", "minecraft:item/generated")
                        add("textures", JsonObject().apply { addProperty("layer0", "$ns:item/$name") })
                        report.warn("$path: created with a generated parent - if $item is a block item, add its model to the pack")
                    }
                val list = model.getAsJsonArray("overrides") ?: JsonArray()
                overrides.forEach { list.add(it.deepCopy()) }
                // Minecraft picks the LAST matching override, so ascending
                // custom_model_data order is what makes each value select its own model.
                val sorted = list.map { it.asJsonObject }.sortedBy {
                    it.getAsJsonObject("predicate")?.get("custom_model_data")?.asDouble ?: 0.0
                }
                model.add("overrides", JsonArray().apply { sorted.forEach(::add) })
                json[path] = model
                if (path !in files) files[path] = path
                report.info("$path: ${overrides.size} item override(s) defined by rules")
            }
        }

        // ------------------------------------------------------------ atlas
        fun generateAtlas() {
            val referenced = sortedSetOf<String>()
            val modelPaths = (files.keys + json.keys).filter { Regex("^assets/[^/]+/models/.+\\.json$").matches(it) }.toSet()
            for (path in modelPaths) {
                val model = (json[path] ?: readJson(path))?.takeIf { it.isJsonObject }?.asJsonObject ?: continue
                model.getAsJsonObject("textures")?.entrySet()?.forEach { (_, v) ->
                    if (v.isJsonPrimitive && !v.asString.startsWith("#")) referenced += Res.normalize(v.asString)
                }
            }
            val singles = sortedSetOf<String>()
            val directories = sortedSetOf<String>()
            for (id in referenced) {
                val (_, texturePath) = Res.split(id)
                val top = texturePath.substringBefore('/', "")
                when {
                    top == "block" || top == "item" -> Unit // stitched by default
                    top.isEmpty() || top in VANILLA_TEXTURE_ROOTS -> singles += id
                    else -> directories += top
                }
                if (vanilla != null && !providedByPack(Res.textureFile(id)) && !vanilla.has(Res.textureFile(id))) {
                    report.warn("texture $id is used by a model but exists neither in the pack nor in 1.20.1")
                }
            }
            if (singles.isEmpty() && directories.isEmpty()) return

            val atlasPath = "assets/minecraft/atlases/blocks.json"
            val atlas = (if (atlasPath in files) readJson(atlasPath)?.asJsonObject else null) ?: JsonObject()
            val sources = atlas.getAsJsonArray("sources") ?: JsonArray().also { atlas.add("sources", it) }
            directories.forEach { dir ->
                sources.add(JsonObject().apply {
                    addProperty("type", "directory"); addProperty("source", dir); addProperty("prefix", "$dir/")
                })
            }
            singles.forEach { id ->
                sources.add(JsonObject().apply { addProperty("type", "single"); addProperty("resource", id) })
            }
            json[atlasPath] = atlas
            if (atlasPath !in files) files[atlasPath] = atlasPath
            report.info("$atlasPath: ${directories.size} folder(s) and ${singles.size} texture(s) added to the block atlas " +
                "(1.19.3+ no longer stitches textures outside block/ and item/)")
        }

        // ------------------------------------------------------------ output
        fun write() {
            val all = LinkedHashSet<String>().apply { addAll(files.keys); addAll(json.keys); addAll(bytes.keys) }
            for (path in all) {
                when {
                    path in bytes -> { sink.put(path, bytes.getValue(path)); report.filesRewritten++ }
                    path in json -> { sink.put(path, gson.toJson(json.getValue(path)).toByteArray()); report.filesRewritten++ }
                    else -> sink.copy(path, source, files.getValue(path))
                }
                report.filesWritten++
            }
        }
    }

    companion object {
        const val TARGET_FORMAT = 15
        const val PACK_RULES = "wizard-states.json"
        const val REPORT = "wizard-conversion-report.txt"

        /** Texture folders vanilla keeps outside the block atlas. Referenced files there get single entries. */
        val VANILLA_TEXTURE_ROOTS = setOf(
            "entity", "misc", "environment", "gui", "font", "painting", "mob_effect", "particle",
            "map", "models", "colormap", "effect", "trims",
        )
    }
}
