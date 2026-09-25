package dev.wizardlauncher.core.game

import dev.wizardlauncher.core.Log
import java.nio.file.Files
import java.nio.file.Path

object ModConfigs {
    private val REQUIRED = mapOf(
        "moreculling.toml" to mapOf("useBlockStateCulling" to "false"),
    )

    fun enforce(gameDir: Path) {
        val config = gameDir.resolve("config")
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
}
