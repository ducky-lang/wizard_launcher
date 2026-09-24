package dev.wizardlauncher.pack

import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser

/**
 * Declarative rules for moving a pack between game versions - and for
 * *defining block states* the pack wants to exist.
 *
 * Rules come from three places, applied in order, later ones winning:
 *  1. the built-in 1.16.5 -> 1.20.1 table shipped with the converter;
 *  2. `wizard-states.json` at the root of the pack itself;
 *  3. any extra files passed on the command line / from the launcher's data folder.
 *
 * The format, all sections optional:
 * ```
 * {
 *   "format": 1,
 *   "copy_files":        [{"from": "assets/...", "to": "assets/..."}],
 *   "rename_references": {"models": {"ns:old": "ns:new"}, "textures": {...}},
 *   "split_blockstates": [{"from": "minecraft:cauldron", "when": {"level": "1|2|3"},
 *                          "to": "minecraft:water_cauldron", "remove_properties": [], "set": {}}],
 *   "lang_keys":         {"old.key": "new.key"},
 *   "states": {                      // define / override block states -> models
 *     "minecraft:note_block": {
 *       "instrument=harp,note=1,powered=false": {"model": "wizard:block/crystal_ball"}
 *     }
 *   },
 *   "items": {                       // custom_model_data (or any predicate) -> model
 *     "minecraft:stick": [{"predicate": {"custom_model_data": 1001}, "model": "wizard:item/wand"}]
 *   }
 * }
 * ```
 */
data class StateRules(
    val copyFiles: List<Pair<String, String>> = emptyList(),
    val modelRenames: Map<String, String> = emptyMap(),
    val textureRenames: Map<String, String> = emptyMap(),
    val splits: List<Split> = emptyList(),
    val langKeys: Map<String, String> = emptyMap(),
    val states: Map<String, Map<String, JsonElement>> = emptyMap(),
    val items: Map<String, List<JsonObject>> = emptyMap(),
) {
    data class Split(
        val from: String,
        val to: String,
        val `when`: Map<String, Set<String>>,
        val removeProperties: Set<String>,
        val set: Map<String, String>,
    )

    operator fun plus(other: StateRules) = StateRules(
        copyFiles = copyFiles + other.copyFiles,
        modelRenames = modelRenames + other.modelRenames,
        textureRenames = textureRenames + other.textureRenames,
        splits = splits + other.splits,
        langKeys = langKeys + other.langKeys,
        states = (states.keys + other.states.keys).associateWith { (states[it] ?: emptyMap()) + (other.states[it] ?: emptyMap()) },
        items = (items.keys + other.items.keys).associateWith { (items[it] ?: emptyList()) + (other.items[it] ?: emptyList()) },
    )

    companion object {
        fun builtin(): StateRules {
            val stream = StateRules::class.java.getResourceAsStream("rules-1.16.5-to-1.20.1.json")
                ?: error("built-in rules missing from the converter jar")
            return parse(stream.use { it.readBytes().toString(Charsets.UTF_8) }, "built-in")
        }

        /** Parses a rules file. Malformed entries throw with a precise message. */
        fun parse(text: String, origin: String): StateRules {
            val root = try {
                JsonParser.parseString(text).asJsonObject
            } catch (e: Exception) {
                throw IllegalArgumentException("$origin: not a JSON object (${e.message})")
            }
            val format = root.get("format")?.asInt ?: 1
            require(format == 1) { "$origin: unsupported rules format $format" }

            val copies = root.arr("copy_files").map {
                val o = it.asJsonObject
                checkedPath(o.str("from", origin), origin) to checkedPath(o.str("to", origin), origin)
            }
            val renames = root.getAsJsonObject("rename_references") ?: JsonObject()
            val splits = root.arr("split_blockstates").map {
                val o = it.asJsonObject
                Split(
                    from = id(o.str("from", origin)),
                    to = id(o.str("to", origin)),
                    `when` = (o.getAsJsonObject("when") ?: JsonObject()).entrySet()
                        .associate { (k, v) -> k to v.asString.split('|').map(String::trim).toSet() },
                    removeProperties = o.arr("remove_properties").map { p -> p.asString }.toSet(),
                    set = (o.getAsJsonObject("set") ?: JsonObject()).entrySet().associate { (k, v) -> k to v.asString },
                )
            }
            val states = (root.getAsJsonObject("states") ?: JsonObject()).entrySet().associate { (block, variants) ->
                require(variants.isJsonObject) { "$origin: states.$block must be an object of variant -> model" }
                id(block) to variants.asJsonObject.entrySet().associate { (key, model) ->
                    VariantKey.parse(key) // validates syntax early
                    key to model
                }
            }
            val items = (root.getAsJsonObject("items") ?: JsonObject()).entrySet().associate { (item, list) ->
                require(list.isJsonArray) { "$origin: items.$item must be an array of overrides" }
                id(item) to list.asJsonArray.map { o ->
                    val obj = o.asJsonObject
                    require(obj.has("model") && obj.has("predicate")) { "$origin: items.$item entries need 'predicate' and 'model'" }
                    obj.deepCopy().also { it.addProperty("model", id(it.get("model").asString)) }
                }
            }
            return StateRules(
                copyFiles = copies,
                modelRenames = renames.map("models"),
                textureRenames = renames.map("textures"),
                splits = splits,
                langKeys = (root.getAsJsonObject("lang_keys") ?: JsonObject()).entrySet().associate { (k, v) -> k to v.asString },
                states = states,
                items = items,
            )
        }

        private fun JsonObject.arr(name: String): JsonArray = getAsJsonArray(name) ?: JsonArray()
        private fun JsonObject.str(name: String, origin: String): String =
            get(name)?.takeIf { it.isJsonPrimitive }?.asString ?: throw IllegalArgumentException("$origin: missing '$name'")
        private fun JsonObject.map(name: String): Map<String, String> =
            (getAsJsonObject(name) ?: JsonObject()).entrySet().associate { (k, v) -> id(k) to id(v.asString) }

        private fun checkedPath(path: String, origin: String): String {
            require(PackSource.safePath(path) && path.startsWith("assets/")) { "$origin: unsafe path '$path'" }
            return path
        }

        /** Normalises `stone` to `minecraft:stone`. */
        fun id(raw: String): String = if (':' in raw) raw else "minecraft:$raw"
    }
}

/** A blockstate variant key such as `facing=north,half=top`, order-insensitive. */
data class VariantKey(val properties: Map<String, String>) {
    override fun toString() = properties.entries.sortedBy { it.key }.joinToString(",") { "${it.key}=${it.value}" }

    fun matches(condition: Map<String, Set<String>>) =
        condition.all { (key, allowed) -> properties[key]?.let { it in allowed } == true }

    companion object {
        fun parse(key: String): VariantKey {
            if (key.isBlank() || key == "normal") return VariantKey(emptyMap())
            return VariantKey(key.split(',').associate { part ->
                val eq = part.indexOf('=')
                require(eq > 0) { "bad variant key '$key'" }
                part.substring(0, eq).trim() to part.substring(eq + 1).trim()
            })
        }
    }
}
