package dev.wizardlauncher.core.install

import dev.wizardlauncher.core.AppPaths
import dev.wizardlauncher.core.Catalog
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.instance.Instance
import dev.wizardlauncher.core.net.HuggingFace
import dev.wizardlauncher.core.net.SecureDownloader
import dev.wizardlauncher.core.security.Checksum
import dev.wizardlauncher.core.security.Hashes
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.util.zip.ZipFile

class ContentInstaller(
    private val paths: AppPaths,
    private val state: InstallState,
    private val downloader: SecureDownloader,
    private val progress: Progress,
    private val online: Boolean = true,
) {
    private val catalog = Catalog.current
    private val lookups = SecureDownloader(catalog.download.content, maxRetries = 1, backoffMs = 1000)

    fun isWorldInstalled() = Files.isRegularFile(paths.worldDir.resolve("level.dat"))

    fun ensureWorld(force: Boolean = false) {
        if (!force && isWorldInstalled()) return
        val map = catalog.resource("map")
        val zip = fetch(map)
        progress.update(null, "Installing the castle...")
        val staging = paths.worldDir.resolveSibling("world_installing")
        SafeZip.deleteTree(staging)
        SafeZip.extract(zip, staging, progress, "the castle")
        if (!Files.isRegularFile(staging.resolve("level.dat"))) {
            SafeZip.deleteTree(staging)
            throw LauncherException("'${map.name}.zip' does not contain a Minecraft world (level.dat is missing).\n\n" +
                "Put the world folder, or its contents, at the top of the zip and upload it again.")
        }
        SafeZip.deleteTree(paths.worldDir)
        Files.move(staging, paths.worldDir)
        Log.info("Castle installed.")
    }

    fun fetch(resource: Catalog.Resource): Path {
        val zip = paths.contentCache.resolve("${resource.name}.zip")
        val key = "content:${resource.id}"
        val cached = state.get(key)?.takeIf { Files.isRegularFile(zip) }
        val pinned = resource.sha256.takeIf { it.isNotBlank() }
        var remote: HuggingFace.Lookup = HuggingFace.Lookup.Unknown
        if (pinned == null && online) remote = HuggingFace.lookup(resource.url, lookups)
        val expected = pinned ?: (remote as? HuggingFace.Lookup.Found)?.sha256
        when {
            remote is HuggingFace.Lookup.Missing && cached != null ->
                Log.info("'${remote.file}' is no longer on the download server; using the copy already on this computer.")
            remote is HuggingFace.Lookup.Missing -> throw LauncherException(
                "'${remote.file}' is missing from the download server (Hugging Face: ${remote.repo}).\n\n" +
                    "The map's author needs to upload it again under exactly that name. Nothing on this computer was changed.")
        }
        if (cached != null && (expected == null || Hashes.matches(cached, expected))) {
            cleanupLegacyCache(resource)
            return zip
        }
        if (cached != null) Log.info("A newer ${resource.name} is available; downloading it...")
        else Log.info("Downloading ${resource.name}...")
        if (!online && cached == null) throw LauncherException(
            "${resource.name} has not been downloaded yet. Turn offline mode off and connect once, or import an offline bundle.")
        val sha256 = try {
            downloader.download(resource.url, zip, Checksum.sha256(expected), maxBytes = 8L shl 30, resume = expected != null) { done, total ->
                progress.update(if (total > 0) done.toDouble() / total else null,
                    "${resource.name}  ·  ${formatBytes(done)}${if (total > 0) " of ${formatBytes(total)}" else ""}")
            }
        } catch (e: LauncherException) {
            if (cached != null) {
                Log.info("Could not update ${resource.name} (${e.message?.lineSequence()?.firstOrNull()}); using the copy already on this computer.")
                return zip
            }
            throw e
        }
        if (expected == null) Log.file("${resource.name}: downloaded sha256=$sha256 (the server gave no fingerprint)")
        state.mark(key, sha256)
        cleanupLegacyCache(resource)
        return zip
    }

    private fun cleanupLegacyCache(resource: Catalog.Resource) {
        val old = paths.contentCache.resolve(resource.name)
        if (Files.isDirectory(old)) runCatching { SafeZip.deleteTree(old) }
    }

    fun ensureResourcePack(gameDir: Path): String {
        val resource = catalog.resource("resource_pack")
        val zip = try {
            fetch(resource)
        } catch (e: LauncherException) {
            val packs = gameDir.resolve("resourcepacks")
            val installed = listOf("${resource.name}.zip", resource.name).firstOrNull {
                Files.isRegularFile(packs.resolve(it)) || Files.isRegularFile(packs.resolve(it).resolve("pack.mcmeta"))
            } ?: throw e
            Log.info("Using the ${resource.name} already installed: ${e.message?.lineSequence()?.firstOrNull()}")
            return installed
        }
        val sha = state.get("content:${resource.id}") ?: Hashes.of(zip, "SHA-256")
        val packs = gameDir.resolve("resourcepacks")
        val loose = !hasRootPackMeta(zip)
        val name = if (loose) resource.name else "${resource.name}.zip"
        val target = packs.resolve(name)
        val key = "resourcepack:" + paths.clientBase.relativize(gameDir).toString().replace('\\', '/')
        val wanted = InstallState.fingerprint(sha, name)
        Files.createDirectories(packs)
        staleNames(resource).filter { it != name }.map(packs::resolve).forEach { old ->
            runCatching { if (Files.isDirectory(old)) SafeZip.deleteTree(old) else Files.deleteIfExists(old) }
        }
        val present = if (loose) Files.isRegularFile(target.resolve("pack.mcmeta")) else Files.isRegularFile(target)
        if (state.matches(key, wanted) && present) return name
        progress.update(null, "Installing ${resource.name}...")
        if (loose) {
            SafeZip.extract(zip, target, progress, resource.name)
        } else {
            val tmp = packs.resolve("$name.installing")
            Files.deleteIfExists(tmp)
            val linked = runCatching { Files.createLink(tmp, zip) }.isSuccess
            if (!linked) Files.copy(zip, tmp, StandardCopyOption.REPLACE_EXISTING)
            Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING)
        }
        state.mark(key, wanted)
        return name
    }

    fun staleNames(resource: Catalog.Resource = catalog.resource("resource_pack")) =
        listOf(resource.name, "${resource.name}.zip", "${resource.name} (1.20.1).zip")

    private fun hasRootPackMeta(zip: Path): Boolean =
        runCatching { ZipFile(zip.toFile()).use { it.getEntry("pack.mcmeta") != null } }.getOrDefault(false)

    fun ensureLegacyPackSupport(instance: Instance, gameDir: Path): Boolean {
        val target = gameDir.resolve("mods").resolve(LEGACY_MOD)
        val bundled = listOfNotNull(
            paths.resources.resolve("mods").resolve(instance.legacyMod),
            paths.resources.resolve("mods").resolve(LEGACY_MOD).takeIf { instance.minecraft == "1.20.1" },
        ).firstOrNull(Files::isRegularFile)
        if (bundled == null) {
            Files.deleteIfExists(target)
            Log.info("Legacy pack support is not available for Minecraft ${instance.minecraft}; older resource packs may not display correctly.")
            return false
        }
        if (!Files.isRegularFile(target) || Files.size(target) != Files.size(bundled) ||
            Hashes.of(target, "SHA-256") != Hashes.of(bundled, "SHA-256")) {
            Files.createDirectories(target.parent)
            Files.copy(bundled, target, StandardCopyOption.REPLACE_EXISTING)
        }
        val rules = paths.root.resolve("wizard-states.json")
        val configRules = gameDir.resolve("config").resolve("wizard-states.json")
        if (Files.isRegularFile(rules)) {
            Files.createDirectories(configRules.parent)
            Files.copy(rules, configRules, StandardCopyOption.REPLACE_EXISTING)
        } else {
            Files.deleteIfExists(configRules)
        }
        return runCatching { ZipFile(target.toFile()).use { it.getEntry(WORLD_GATE) != null } }.getOrDefault(false)
    }

    companion object {
        const val LEGACY_MOD = "wizard-legacy-packs.jar"
        const val WORLD_GATE = "dev/wizardlauncher/legacypacks/WorldGate.class"

        fun requireSpace(paths: AppPaths, mb: Int) {
            val free = runCatching { Files.getFileStore(paths.root).usableSpace / (1024 * 1024) }.getOrDefault(Long.MAX_VALUE)
            if (free < mb) throw LauncherException(
                "Not enough disk space: about $mb MB is needed, ${free} MB is free on the drive holding\n${paths.root}")
        }
    }
}
