package dev.wizardlauncher.core.library

import com.google.gson.JsonElement
import com.google.gson.JsonParser
import dev.wizardlauncher.core.LauncherException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.util.zip.ZipFile

data class ModInfo(
    val file: String,
    val id: String,
    val name: String,
    val version: String,
    val description: String,
    val authors: List<String>,
    val enabled: Boolean,
    val managed: Boolean,
    val hasIcon: Boolean,
    val sizeBytes: Long,
)

class ModLibrary(private val modsDir: Path, private val managed: () -> Set<String>) {
    fun list(): List<ModInfo> {
        if (!Files.isDirectory(modsDir)) return emptyList()
        val owned = managed().map { it.substringAfterLast('/') }.toSet()
        return Files.list(modsDir).use { s -> s.filter { isModFile(it.fileName.toString()) }.toList() }
            .mapNotNull { runCatching { read(it, owned) }.getOrNull() }
            .sortedWith(compareBy({ !it.enabled }, { it.name.lowercase() }))
    }

    private fun isModFile(name: String) = name.endsWith(".jar") || name.endsWith(".jar.disabled")

    private fun read(file: Path, owned: Set<String>): ModInfo {
        val fileName = file.fileName.toString()
        val enabled = fileName.endsWith(".jar")
        val base = fileName.removeSuffix(".disabled")
        var meta: com.google.gson.JsonObject? = null
        var hasIcon = false
        ZipFile(file.toFile()).use { zip ->
            zip.getEntry("fabric.mod.json")?.let { entry ->
                meta = JsonParser.parseString(zip.getInputStream(entry).readBytes().toString(Charsets.UTF_8)).asJsonObject
                hasIcon = iconPath(meta!!)?.let { zip.getEntry(it) != null } ?: false
            }
        }
        val m = meta
        return ModInfo(
            file = fileName,
            id = m?.get("id")?.asString ?: base.removeSuffix(".jar"),
            name = m?.get("name")?.asString ?: base.removeSuffix(".jar"),
            version = m?.get("version")?.asString ?: "",
            description = m?.get("description")?.asString ?: "",
            authors = m?.getAsJsonArray("authors")?.mapNotNull(::person) ?: emptyList(),
            enabled = enabled,
            managed = base in owned || base == "wizard-legacy-packs.jar",
            hasIcon = hasIcon,
            sizeBytes = Files.size(file),
        )
    }

    private fun person(e: JsonElement): String? = when {
        e.isJsonPrimitive -> e.asString
        e.isJsonObject -> e.asJsonObject.get("name")?.asString
        else -> null
    }

    private fun iconPath(meta: com.google.gson.JsonObject): String? {
        val icon = meta.get("icon") ?: return null
        return when {
            icon.isJsonPrimitive -> icon.asString
            icon.isJsonObject -> icon.asJsonObject.entrySet().maxByOrNull { it.key.toIntOrNull() ?: 0 }?.value?.asString
            else -> null
        }?.removePrefix("/")
    }

    fun icon(fileName: String): ByteArray? {
        val file = resolve(fileName) ?: return null
        return ZipFile(file.toFile()).use { zip ->
            val meta = zip.getEntry("fabric.mod.json")?.let { JsonParser.parseString(zip.getInputStream(it).readBytes().toString(Charsets.UTF_8)).asJsonObject }
                ?: return null
            val entry = iconPath(meta)?.let(zip::getEntry) ?: return null
            if (entry.size > 2L shl 20) return null
            zip.getInputStream(entry).readBytes()
        }
    }

    fun setEnabled(fileName: String, enabled: Boolean) {
        val file = resolve(fileName) ?: throw LauncherException("That mod is no longer installed.")
        val base = fileName.removeSuffix(".disabled")
        val target = modsDir.resolve(if (enabled) base else "$base.disabled")
        if (file != target) Files.move(file, target, StandardCopyOption.REPLACE_EXISTING)
    }

    fun remove(fileName: String) {
        val file = resolve(fileName) ?: return
        val owned = managed().map { it.substringAfterLast('/') }.toSet()
        if (fileName.removeSuffix(".disabled") in owned) throw LauncherException("This mod is part of the modpack. Disable it instead.")
        Files.deleteIfExists(file)
    }

    fun import(files: List<Path>): Int {
        Files.createDirectories(modsDir)
        var count = 0
        for (source in files) {
            val name = source.fileName.toString()
            if (!name.endsWith(".jar") || !Files.isRegularFile(source)) continue
            val valid = runCatching { ZipFile(source.toFile()).use { it.getEntry("fabric.mod.json") != null } }.getOrDefault(false)
            if (!valid) throw LauncherException("$name is not a Fabric mod.")
            Files.copy(source, modsDir.resolve(name), StandardCopyOption.REPLACE_EXISTING)
            count++
        }
        return count
    }

    private fun resolve(fileName: String): Path? {
        if (!isModFile(fileName) || fileName.contains('/') || fileName.contains('\\') || fileName.startsWith(".")) return null
        return modsDir.resolve(fileName).takeIf(Files::isRegularFile)
    }
}
