package dev.wizardlauncher.core.runtime

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import dev.wizardlauncher.core.AppPaths
import dev.wizardlauncher.core.Json
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.Platform
import dev.wizardlauncher.core.Settings
import dev.wizardlauncher.core.install.Progress
import dev.wizardlauncher.core.install.formatBytes
import dev.wizardlauncher.core.net.SecureDownloader
import dev.wizardlauncher.core.security.Checksum
import dev.wizardlauncher.core.security.Hashes
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.nio.file.attribute.PosixFilePermission
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicLong
import org.tukaani.xz.LZMAInputStream

data class JavaPick(val path: Path, val major: Int)

class JavaRuntimes(
    private val paths: AppPaths,
    private val settings: Settings,
    private val downloader: SecureDownloader,
    private val progress: Progress,
) {
    fun locate(requiredMajor: Int, component: String?, online: Boolean): JavaPick {
        settings.javaPath.takeIf { it.isNotBlank() }?.let { configured ->
            val path = Path.of(configured)
            val major = JavaLocator.probeMajor(path)
            if (major != null && major >= requiredMajor) return JavaPick(path, major)
            Log.info("The Java set in Settings ($configured) is not Java $requiredMajor+; picking another.")
        }
        val own = Path.of(System.getProperty("java.home"), "bin", EXE)
        if (Files.isExecutable(own) && Runtime.version().feature() >= requiredMajor) return JavaPick(own, Runtime.version().feature())
        val name = component ?: componentFor(requiredMajor)
        installed(name)?.let { return JavaPick(it, majorOf(name, requiredMajor)) }
        if (!online) throw LauncherException(
            "This installation needs Java $requiredMajor, which has not been downloaded yet.\n\n" +
                "Turn offline mode off and connect once, or set a Java $requiredMajor+ path in Settings.")
        return JavaPick(install(name), majorOf(name, requiredMajor))
    }

    fun hasJava(requiredMajor: Int, component: String?): Boolean =
        Runtime.version().feature() >= requiredMajor || installed(component ?: componentFor(requiredMajor)) != null ||
            settings.javaPath.isNotBlank()

    fun installed(component: String): Path? {
        val dir = paths.runtimes.resolve(component)
        val marker = Json.read(dir.resolve(MARKER))?.takeIf { it.isJsonObject }?.asJsonObject ?: return null
        if (marker.get("platform")?.asString != platformKey) return null
        return javaIn(dir).takeIf(Files::isRegularFile)
    }

    private fun install(component: String): Path {
        val key = platformKey ?: throw LauncherException("Mojang does not publish a Java runtime for this system (${Platform.current} ${Platform.arch}).\n\n" +
            "Install Java yourself and set its path in Settings.")
        progress.update(null, "Looking up Java for this installation...")
        val all = JsonParser.parseString(downloader.fetchText(ALL_RUNTIMES)).asJsonObject
        val entry = all.getAsJsonObject(key)?.getAsJsonArray(component)?.firstOrNull()?.asJsonObject
            ?: throw LauncherException("Mojang has no '$component' Java runtime for $key.")
        val manifestRef = entry.getAsJsonObject("manifest")
        val version = entry.getAsJsonObject("version")?.get("name")?.asString ?: component
        val manifest = JsonParser.parseString(downloader.fetchText(manifestRef.get("url").asString,
            Checksum.sha1(manifestRef.get("sha1").asString))).asJsonObject
        val dir = paths.runtimes.resolve(component)
        Files.deleteIfExists(dir.resolve(MARKER))
        Files.createDirectories(dir)
        Log.info("Installing Java $version (first run only)...")
        val entries = manifest.getAsJsonObject("files").entrySet().map { it.key to it.value.asJsonObject }
        for ((rel, info) in entries) {
            if (info.get("type").asString == "directory") Files.createDirectories(safe(dir, rel))
        }
        val files = entries.filter { it.second.get("type").asString == "file" }
        val total = files.sumOf { it.second.getAsJsonObject("downloads").getAsJsonObject("raw").get("size").asLong }.coerceAtLeast(1)
        val done = AtomicLong()
        val pool = Executors.newFixedThreadPool(12)
        try {
            files.map { (rel, info) ->
                pool.submit {
                    fetchFile(safe(dir, rel), info)
                    val b = done.addAndGet(info.getAsJsonObject("downloads").getAsJsonObject("raw").get("size").asLong)
                    progress.update(b.toDouble() / total, "Java $version  ·  ${formatBytes(b)} of ${formatBytes(total)}")
                }
            }.forEach {
                try { it.get() } catch (e: java.util.concurrent.ExecutionException) { throw e.cause ?: e }
            }
        } finally {
            pool.shutdownNow()
        }
        if (Platform.current != Platform.WINDOWS) {
            for ((rel, info) in entries.filter { it.second.get("type").asString == "link" }) {
                val link = safe(dir, rel)
                Files.deleteIfExists(link)
                Files.createDirectories(link.parent)
                runCatching { Files.createSymbolicLink(link, Path.of(info.get("target").asString)) }
                    .onFailure { Log.file("Could not create link $rel: ${it.message}") }
            }
        }
        val java = javaIn(dir)
        if (!Files.isRegularFile(java)) throw LauncherException("The Java runtime was downloaded but has no java executable at $java.")
        Json.write(dir.resolve(MARKER), JsonObject().apply {
            addProperty("component", component)
            addProperty("platform", key)
            addProperty("version", version)
            addProperty("manifest", manifestRef.get("sha1").asString)
        })
        Log.info("Java $version is ready.")
        return java
    }

    private fun fetchFile(target: Path, info: JsonObject) {
        val downloads = info.getAsJsonObject("downloads")
        val raw = downloads.getAsJsonObject("raw")
        val rawSha1 = raw.get("sha1").asString
        if (!(Files.isRegularFile(target) && Files.size(target) == raw.get("size").asLong && Checksum("sha1", rawSha1).verify(target))) {
            val lzma = downloads.getAsJsonObject("lzma")
            if (lzma != null) {
                val packed = target.resolveSibling(target.fileName.toString() + ".lzma")
                downloader.download(lzma.get("url").asString, packed, Checksum.sha1(lzma.get("sha1").asString), resume = false)
                val tmp = target.resolveSibling(target.fileName.toString() + ".tmp")
                LZMAInputStream(Files.newInputStream(packed).buffered()).use { input -> Files.copy(input, tmp, StandardCopyOption.REPLACE_EXISTING) }
                Files.deleteIfExists(packed)
                if (!Hashes.matches(Hashes.of(tmp, "SHA-1"), rawSha1)) {
                    Files.deleteIfExists(tmp)
                    throw LauncherException("A Java runtime file did not match its fingerprint: ${target.fileName}. Press Play again to retry.")
                }
                Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            } else {
                downloader.download(raw.get("url").asString, target, Checksum.sha1(rawSha1), resume = false)
            }
        }
        if (info.get("executable")?.asBoolean == true && Platform.current != Platform.WINDOWS) {
            runCatching {
                val perms = Files.getPosixFilePermissions(target).toMutableSet()
                perms += listOf(PosixFilePermission.OWNER_EXECUTE, PosixFilePermission.GROUP_EXECUTE, PosixFilePermission.OTHERS_EXECUTE)
                Files.setPosixFilePermissions(target, perms)
            }
        }
    }

    private fun safe(dir: Path, rel: String): Path {
        val p = dir.resolve(rel).normalize()
        if (!p.startsWith(dir)) throw LauncherException("The Java runtime manifest lists an unsafe path ($rel).")
        return p
    }

    companion object {
        const val ALL_RUNTIMES = "https://piston-meta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json"
        private const val MARKER = ".wizard-runtime.json"
        private val EXE = if (Platform.current == Platform.WINDOWS) "java.exe" else "java"

        fun majorOf(component: String, fallback: Int) = when (component) {
            "java-runtime-delta" -> 21
            "java-runtime-gamma", "java-runtime-beta" -> 17
            "java-runtime-alpha" -> 16
            "jre-legacy" -> 8
            else -> fallback
        }

        fun componentFor(major: Int) = when {
            major >= 21 -> "java-runtime-delta"
            major >= 17 -> "java-runtime-gamma"
            major >= 16 -> "java-runtime-alpha"
            else -> "jre-legacy"
        }

        val platformKey: String? by lazy {
            when (Platform.current) {
                Platform.WINDOWS -> when (Platform.arch) { "x86_64" -> "windows-x64"; "aarch64" -> "windows-arm64"; "x86" -> "windows-x86"; else -> null }
                Platform.MACOS -> when (Platform.arch) { "aarch64" -> "mac-os-arm64"; "x86_64" -> "mac-os"; else -> null }
                Platform.LINUX -> when (Platform.arch) { "x86_64" -> "linux"; "x86" -> "linux-i386"; else -> null }
            }
        }

        fun javaIn(dir: Path): Path =
            if (Platform.current == Platform.MACOS) dir.resolve("jre.bundle/Contents/Home/bin/java") else dir.resolve("bin").resolve(EXE)
    }
}
