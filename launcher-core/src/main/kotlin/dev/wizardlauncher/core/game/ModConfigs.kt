package dev.wizardlauncher.core.game

import com.google.gson.GsonBuilder
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import dev.wizardlauncher.core.Log
import java.nio.file.Files
import java.nio.file.Path

object ModConfigs {
    private val REQUIRED = mapOf(
        "moreculling.toml" to mapOf("useBlockStateCulling" to "false"),
    )

    private val REQUIRED_JSON = mapOf(
        "sodium-extra-options.json" to mapOf(
            "particle_settings.particles" to true,
            "extra_settings.toasts" to true,
            "extra_settings.advancement_toast" to true,
            "extra_settings.system_toast" to true,
            "render_settings.armor_stand" to true,
            "render_settings.item_frame" to true,
            "animation_settings.animation" to true,
            "animation_settings.portal" to true,
        ),
    )

    fun enforce(gameDir: Path) {
        val config = gameDir.resolve("config")
        for ((file, values) in REQUIRED_JSON) {
            for (target in listOf(config.resolve("yosbr").resolve("config").resolve(file), config.resolve(file))) {
                runCatching {
                    if (patchJson(target, values)) Log.info("Set ${values.keys.joinToString()} in config/$file")
                }.onFailure { Log.file("Could not update config/$file: ${it.message}") }
            }
        }
        for ((file, values) in REQUIRED) {
            val live = config.resolve(file)
            val defaults = config.resolve("yosbr").resolve("config").resolve(file)
            runCatching {
                if (Files.isRegularFile(defaults)) {
                    patchToml(defaults, values)
                    if (!Files.exists(live)) {
                        Files.createDirectories(live.parent)
                        Files.copy(defaults, live)
                    }
                }
                if (patchToml(live, values)) Log.info("Set ${values.keys.joinToString()} in config/$file")
            }.onFailure { Log.file("Could not update config/$file: ${it.message}") }
        }
    }

    fun patchToml(file: Path, values: Map<String, String>): Boolean {
        val lines = if (Files.isRegularFile(file)) Files.readAllLines(file).toMutableList() else mutableListOf()
        var changed = false
        for ((key, value) in values) {
            val line = "$key = $value"
            val firstTable = lines.indexOfFirst { it.trimStart().startsWith("[") }.let { if (it < 0) lines.size else it }
            val idx = lines.subList(0, firstTable).indexOfFirst { it.substringBefore('=').trim() == key }
            when {
                idx >= 0 && lines[idx].trim() == line -> Unit
                idx >= 0 -> { lines[idx] = line; changed = true }
                else -> { lines.add(firstTable, line); changed = true }
            }
        }
        if (changed) {
            Files.createDirectories(file.parent)
            Files.write(file, lines)
        }
        return changed
    }

    fun patchJson(file: Path, values: Map<String, Boolean>): Boolean {
        if (!Files.isRegularFile(file)) return false
        val root = JsonParser.parseString(Files.readString(file)).takeIf { it.isJsonObject }?.asJsonObject ?: return false
        var changed = false
        for ((path, value) in values) {
            val keys = path.split('.')
            var node = root
            for (key in keys.dropLast(1)) {
                node = node.get(key)?.takeIf { it.isJsonObject }?.asJsonObject ?: JsonObject().also { node.add(key, it) }
            }
            val leaf = keys.last()
            val current = node.get(leaf)
            if (current == null || !current.isJsonPrimitive || !current.asJsonPrimitive.isBoolean || current.asBoolean != value) {
                node.addProperty(leaf, value)
                changed = true
            }
        }
        if (changed) Files.writeString(file, GsonBuilder().setPrettyPrinting().create().toJson(root))
        return changed
    }
}
