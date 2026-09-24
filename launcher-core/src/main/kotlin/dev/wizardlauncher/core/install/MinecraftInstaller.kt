package dev.wizardlauncher.core.install

import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import dev.wizardlauncher.core.AppPaths
import dev.wizardlauncher.core.Catalog
import dev.wizardlauncher.core.Json
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.Platform
import dev.wizardlauncher.core.net.SecureDownloader
import dev.wizardlauncher.core.security.Checksum
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.zip.ZipFile

/**
 * Installs vanilla Minecraft plus the Fabric loader straight from Mojang's
 * and Fabric's metadata - no third-party launcher library in between.
 *
 * Every file Mojang publishes a SHA-1 for (client jar, libraries, asset
 * index, every asset object) is verified before it is used, and the version
 * JSON itself is verified against the SHA-1 in Mojang's manifest, so the
 * chain of trust runs from one HTTPS request to piston-meta.mojang.com.
 *
 * Once installed, [VersionProfile.load] reads everything from disk: launching
 * never needs the network.
 */
class MinecraftInstaller(
    private val paths: AppPaths,
    private val state: InstallState,
    private val downloader: SecureDownloader,
    private val progress: Progress,
) {
    private val mc = Catalog.current.minecraft.clientVersion

    fun isInstalled(loader: String): Boolean {
        val id = fabricId(loader)
        return state.matches("minecraft", InstallState.fingerprint(mc, loader)) &&
            Files.isRegularFile(paths.versions.resolve(id).resolve("$id.json")) &&
            Files.isRegularFile(paths.versions.resolve(mc).resolve("$mc.jar"))
    }

    fun fabricId(loader: String) = "fabric-loader-$loader-$mc"

    fun ensure(loader: String): String {
        val id = fabricId(loader)
        if (isInstalled(loader)) return id
        Log.info("Installing Minecraft $mc with Fabric $loader (first run only)...")
        progress.update(null, "Reading Minecraft $mc metadata...")

        val manifest = JsonParser.parseString(downloader.fetchText(VERSION_MANIFEST)).asJsonObject
        val entry = manifest.getAsJsonArray("versions").map { it.asJsonObject }.firstOrNull { it.get("id").asString == mc }
            ?: throw LauncherException("Mojang's version list does not contain Minecraft $mc.")
        val vanillaDir = paths.versions.resolve(mc)
        val vanillaJson = vanillaDir.resolve("$mc.json")
        downloader.download(entry.get("url").asString, vanillaJson, Checksum.sha1(entry.get("sha1")?.asString))
        val vanilla = Json.read(vanillaJson)!!.asJsonObject

        val client = vanilla.getAsJsonObject("downloads").getAsJsonObject("client")
        progress.update(null, "Downloading the Minecraft $mc client...")
        downloader.download(client.get("url").asString, vanillaDir.resolve("$mc.jar"), Checksum.sha1(client.get("sha1").asString))

        val fabricDir = paths.versions.resolve(id)
        val fabricJson = fabricDir.resolve("$id.json")
        downloader.download("$FABRIC_META/v2/versions/loader/$mc/$loader/profile/json", fabricJson, resume = false)
        val fabric = Json.read(fabricJson)!!.asJsonObject

        val profile = VersionProfile.merge(paths, vanilla, fabric)
        downloadLibraries(profile.libraries)
        extractNatives(profile)
        downloadAssets(vanilla)

        state.mark("minecraft", InstallState.fingerprint(mc, loader))
        Log.info("Minecraft $mc with Fabric $loader installed.")
        return id
    }

    private fun downloadLibraries(libraries: List<Library>) {
        val todo = libraries.filter { it.url != null }
        val done = AtomicInteger()
        parallel(todo) { lib ->
            val target = paths.libraries.resolve(lib.path)
            if (lib.sha1 == null) {
                // Fabric's Maven does not always publish a SHA-1 in the profile.
                // HTTPS + the host allow-list still apply; the file is only
                // fetched if it is not already present.
                if (!Files.isRegularFile(target)) downloader.download(lib.url!!, target, resume = false)
            } else {
                downloader.download(lib.url!!, target, Checksum.sha1(lib.sha1))
            }
            val n = done.incrementAndGet()
            progress.update(n.toDouble() / todo.size, "Game libraries  ·  $n of ${todo.size}")
        }
    }

    private fun extractNatives(profile: VersionProfile) {
        val dir = profile.nativesDir
        Files.createDirectories(dir)
        for (lib in profile.libraries.filter { it.isNatives }) {
            val jar = paths.libraries.resolve(lib.path)
            ZipFile(jar.toFile()).use { zip ->
                zip.entries().asSequence()
                    .filter { !it.isDirectory && !it.name.startsWith("META-INF/") }
                    .filter { it.name.endsWith(".dll") || it.name.endsWith(".so") || it.name.endsWith(".dylib") || it.name.endsWith(".jnilib") }
                    .forEach { entry ->
                        // Natives are flat by convention; the file name alone decides.
                        val target = dir.resolve(entry.name.substringAfterLast('/'))
                        zip.getInputStream(entry).use { Files.copy(it, target, java.nio.file.StandardCopyOption.REPLACE_EXISTING) }
                    }
            }
        }
    }

    private fun downloadAssets(vanilla: JsonObject) {
        val index = vanilla.getAsJsonObject("assetIndex")
        val indexFile = paths.assets.resolve("indexes").resolve(index.get("id").asString + ".json")
        downloader.download(index.get("url").asString, indexFile, Checksum.sha1(index.get("sha1").asString))
        val objects = Json.read(indexFile)!!.asJsonObject.getAsJsonObject("objects").entrySet().map { it.value.asJsonObject }
        val total = objects.sumOf { it.get("size").asLong }
        val bytes = AtomicLong()
        val count = AtomicInteger()
        parallel(objects.distinctBy { it.get("hash").asString }) { obj ->
            val hash = obj.get("hash").asString
            val target = paths.assets.resolve("objects").resolve(hash.substring(0, 2)).resolve(hash)
            downloader.download("$RESOURCES/${hash.substring(0, 2)}/$hash", target, Checksum.sha1(hash), resume = false)
            val b = bytes.addAndGet(obj.get("size").asLong)
            if (count.incrementAndGet() % 25 == 0) {
                progress.update(b.toDouble() / total, "Game assets  ·  ${formatBytes(b)} of ${formatBytes(total)}")
            }
        }
    }

    private fun <T> parallel(items: List<T>, action: (T) -> Unit) {
        val pool = Executors.newFixedThreadPool(8)
        try {
            val futures: List<Future<*>> = items.map { pool.submit { action(it) } }
            futures.forEach {
                try { it.get() } catch (e: java.util.concurrent.ExecutionException) { throw e.cause ?: e }
            }
        } finally {
            pool.shutdownNow()
        }
    }

    companion object {
        const val VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
        const val FABRIC_META = "https://meta.fabricmc.net"
        const val RESOURCES = "https://resources.download.minecraft.net"
    }
}

data class Library(val name: String, val path: String, val url: String?, val sha1: String?) {
    val key: String get() = name.split(':').let { p -> listOf(p[0], p[1], p.getOrNull(3) ?: "").joinToString(":") }
    val isNatives get() = name.split(':').getOrNull(3)?.startsWith("natives-") == true

    companion object {
        fun pathOf(name: String): String {
            val (coords, ext) = name.split('@').let { it[0] to (it.getOrNull(1) ?: "jar") }
            val p = coords.split(':')
            require(p.size >= 3) { "bad library name $name" }
            val classifier = p.getOrNull(3)?.let { "-$it" } ?: ""
            return "${p[0].replace('.', '/')}/${p[1]}/${p[2]}/${p[1]}-${p[2]}$classifier.$ext"
        }
    }
}

/**
 * A launchable version: vanilla JSON with the Fabric profile layered on top,
 * rules already evaluated for this machine.
 */
class VersionProfile(
    val id: String,
    val mainClass: String,
    val libraries: List<Library>,
    val clientJar: Path,
    val assetIndex: String,
    val gameArgs: List<JsonElement>,
    val jvmArgs: List<JsonElement>,
    val nativesDir: Path,
    val versionType: String,
) {
    companion object {
        fun load(paths: AppPaths, fabricId: String): VersionProfile {
            val fabric = Json.read(paths.versions.resolve(fabricId).resolve("$fabricId.json"))?.asJsonObject
                ?: throw LauncherException("The game is not installed yet. Connect to the internet once and press Play.")
            val parent = fabric.get("inheritsFrom").asString
            val vanilla = Json.read(paths.versions.resolve(parent).resolve("$parent.json"))!!.asJsonObject
            return merge(paths, vanilla, fabric)
        }

        fun merge(paths: AppPaths, vanilla: JsonObject, fabric: JsonObject): VersionProfile {
            val mc = vanilla.get("id").asString
            // Fabric first: where both ship the same artifact, the loader's pick wins.
            val libs = LinkedHashMap<String, Library>()
            for (lib in parseLibraries(fabric) + parseLibraries(vanilla)) libs.putIfAbsent(lib.key, lib)
            fun args(o: JsonObject, kind: String) = o.getAsJsonObject("arguments")?.getAsJsonArray(kind)?.toList() ?: emptyList()
            return VersionProfile(
                id = fabric.get("id").asString,
                mainClass = fabric.get("mainClass")?.asString ?: vanilla.get("mainClass").asString,
                libraries = libs.values.toList(),
                clientJar = paths.versions.resolve(mc).resolve("$mc.jar"),
                assetIndex = vanilla.getAsJsonObject("assetIndex").get("id").asString,
                gameArgs = args(vanilla, "game") + args(fabric, "game"),
                jvmArgs = args(vanilla, "jvm") + args(fabric, "jvm"),
                nativesDir = paths.versions.resolve(mc).resolve("natives-${Platform.arch}"),
                versionType = vanilla.get("type")?.asString ?: "release",
            )
        }

        private fun parseLibraries(json: JsonObject): List<Library> {
            val out = ArrayList<Library>()
            for (e in json.getAsJsonArray("libraries") ?: JsonArray()) {
                val lib = e.asJsonObject
                if (!Rules.allow(lib.getAsJsonArray("rules"))) continue
                val name = lib.get("name").asString
                val classifier = name.split(':').getOrNull(3)
                if (classifier != null && classifier.startsWith("natives-") && !Rules.nativesMatchArch(classifier)) continue
                val artifact = lib.getAsJsonObject("downloads")?.getAsJsonObject("artifact")
                out += if (artifact != null) {
                    Library(name, artifact.get("path").asString, artifact.get("url")?.asString?.takeIf { it.isNotBlank() },
                        artifact.get("sha1")?.asString)
                } else {
                    val path = Library.pathOf(name)
                    val base = (lib.get("url")?.asString ?: "https://libraries.minecraft.net/").trimEnd('/')
                    Library(name, path, "$base/$path", lib.get("sha1")?.asString)
                }
            }
            return out
        }
    }
}

/** Mojang's `rules` arrays: allow/disallow by OS, architecture and feature. */
object Rules {
    fun allow(rules: JsonArray?, features: Map<String, Boolean> = emptyMap()): Boolean {
        if (rules == null || rules.size() == 0) return true
        var allowed = false
        for (r in rules) {
            val rule = r.asJsonObject
            val os = rule.getAsJsonObject("os")
            val osMatches = os == null || (
                (os.get("name") == null || os.get("name").asString == Platform.current.mojangName) &&
                    (os.get("arch") == null || (os.get("arch").asString == "x86" && Platform.arch == "x86")) &&
                    (os.get("version") == null || Regex(os.get("version").asString).containsMatchIn(System.getProperty("os.version")))
                )
            val featureMatches = rule.getAsJsonObject("features")?.entrySet()?.all { (k, v) -> (features[k] ?: false) == v.asBoolean } ?: true
            if (osMatches && featureMatches) allowed = rule.get("action").asString == "allow"
        }
        return allowed
    }

    /**
     * 1.20.1 lists LWJGL natives for every architecture of an OS side by side
     * (`natives-macos`, `natives-macos-arm64`, ...). They share file names,
     * so only the one matching this CPU may be used.
     */
    fun nativesMatchArch(classifier: String): Boolean {
        val tag = when {
            classifier.endsWith("-arm64") -> "aarch64"
            classifier.endsWith("-x86") -> "x86"
            else -> "x86_64"
        }
        return tag == Platform.arch
    }
}
