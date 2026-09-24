package dev.wizardlauncher.core.install

import dev.wizardlauncher.core.AppPaths
import dev.wizardlauncher.core.Catalog
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.net.SecureDownloader
import dev.wizardlauncher.core.security.Checksum
import dev.wizardlauncher.pack.PackConverter
import dev.wizardlauncher.pack.VanillaAssets
import java.nio.file.Files
import java.nio.file.Path

/**
 * The map and the resource pack: downloaded once into a cache, then
 * installed from it. The world is copied (never moved) so "reset world"
 * restores it from the cache without another gigabyte over the wire.
 */
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

    /** Download + unpack a catalog resource into the content cache. */
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
            // No digest published yet: record what we got, so the maintainer
            // can pin it and so a later re-download that differs is noticed.
            Log.file("${resource.name}: downloaded sha256=$sha256 (no pin in catalog)")
        }
        SafeZip.extract(zip, dir, progress, resource.name)
        Files.deleteIfExists(zip)
        return dir
    }

    /**
     * Installs the resource pack into the game, converted for 1.20.1 when the
     * catalog says it was authored for an older format. Returns the folder or
     * zip name to enable in options.txt.
     */
    fun ensureResourcePack(convert: Boolean): String {
        val resource = catalog.resource("resource_pack")
        val needsConversion = convert && resource.convertFrom != null && resource.convertFrom < PackConverter.TARGET_FORMAT
        val name = if (needsConversion) "${resource.name} (1.20.1).zip" else resource.name
        val target = paths.gameDir.resolve("resourcepacks").resolve(name)
        val rulesFile = paths.root.resolve("wizard-states.json")
        val wanted = InstallState.fingerprint(resource.url, resource.sha256, needsConversion, CONVERTER_REVISION,
            if (Files.isRegularFile(rulesFile)) Files.getLastModifiedTime(rulesFile).toMillis() else 0)
        if (state.matches("resourcepack", wanted) && Files.exists(target)) return name

        val source = fetch(resource)
        Files.createDirectories(target.parent)
        if (needsConversion) {
            progress.update(null, "Converting the resource pack for 1.20.1...")
            val vanillaJar = paths.versions.resolve(catalog.minecraft.clientVersion).resolve("${catalog.minecraft.clientVersion}.jar")
            val vanilla = if (Files.isRegularFile(vanillaJar)) VanillaAssets.fromClientJar(vanillaJar) else null
            val report = PackConverter(vanilla, Log::file).convert(source, target,
                listOfNotNull(rulesFile.takeIf(Files::isRegularFile)))
            Files.writeString(paths.logs.resolve("resource-pack-conversion.txt"), report.render())
            Log.info("Resource pack converted for 1.20.1 (${report.filesRewritten} files adapted, " +
                "${report.warnings.size} note(s) in logs/resource-pack-conversion.txt).")
        } else {
            SafeZip.deleteTree(target)
            SafeZip.copyTree(source, target)
        }
        state.mark("resourcepack", wanted)
        return name
    }

    companion object {
        /** Bump when the converter changes output, so installed packs are re-converted. */
        const val CONVERTER_REVISION = 1

        fun requireSpace(paths: AppPaths, mb: Int) {
            val free = runCatching { Files.getFileStore(paths.root).usableSpace / (1024 * 1024) }.getOrDefault(Long.MAX_VALUE)
            if (free < mb) throw LauncherException(
                "Not enough disk space: about $mb MB is needed, ${free} MB is free on the drive holding\n${paths.root}")
        }
    }
}
