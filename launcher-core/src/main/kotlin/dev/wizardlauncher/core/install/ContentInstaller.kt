package dev.wizardlauncher.core.install

import dev.wizardlauncher.core.AppPaths
import dev.wizardlauncher.core.Catalog
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.net.SecureDownloader
import dev.wizardlauncher.core.security.Checksum
import dev.wizardlauncher.core.security.Hashes
import java.nio.file.Files
import java.nio.file.Path

class ContentInstaller(
    private val paths: AppPaths,
    private val state: InstallState,
    private val downloader: SecureDownloader,
    private val progress: Progress,
) {
    private val catalog = Catalog.current

    fun isWorldInstalled() = Files.isRegularFile(paths.worldDir.resolve("level.dat"))

    fun ensureWorld(force: Boolean = false) {
        if (!force && isWorldInstalled()) return
        val map = catalog.resource("map")
        val cached = fetch(map)
        progress.update(null, "Installing the castle...")
        val staging = paths.worldDir.resolveSibling("world_installing")
        SafeZip.deleteTree(staging)
        SafeZip.copyTree(cached, staging)
        SafeZip.deleteTree(paths.worldDir)
        Files.move(staging, paths.worldDir)
        Log.info("Castle installed.")
    }

    private fun fetch(resource: Catalog.Resource): Path {
        val dir = paths.contentCache.resolve(resource.name)
        if (Files.isDirectory(dir) && Files.list(dir).use { it.findAny().isPresent }) return dir
        val zip = paths.contentCache.resolve("${resource.name}.zip")
        Log.info("Downloading ${resource.name} (first run only)...")
        val sha256 = downloader.download(resource.url, zip, Checksum.sha256(resource.sha256), maxBytes = 4L shl 30) { done, total ->
            progress.update(if (total > 0) done.toDouble() / total else null,
                "${resource.name}  ·  ${formatBytes(done)}${if (total > 0) " of ${formatBytes(total)}" else ""}")
        }
        if (resource.sha256.isBlank()) {
            Log.file("${resource.name}: downloaded sha256=$sha256 (no pin in catalog)")
        }
        SafeZip.extract(zip, dir, progress, resource.name)
        Files.deleteIfExists(zip)
        return dir
    }

    fun ensureResourcePack(): String {
        val resource = catalog.resource("resource_pack")
        val name = resource.name
        val target = paths.gameDir.resolve("resourcepacks").resolve(name)
        val wanted = InstallState.fingerprint(resource.url, resource.sha256, "native")
        Files.deleteIfExists(paths.gameDir.resolve("resourcepacks").resolve("$name (1.20.1).zip"))
        if (state.matches("resourcepack", wanted) && Files.isRegularFile(target.resolve("pack.mcmeta"))) return name
        val source = fetch(resource)
        progress.update(null, "Installing ${resource.name}...")
        Files.createDirectories(target.parent)
        val staging = target.resolveSibling("$name.installing")
        SafeZip.deleteTree(staging)
        SafeZip.copyTree(source, staging)
        SafeZip.deleteTree(target)
        Files.move(staging, target)
        state.mark("resourcepack", wanted)
        return name
    }

    fun ensureLegacyPackSupport() {
        val bundled = paths.resources.resolve("mods").resolve(LEGACY_MOD)
        val target = paths.gameDir.resolve("mods").resolve(LEGACY_MOD)
        if (!Files.isRegularFile(bundled)) {
            Log.info("Legacy pack support is not part of this build; older resource packs may not display correctly.")
            return
        }
        if (!Files.isRegularFile(target) || Files.size(target) != Files.size(bundled) ||
            Hashes.of(target, "SHA-256") != Hashes.of(bundled, "SHA-256")) {
            Files.createDirectories(target.parent)
            Files.copy(bundled, target, java.nio.file.StandardCopyOption.REPLACE_EXISTING)
        }
        val rules = paths.root.resolve("wizard-states.json")
        val configRules = paths.gameDir.resolve("config").resolve("wizard-states.json")
        if (Files.isRegularFile(rules)) {
            Files.createDirectories(configRules.parent)
            Files.copy(rules, configRules, java.nio.file.StandardCopyOption.REPLACE_EXISTING)
        } else {
            Files.deleteIfExists(configRules)
        }
    }

    companion object {
        const val LEGACY_MOD = "wizard-legacy-packs.jar"

        fun requireSpace(paths: AppPaths, mb: Int) {
            val free = runCatching { Files.getFileStore(paths.root).usableSpace / (1024 * 1024) }.getOrDefault(Long.MAX_VALUE)
            if (free < mb) throw LauncherException(
                "Not enough disk space: about $mb MB is needed, ${free} MB is free on the drive holding\n${paths.root}")
        }
    }
}
