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

class MinecraftInstaller(
    private val paths: AppPaths,
    private val state: InstallState,
    private val downloader: SecureDownloader,
    private val progress: Progress,
    private val mc: String = Catalog.current.minecraft.clientVersion,
) {
    private val stateKey = "minecraft@$mc"

    private fun recorded(loader: String): Boolean {
        val fingerprint = InstallState.fingerprint(mc, loader)
        return state.matches(stateKey, fingerprint) || (mc == LEGACY_VERSION && state.matches("minecraft", fingerprint))
    }

    fun fabricId(loader: String) = "fabric-loader-$loader-$mc"

    fun isInstalled(loader: String): Boolean = recorded(loader) && missingFiles(loader).isEmpty()

    fun missingFiles(loader: String): List<Path> {
        val id = fabricId(loader)
        val fabricJson = paths.versions.resolve(id).resolve("$id.json")
        val vanillaJson = paths.versions.resolve(mc).resolve("$mc.json")
        if (!Files.isRegularFile(fabricJson) || !Files.isRegularFile(vanillaJson)) return listOf(fabricJson, vanillaJson).filterNot(Files::isRegularFile)
        val profile = runCatching { VersionProfile.load(paths, id) }.getOrElse { return listOf(fabricJson) }
        val missing = ArrayList<Path>()
        if (!Files.isRegularFile(profile.clientJar)) missing.add(profile.clientJar)
        profile.libraries.map { paths.libraries.resolve(it.path) }.filterNotTo(missing, Files::isRegularFile)
        val index = paths.assets.resolve("indexes").resolve(profile.assetIndex + ".json")
        if (!Files.isRegularFile(index)) {
            missing.add(index)
        } else {
            val objects = Json.read(index)?.asJsonObject?.getAsJsonObject("objects")
            objects?.entrySet()?.forEach { (_, v) ->
                val hash = v.asJsonObject.get("hash").asString
                val file = paths.assets.resolve("objects").resolve(hash.substring(0, 2)).resolve(hash)
                if (!Files.isRegularFile(file)) missing.add(file)
            }
        }
        if (profile.libraries.any { it.isNatives } && !Files.isDirectory(profile.nativesDir)) missing.add(profile.nativesDir)
        return missing
    }

    fun ensure(loader: String, verify: Boolean = false): String {
        val id = fabricId(loader)
        val recorded = recorded(loader)
        val missing = if (recorded) missingFiles(loader) else emptyList()
        if (recorded && missing.isEmpty() && !verify) return id
        if (recorded && missing.isNotEmpty()) {
            Log.info("Repairing ${missing.size} missing game file(s)...")
            Log.file("Missing: " + missing.take(20).joinToString())
        } else if (!recorded) {
            Log.info("Installing Minecraft $mc with Fabric $loader (first run only)...")
        }
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
        downloadLibraries(profile.libraries, verify)
        extractNatives(profile)
        downloadAssets(vanilla, verify)

        val stillMissing = missingFiles(loader)
        if (stillMissing.isNotEmpty()) {
            throw LauncherException("The game could not be fully installed; ${stillMissing.size} file(s) are still missing, for example:\n" +
                stillMissing.take(3).joinToString("\n"))
        }
        state.mark(stateKey, InstallState.fingerprint(mc, loader))
        Log.info("Minecraft $mc with Fabric $loader is ready.")
        return id
    }

    private fun present(target: Path, size: Long?): Boolean =
        size != null && size > 0 && Files.isRegularFile(target) && Files.size(target) == size

    private fun downloadLibraries(libraries: List<Library>, verify: Boolean) {
        val done = AtomicInteger()
        val unreachable = libraries.filter { it.url == null && !Files.isRegularFile(paths.libraries.resolve(it.path)) }
        if (unreachable.isNotEmpty()) {
            throw LauncherException("Minecraft lists game libraries without a download address:\n" + unreachable.joinToString("\n") { it.name })
        }
        val todo = libraries.filter { it.url != null }
        parallel(todo) { lib ->
            val target = paths.libraries.resolve(lib.path)
            when {
                !verify && present(target, lib.size) -> Unit
                lib.sha1 == null -> if (verify || !Files.isRegularFile(target)) downloader.download(lib.url!!, target, resume = false)
                else -> downloader.download(lib.url!!, target, Checksum.sha1(lib.sha1))
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
                        val target = dir.resolve(entry.name.substringAfterLast('/'))
                        zip.getInputStream(entry).use { Files.copy(it, target, java.nio.file.StandardCopyOption.REPLACE_EXISTING) }
                    }
            }
        }
    }

    private fun downloadAssets(vanilla: JsonObject, verify: Boolean) {
        val index = vanilla.getAsJsonObject("assetIndex")
        val indexFile = paths.assets.resolve("indexes").resolve(index.get("id").asString + ".json")
        downloader.download(index.get("url").asString, indexFile, Checksum.sha1(index.get("sha1").asString))
        val objects = Json.read(indexFile)!!.asJsonObject.getAsJsonObject("objects").entrySet().map { it.value.asJsonObject }
            .distinctBy { it.get("hash").asString }
        val total = objects.sumOf { it.get("size").asLong }.coerceAtLeast(1)
        val bytes = AtomicLong()
        val count = AtomicInteger()
        parallel(objects) { obj ->
            val hash = obj.get("hash").asString
            val size = obj.get("size").asLong
            val target = paths.assets.resolve("objects").resolve(hash.substring(0, 2)).resolve(hash)
            if (verify || !present(target, size)) {
                downloader.download("$RESOURCES/${hash.substring(0, 2)}/$hash", target, Checksum.sha1(hash), resume = false)
            }
            val b = bytes.addAndGet(size)
            if (count.incrementAndGet() % 25 == 0) {
                progress.update(b.toDouble() / total, "Game assets  ·  ${formatBytes(b)} of ${formatBytes(total)}")
            }
        }
    }

    private fun <T> parallel(items: List<T>, action: (T) -> Unit) {
        if (items.isEmpty()) return
        val pool = Executors.newFixedThreadPool(THREADS.coerceAtMost(items.size))
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
        const val LEGACY_VERSION = "1.20.1"
        val THREADS = (Runtime.getRuntime().availableProcessors() * 3).coerceIn(8, 24)
    }
}

data class Library(val name: String, val path: String, val url: String?, val sha1: String?, val size: Long? = null) {
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
    val javaMajor: Int? = null,
    val javaComponent: String? = null,
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
                javaMajor = vanilla.getAsJsonObject("javaVersion")?.get("majorVersion")?.asInt,
                javaComponent = vanilla.getAsJsonObject("javaVersion")?.get("component")?.asString,
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
                        artifact.get("sha1")?.asString, artifact.get("size")?.asLong)
                } else {
                    val path = Library.pathOf(name)
                    val base = (lib.get("url")?.asString ?: "https://libraries.minecraft.net/").trimEnd('/')
                    Library(name, path, "$base/$path", lib.get("sha1")?.asString, lib.get("size")?.asLong)
                }
            }
            return out
        }
    }
}

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

    fun nativesMatchArch(classifier: String): Boolean {
        val tag = when {
            classifier.endsWith("-arm64") -> "aarch64"
            classifier.endsWith("-x86") -> "x86"
            else -> "x86_64"
        }
        return tag == Platform.arch
    }
}
