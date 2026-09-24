package dev.wizardlauncher.core.library

import com.google.gson.JsonElement
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.install.SafeZip
import dev.wizardlauncher.legacy.LegacyTranslator
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.util.zip.ZipFile

data class PackInfo(
    val name: String,
    val description: String,
    val format: Int,
    val legacy: Boolean,
    val enabled: Boolean,
    val folder: Boolean,
    val hasIcon: Boolean,
)

class PackLibrary(private val gameDir: Path) {
    private val dir: Path get() = gameDir.resolve("resourcepacks")
    private val options: Path get() = gameDir.resolve("options.txt")

    fun list(): List<PackInfo> {
        if (!Files.isDirectory(dir)) return emptyList()
        val enabled = enabledPacks()
        return Files.list(dir).use { s -> s.toList() }
            .filter { Files.isDirectory(it) || it.fileName.toString().endsWith(".zip") }
            .mapNotNull { path ->
                val name = path.fileName.toString()
                val meta = runCatching { read(path, "pack.mcmeta") }.getOrNull() ?: return@mapNotNull null
                val format = LegacyTranslator.readFormat(meta)
                val description = runCatching {
                    val root = com.google.gson.JsonParser.parseString(String(meta, Charsets.UTF_8)).asJsonObject
                    text(root.getAsJsonObject("pack").get("description"))
                }.getOrDefault("")
                PackInfo(name, description, format, LegacyTranslator.needsTranslation(format), "file/$name" in enabled,
                    Files.isDirectory(path), runCatching { read(path, "pack.png") != null }.getOrDefault(false))
            }
            .sortedWith(compareBy({ !it.enabled }, { it.name.lowercase() }))
    }

    private fun text(e: JsonElement?): String = when {
        e == null || e.isJsonNull -> ""
        e.isJsonPrimitive -> e.asString.replace(Regex("§."), "")
        e.isJsonArray -> e.asJsonArray.joinToString("") { text(it) }
        e.isJsonObject -> (e.asJsonObject.get("text")?.asString ?: "") + (e.asJsonObject.getAsJsonArray("extra")?.joinToString("") { text(it) } ?: "")
        else -> ""
    }

    private fun read(pack: Path, entry: String): ByteArray? =
        if (Files.isDirectory(pack)) pack.resolve(entry).takeIf(Files::isRegularFile)?.let(Files::readAllBytes)
        else ZipFile(pack.toFile()).use { zip ->
            val direct = zip.getEntry(entry)
            val nested = direct ?: zip.entries().asSequence().firstOrNull { it.name.endsWith("/$entry") && it.name.count { c -> c == '/' } == 1 }
            nested?.let { zip.getInputStream(it).readBytes() }
        }

    fun icon(name: String): ByteArray? = resolve(name)?.let { runCatching { read(it, "pack.png") }.getOrNull() }

    fun enabledPacks(): List<String> = optionList("resourcePacks")

    fun setEnabled(name: String, enabled: Boolean) {
        val pack = resolve(name) ?: throw LauncherException("That resource pack is no longer installed.")
        val entry = "file/${pack.fileName}"
        val format = runCatching { LegacyTranslator.readFormat(read(pack, "pack.mcmeta")!!) }.getOrDefault(15)
        editOptions { lists ->
            val packs = lists.getOrPut("resourcePacks") { mutableListOf("vanilla") }
            val incompatible = lists.getOrPut("incompatibleResourcePacks") { mutableListOf() }
            packs.remove(entry); incompatible.remove(entry)
            if (enabled) {
                if ("vanilla" !in packs) packs.add(0, "vanilla")
                packs += entry
                if (format != LegacyTranslator.TARGET_FORMAT) incompatible += entry
            }
        }
    }

    fun import(sources: List<Path>): Int {
        Files.createDirectories(dir)
        var count = 0
        for (source in sources) {
            val name = source.fileName.toString()
            val target = dir.resolve(name)
            when {
                Files.isDirectory(source) && Files.isRegularFile(source.resolve("pack.mcmeta")) -> { SafeZip.copyTree(source, target); count++ }
                name.endsWith(".zip") && runCatching { read(source, "pack.mcmeta") != null }.getOrDefault(false) -> {
                    Files.copy(source, target, StandardCopyOption.REPLACE_EXISTING); count++
                }
                else -> throw LauncherException("$name is not a resource pack.")
            }
        }
        return count
    }

    fun remove(name: String) {
        val pack = resolve(name) ?: return
        setEnabled(name, false)
        SafeZip.deleteTree(pack)
    }

    fun resolve(name: String): Path? {
        if (name.isBlank() || name.contains('/') || name.contains('\\') || name.startsWith(".")) return null
        return dir.resolve(name).takeIf(Files::exists)
    }

    private fun optionList(key: String): List<String> {
        if (!Files.isRegularFile(options)) return emptyList()
        val line = Files.readAllLines(options).firstOrNull { it.startsWith("$key:") } ?: return emptyList()
        return parse(line.substringAfter(':'))
    }

    private fun parse(raw: String): MutableList<String> =
        Regex("\"((?:[^\"\\\\]|\\\\.)*)\"").findAll(raw).map { it.groupValues[1].replace("\\\"", "\"") }.toMutableList()

    private fun editOptions(edit: (MutableMap<String, MutableList<String>>) -> Unit) {
        val lines = if (Files.isRegularFile(options)) Files.readAllLines(options).toMutableList() else mutableListOf()
        val keys = listOf("resourcePacks", "incompatibleResourcePacks")
        val lists = keys.associateWith { k -> lines.firstOrNull { it.startsWith("$k:") }?.let { parse(it.substringAfter(':')) } ?: mutableListOf() }.toMutableMap()
        edit(lists)
        for (k in keys) {
            val line = "$k:" + lists.getValue(k).joinToString(",", "[", "]") { "\"" + it.replace("\"", "\\\"") + "\"" }
            val idx = lines.indexOfFirst { it.startsWith("$k:") }
            if (idx >= 0) lines[idx] = line else lines += line
        }
        Files.createDirectories(options.parent)
        Files.write(options, lines)
    }
}

class ShaderLibrary(private val gameDir: Path) {
    private val dir: Path get() = gameDir.resolve("shaderpacks")

    fun list(): List<Map<String, Any>> {
        if (!Files.isDirectory(dir)) return emptyList()
        return Files.list(dir).use { s -> s.toList() }
            .filter { Files.isDirectory(it) || it.fileName.toString().endsWith(".zip") }
            .map { mapOf("name" to it.fileName.toString(), "sizeBytes" to if (Files.isDirectory(it)) 0L else Files.size(it)) }
            .sortedBy { (it["name"] as String).lowercase() }
    }

    fun import(sources: List<Path>): Int {
        Files.createDirectories(dir)
        var count = 0
        for (source in sources) {
            val name = source.fileName.toString()
            if (Files.isDirectory(source)) SafeZip.copyTree(source, dir.resolve(name))
            else if (name.endsWith(".zip")) Files.copy(source, dir.resolve(name), StandardCopyOption.REPLACE_EXISTING)
            else throw LauncherException("$name is not a shader pack (.zip).")
            count++
        }
        return count
    }

    fun remove(name: String) {
        if (name.isBlank() || name.contains('/') || name.contains('\\') || name.startsWith(".")) return
        SafeZip.deleteTree(dir.resolve(name))
    }
}
