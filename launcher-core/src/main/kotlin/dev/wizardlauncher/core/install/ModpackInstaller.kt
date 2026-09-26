package dev.wizardlauncher.core.install

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import dev.wizardlauncher.core.AppPaths
import dev.wizardlauncher.core.Catalog
import dev.wizardlauncher.core.Json
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.instance.Instance
import dev.wizardlauncher.core.net.SecureDownloader
import dev.wizardlauncher.core.security.Checksum
import java.net.URI
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger
import java.util.zip.ZipFile

class ModpackInstaller(
    private val paths: AppPaths,
    private val state: InstallState,
    private val downloader: SecureDownloader,
    private val progress: Progress,
    private val instance: Instance? = null,
) {
    private val pack = instance?.modpack ?: Catalog.current.modpack
    private val gameDir: Path = instance?.let { paths.gameDir(it.id) } ?: paths.gameDir
    private val cacheDir: Path = instance?.let { paths.instanceRoot(it.id).resolve("modpack") } ?: paths.modpackCache
    private val localArchive: Path? = instance?.modpackFile
    private val manifestFile: Path get() = gameDir.resolve(".wizard-modpack.json")

    private fun fingerprint() = InstallState.fingerprint(
        pack.id, pack.version, pack.url, pack.sha512, *pack.extraMods.map { "${it.path}|${it.sha512}" }.toTypedArray())

    val name: String get() = pack.name
    val version: String get() = pack.version

    fun isInstalled(): Boolean {
        val manifest = Json.read(manifestFile)?.takeIf { it.isJsonObject }?.asJsonObject ?: return false
        if (manifest.get("fingerprint")?.asString != fingerprint()) return false
        val files = manifest.getAsJsonArray("files") ?: return false
        return files.all { present(it.asString) }
    }

    private fun present(rel: String) =
        Files.isRegularFile(gameDir.resolve(rel)) || Files.isRegularFile(gameDir.resolve("$rel.disabled"))

    fun managedFiles(): Set<String> =
        Json.read(manifestFile)?.takeIf { it.isJsonObject }?.asJsonObject?.getAsJsonArray("files")?.map { it.asString }?.toSet() ?: emptySet()

    fun loaderVersion(): String {
        Json.read(manifestFile)?.asJsonObject?.get("fabric_loader")?.asString?.let { return it }
        return readIndex()?.getAsJsonObject("dependencies")?.get("fabric-loader")?.asString
            ?: instance?.loaderFallback ?: Catalog.current.minecraft.fabricLoaderFallback
    }

    private fun archive(): Path = localArchive ?: cacheDir.resolve("${pack.id}-${pack.version}.mrpack")

    private fun readIndex(): JsonObject? {
        val file = archive().takeIf(Files::isRegularFile) ?: return null
        return ZipFile(file.toFile()).use { zip ->
            val entry = zip.getEntry("modrinth.index.json") ?: return null
            if (entry.size > 4L shl 20) throw LauncherException("The modpack index is implausibly large.")
            JsonParser.parseString(zip.getInputStream(entry).readBytes().toString(Charsets.UTF_8)).asJsonObject
        }
    }

    fun fetchArchive() {
        if (localArchive != null) {
            if (!Files.isRegularFile(localArchive)) throw LauncherException("The imported modpack file is missing:\n$localArchive\n\nImport the modpack again.")
            return
        }
        if (pack.url.isBlank()) throw LauncherException("No modpack is configured in this build.")
        progress.update(null, "Fetching ${pack.name} ${pack.version}...")
        downloader.download(pack.url, archive(), Checksum.sha512(pack.sha512), maxBytes = 64L shl 20)
    }

    fun ensure() {
        if (isInstalled()) return
        fetchArchive()
        Log.info("Installing ${pack.name} ${pack.version} (first run only)...")
        val previous = Json.read(manifestFile)?.takeIf { it.isJsonObject }?.asJsonObject
        val index = readIndex() ?: throw LauncherException("The modpack archive is missing its index.")
        val installed = ArrayList<String>()

        val files = index.getAsJsonArray("files").map { it.asJsonObject }.filter { f ->
            f.getAsJsonObject("env")?.get("client")?.asString != "unsupported"
        }
        val jobs = files.mapNotNull { f ->
            val rel = safeRelative(f.get("path").asString) ?: run {
                Log.file("Skipping modpack entry outside the game folders: ${f.get("path")}"); return@mapNotNull null
            }
            val url = f.getAsJsonArray("downloads").map { it.asString }.firstOrNull { downloader.isAllowed(URI.create(it)) }
                ?: run { Log.file("No allowed mirror for $rel"); return@mapNotNull null }
            val sha512 = f.getAsJsonObject("hashes")?.get("sha512")?.asString
                ?: throw LauncherException("Modpack entry $rel has no SHA-512 and was refused.")
            Triple(rel, url, sha512)
        }
        val done = AtomicInteger()
        val pool = Executors.newFixedThreadPool(THREADS)
        try {
            jobs.map { (rel, url, sha512) ->
                pool.submit {
                    if (!Files.isRegularFile(gameDir.resolve("$rel.disabled"))) {
                        downloader.download(url, gameDir.resolve(rel), Checksum.sha512(sha512), maxBytes = 256L shl 20, resume = false)
                    }
                    val n = done.incrementAndGet()
                    progress.update(n.toDouble() / jobs.size, "${pack.name}  ·  $n of ${jobs.size}  ·  ${rel.substringAfterLast('/')}")
                }
            }.forEach {
                try { it.get() } catch (e: ExecutionException) { throw e.cause ?: e }
            }
        } finally {
            pool.shutdownNow()
        }
        installed += jobs.map { it.first }

        ZipFile(archive().toFile()).use { zip ->

            for (entry in zip.entries().asSequence().filter { !it.isDirectory }) {
                val prefix = listOf("overrides/", "client-overrides/").firstOrNull { entry.name.startsWith(it) } ?: continue
                val rel = safeRelative(entry.name.removePrefix(prefix)) ?: continue
                val target = gameDir.resolve(rel)
                Files.createDirectories(target.parent)
                zip.getInputStream(entry).use { Files.copy(it, target, java.nio.file.StandardCopyOption.REPLACE_EXISTING) }
                installed += rel
            }
        }

        for (extra in pack.extraMods) {
            val rel = safeRelative(extra.path) ?: continue
            progress.update(null, "Installing ${extra.name}...")
            downloader.download(extra.url, gameDir.resolve(rel), Checksum.sha512(extra.sha512), resume = false)
            installed += rel
        }

        sweep(installed.toSet(), previous)
        Json.write(manifestFile, JsonObject().apply {
            addProperty("id", pack.id)
            addProperty("version", pack.version)
            addProperty("fingerprint", fingerprint())
            addProperty("fabric_loader", index.getAsJsonObject("dependencies")?.get("fabric-loader")?.asString
                ?: instance?.loaderFallback ?: Catalog.current.minecraft.fabricLoaderFallback)
            add("files", Json.gson.toJsonTree(installed.distinct().sorted()))
        })
        Log.info("${pack.name} ${pack.version} ready - ${installed.size} files.")
    }

    private fun sweep(keep: Set<String>, previous: JsonObject?) {
        val owned = previous?.getAsJsonArray("files")?.map { it.asString }?.toSet() ?: return
        for (rel in owned - keep) {
            if (rel.startsWith("mods/")) Files.deleteIfExists(gameDir.resolve(rel))
        }
    }

    companion object {
        private val ALLOWED_ROOTS = setOf("mods", "config", "resourcepacks", "shaderpacks")
        private val THREADS = (Runtime.getRuntime().availableProcessors() * 2).coerceIn(4, 12)

        fun unsafePath(raw: String): Boolean {
            val p = raw.trim().replace('\\', '/')
            return p.isEmpty() || p.startsWith("/") || ':' in p || p.split('/').any { it == ".." }
        }

        fun safeRelative(raw: String): String? {
            val p = raw.trim().replace('\\', '/')
            if (p.isEmpty() || p.startsWith("/") || ':' in p) return null
            val parts = p.split('/')
            if (parts.any { it == ".." || it == "." || it.isEmpty() }) return null
            return p.takeIf { parts.first() in ALLOWED_ROOTS }
        }
    }
}
