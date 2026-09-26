package dev.wizardlauncher.core.instance

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import dev.wizardlauncher.core.AppPaths
import dev.wizardlauncher.core.Catalog
import dev.wizardlauncher.core.Json
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.Settings
import dev.wizardlauncher.core.install.ModpackInstaller
import dev.wizardlauncher.core.install.SafeZip
import dev.wizardlauncher.core.security.Hashes
import java.net.URI
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.util.zip.ZipFile

data class Instance(
    val id: String,
    val name: String,
    val description: String,
    val minecraft: String,
    val loaderFallback: String,
    val requiredJava: Int,
    val modpack: Catalog.Modpack,
    val modpackFile: Path?,
    val builtin: Boolean,
    val modHosts: Set<String>,
) {
    val packFormat: Int? get() = PACK_FORMATS[minecraft]
    val legacyMod: String get() = "wizard-legacy-packs-$minecraft.jar"

    companion object {
        val PACK_FORMATS = mapOf("1.20.1" to 15, "1.21.1" to 34)

        fun javaFor(minecraft: String): Int {
            val parts = minecraft.split('.').mapNotNull { it.toIntOrNull() }
            val minor = parts.getOrElse(1) { 0 }
            val patch = parts.getOrElse(2) { 0 }
            return when {
                minor > 20 || (minor == 20 && patch >= 5) -> 21
                minor >= 18 -> 17
                minor == 17 -> 16
                else -> 8
            }
        }
    }
}

class InstanceManager(private val paths: AppPaths, private val settings: Settings) {
    fun builtins(): List<Instance> = Catalog.current.instances.map { spec ->
        Instance(spec.id, spec.name, spec.description, spec.minecraft, spec.loaderFallback, spec.requiredJava,
            spec.modpack, null, true, Catalog.current.download.mods)
    }

    fun custom(): List<Instance> {
        if (!Files.isDirectory(paths.clientBase)) return emptyList()
        return Files.list(paths.clientBase).use { dirs ->
            dirs.filter { it.fileName.toString().startsWith(CUSTOM_PREFIX) }
                .map { it.resolve(DESCRIPTOR) }
                .filter(Files::isRegularFile)
                .toList()
        }.mapNotNull { file -> runCatching { read(file) }.onFailure { Log.file("Ignoring $file: ${it.message}") }.getOrNull() }
            .sortedBy { it.name.lowercase() }
    }

    fun all(): List<Instance> = builtins() + custom()

    fun get(id: String): Instance? = all().firstOrNull { it.id == id }

    fun require(id: String): Instance = get(id) ?: throw LauncherException("There is no installation called '$id'.")

    fun selected(): Instance = get(settings.selectedInstance) ?: builtins().first()

    fun select(id: String): Instance {
        val instance = require(id)
        settings.selectedInstance = instance.id
        settings.save()
        paths.instanceId = instance.id
        return instance
    }

    fun importModpack(source: Path): Instance {
        val pack = readMrpack(source)
        var id = CUSTOM_PREFIX + slug(pack.name)
        var n = 2
        while (Files.exists(paths.instanceRoot(id))) id = CUSTOM_PREFIX + slug(pack.name) + "-" + n++
        val root = paths.instanceRoot(id)
        val archive = root.resolve("modpack").resolve("$id.mrpack")
        Files.createDirectories(archive.parent)
        Files.copy(source, archive, StandardCopyOption.REPLACE_EXISTING)
        Files.createDirectories(paths.gameDir(id))
        val descriptor = JsonObject().apply {
            addProperty("id", id)
            addProperty("name", pack.name)
            addProperty("minecraft", pack.minecraft)
            addProperty("loader", "fabric")
            addProperty("loader_version", pack.loader)
            addProperty("modpack_version", pack.version)
            addProperty("modpack_file", root.relativize(archive).toString().replace('\\', '/'))
            addProperty("sha512", Hashes.of(archive, "SHA-512"))
            addProperty("summary", pack.summary)
            addProperty("mods", pack.mods)
        }
        Json.write(root.resolve(DESCRIPTOR), descriptor)
        Log.info("Imported ${pack.name} ${pack.version} (Minecraft ${pack.minecraft}, ${pack.mods} mods) as a new installation.")
        return read(root.resolve(DESCRIPTOR))
    }

    fun remove(id: String) {
        val instance = require(id)
        if (instance.builtin) throw LauncherException("The castle installations cannot be removed.")
        if (settings.selectedInstance == id) {
            settings.selectedInstance = builtins().first().id
            settings.save()
            paths.instanceId = settings.selectedInstance
        }
        SafeZip.deleteTree(paths.instanceRoot(id))
    }

    private fun read(file: Path): Instance {
        val o = Json.read(file)?.asJsonObject ?: throw LauncherException("unreadable descriptor")
        val root = file.parent
        val archive = root.resolve(o.get("modpack_file").asString).normalize()
        if (!archive.startsWith(root)) throw LauncherException("modpack file outside the installation")
        val mc = o.get("minecraft").asString
        val loader = o.get("loader_version").asString
        return Instance(
            id = o.get("id").asString,
            name = o.get("name").asString,
            description = o.get("summary")?.asString ?: "",
            minecraft = mc,
            loaderFallback = loader,
            requiredJava = Instance.javaFor(mc),
            modpack = Catalog.Modpack(o.get("id").asString, o.get("name").asString, o.get("modpack_version")?.asString ?: "",
                "", o.get("sha512")?.asString ?: "", 0, emptyList()),
            modpackFile = archive,
            builtin = false,
            modHosts = MODRINTH_HOSTS,
        )
    }

    data class MrpackInfo(val name: String, val version: String, val summary: String, val minecraft: String, val loader: String, val mods: Int)

    companion object {
        const val CUSTOM_PREFIX = "custom-"
        const val DESCRIPTOR = "instance.json"
        val MODRINTH_HOSTS = setOf("modrinth.com", "github.com", "githubusercontent.com", "gitlab.com")

        fun readMrpack(file: Path): MrpackInfo = ZipFile(file.toFile()).use { zip ->
            val entry = zip.getEntry("modrinth.index.json")
            if (entry == null) {
                if (zip.getEntry("manifest.json") != null) throw LauncherException(
                    "This looks like a CurseForge modpack. Export it from your launcher as a Modrinth pack (.mrpack) and import that instead.")
                throw LauncherException("This file is not a Modrinth modpack (.mrpack): it has no modrinth.index.json.")
            }
            if (entry.size > 8L shl 20) throw LauncherException("The modpack index is implausibly large.")
            val index = JsonParser.parseString(zip.getInputStream(entry).readBytes().toString(Charsets.UTF_8)).asJsonObject
            if (index.get("game")?.asString != "minecraft") throw LauncherException("This modpack is not for Minecraft: Java Edition.")
            val deps = index.getAsJsonObject("dependencies") ?: throw LauncherException("The modpack does not say which Minecraft it needs.")
            val mc = deps.get("minecraft")?.asString ?: throw LauncherException("The modpack does not say which Minecraft it needs.")
            val other = listOf("forge", "neoforge", "quilt-loader").firstOrNull { deps.has(it) }
            if (other != null) throw LauncherException("This modpack needs $other. Wizard Launcher runs Fabric modpacks only.")
            val loader = deps.get("fabric-loader")?.asString ?: throw LauncherException("This modpack does not use Fabric.")
            val files = index.getAsJsonArray("files") ?: com.google.gson.JsonArray()
            for (f in files.map { it.asJsonObject }) {
                val path = f.get("path").asString
                if (ModpackInstaller.unsafePath(path)) throw LauncherException("The modpack wants to write outside the game folder ($path) and was refused.")
                val sha512 = f.getAsJsonObject("hashes")?.get("sha512")?.asString
                if (sha512.isNullOrBlank()) throw LauncherException("The modpack entry $path has no SHA-512 hash and was refused.")
                val urls = f.getAsJsonArray("downloads")?.map { URI.create(it.asString) } ?: emptyList()
                if (urls.none { u -> u.scheme == "https" && MODRINTH_HOSTS.any { h -> u.host == h || u.host.endsWith(".$h") } }) {
                    throw LauncherException("The modpack downloads $path from a server Modrinth packs may not use and was refused.")
                }
            }
            MrpackInfo(
                name = index.get("name")?.asString?.takeIf { it.isNotBlank() } ?: file.fileName.toString().removeSuffix(".mrpack"),
                version = index.get("versionId")?.asString ?: "",
                summary = index.get("summary")?.asString ?: "",
                minecraft = mc,
                loader = loader,
                mods = files.count { it.asJsonObject.get("path").asString.startsWith("mods/") },
            )
        }

        fun slug(name: String): String =
            name.lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-').take(40).ifEmpty { "modpack" }
    }
}
