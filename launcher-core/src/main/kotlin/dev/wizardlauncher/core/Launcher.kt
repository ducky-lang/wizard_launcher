package dev.wizardlauncher.core

import dev.wizardlauncher.core.auth.Account
import dev.wizardlauncher.core.auth.AccountManager
import dev.wizardlauncher.core.game.ClientRunner
import dev.wizardlauncher.core.game.GameOptions
import dev.wizardlauncher.core.game.ProcessSupervisor
import dev.wizardlauncher.core.game.ServerRunner
import dev.wizardlauncher.core.game.ServersDat
import dev.wizardlauncher.core.install.ContentInstaller
import dev.wizardlauncher.core.install.InstallState
import dev.wizardlauncher.core.install.MinecraftInstaller
import dev.wizardlauncher.core.install.ModpackInstaller
import dev.wizardlauncher.core.install.Progress
import dev.wizardlauncher.core.install.SafeZip
import dev.wizardlauncher.core.install.VersionProfile
import dev.wizardlauncher.core.net.SecureDownloader
import dev.wizardlauncher.core.runtime.JavaLocator
import dev.wizardlauncher.core.security.SecretStore
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.concurrent.locks.ReentrantLock

class Launcher(val paths: AppPaths = AppPaths.default().ensure()) {
    val settings = Settings(paths.root.resolve("settings.json")).load()
    val state = InstallState(paths.root.resolve("install_state.json"))
    val supervisor = ProcessSupervisor(paths.root.resolve("runtime_state.json"))
    val secrets = SecretStore(paths.secrets)
    val accounts = AccountManager(paths.root.resolve("account.json"), secrets, settings)
    private val playLock = ReentrantLock()
    @Volatile var server: ServerRunner? = null
        private set

    init {
        Log.init(paths.logs, settings.keepLogDays)
        Catalog.load(paths.root)
        Bootstrap.copyBundledResources(paths)
    }

    private fun downloader(domains: Set<String>) = Catalog.current.download.let {
        SecureDownloader(domains, it.maxRetries, it.backoffMs)
    }

    fun readyOffline(): Boolean {
        val modpack = ModpackInstaller(paths, state, downloader(Catalog.current.download.mods), Progress.NONE)
        return modpack.isInstalled() &&
            MinecraftInstaller(paths, state, downloader(Catalog.current.download.game), Progress.NONE).isInstalled(modpack.loaderVersion()) &&
            ContentInstaller(paths, state, downloader(Catalog.current.download.content), Progress.NONE).isWorldInstalled()
    }

    fun play(account: Account, progress: Progress): Process {
        if (!playLock.tryLock()) throw LauncherException("A launch is already in progress.")
        try {
            supervisor.reapOrphans()
            if (supervisor.isRunning("client")) throw LauncherException("The game is already running.")
            val catalog = Catalog.current
            val offline = settings.offlineOnly
            if (!readyOffline()) {
                if (offline) throw LauncherException(
                    "Offline mode is on, but the game is not fully installed yet.\n\n" +
                        "Turn offline mode off and connect once, or import an offline bundle (Tools menu).")
                ContentInstaller.requireSpace(paths, catalog.approxDownloadMb)
            }

            val java = JavaLocator.find(settings, catalog.minecraft.requiredJava)
            val content = ContentInstaller(paths, state, downloader(catalog.download.content), progress)
            val modpack = ModpackInstaller(paths, state, downloader(catalog.download.mods), progress)
            val game = MinecraftInstaller(paths, state, downloader(catalog.download.game), progress)

            progress.update(0.02, "Preparing the castle...")
            content.ensureWorld()
            if (!modpack.isInstalled()) modpack.fetchArchive()
            val versionId = game.ensure(modpack.loaderVersion())
            modpack.ensure()
            content.ensureLegacyPackSupport()
            val packName = runCatching { content.ensureResourcePack() }
                .onFailure { Log.error("The resource pack could not be installed; continuing without it: ${it.message}", it) }
                .getOrNull()

            progress.update(0.75, "Opening the portal...")
            val server = ServerRunner(paths, settings, state, supervisor, java, tool("wizard-server-host.jar"))
            this.server = server
            server.verifyBundledJars()
            server.configure(account.name)
            server.start()

            val address = "127.0.0.1:${server.ports.proxy}"
            ServersDat.upsert(paths.gameDir.resolve("servers.dat"), catalog.server.entryName, address)
            packName?.let {
                GameOptions.enableResourcePack(paths.gameDir.resolve("options.txt"), it,
                    compatible = false, stale = listOf("$it (1.20.1).zip"))
            }

            progress.update(0.9, "Launching Minecraft...")
            Log.info("Launching Minecraft as ${account.name}${if (account.isMicrosoft) "" else " (offline name)"}...")
            val client = ClientRunner(paths, settings, supervisor, java, tool("wizard-client-boot.jar"))
                .launch(VersionProfile.load(paths, versionId), account, address)
            server.watchClient(client.pid())
            server.superviseWhile({ client.isAlive }) { Log.info(it) }
            progress.update(1.0, "Enjoy the castle!")
            return client
        } catch (e: Exception) {
            server?.stop()
            throw e
        } finally {
            playLock.unlock()
        }
    }

    fun verifyInstall(progress: Progress, repair: Boolean = true): String {
        val catalog = Catalog.current
        val modpack = ModpackInstaller(paths, state, downloader(catalog.download.mods), progress)
        val game = MinecraftInstaller(paths, state, downloader(catalog.download.game), progress)
        val content = ContentInstaller(paths, state, downloader(catalog.download.content), progress)
        if (repair) {
            if (!modpack.isInstalled()) modpack.fetchArchive()
            game.ensure(modpack.loaderVersion(), verify = true)
            modpack.ensure()
            content.ensureLegacyPackSupport()
        }
        val loader = modpack.loaderVersion()
        val missing = game.missingFiles(loader)
        val java = JavaLocator.find(settings, catalog.minecraft.requiredJava)
        val profile = VersionProfile.load(paths, game.fabricId(loader))
        val boot = tool("wizard-client-boot.jar")
        val command = ClientRunner(paths, settings, supervisor, java, boot)
            .command(profile, Account.offline("WizardCheck"), "127.0.0.1:25566")
        val legacy = Files.isRegularFile(paths.gameDir.resolve("mods").resolve(ContentInstaller.LEGACY_MOD))
        if (missing.isNotEmpty()) {
            throw LauncherException("${missing.size} game file(s) are missing:\n" + missing.take(10).joinToString("\n"))
        }
        return buildString {
            appendLine("Game: ${profile.id} (${profile.libraries.size} libraries, main ${profile.mainClass})")
            appendLine("Modpack: ${catalog.modpack.name} ${catalog.modpack.version}, installed=${modpack.isInstalled()}")
            appendLine("Legacy pack support: ${if (legacy) "installed" else "not bundled"}")
            appendLine("Java: $java")
            appendLine("Launch command: ${command.size} arguments, all ${profile.libraries.size + 2} classpath entries present")
        }
    }

    fun smokeClient(progress: Progress, timeoutSeconds: Long, pack: Path?): String {
        verifyInstall(progress)
        val catalog = Catalog.current
        val modpack = ModpackInstaller(paths, state, downloader(catalog.download.mods), progress)
        val game = MinecraftInstaller(paths, state, downloader(catalog.download.game), progress)
        val java = JavaLocator.find(settings, catalog.minecraft.requiredJava)
        var packName: String? = null
        if (pack != null) {
            packName = pack.fileName.toString()
            val target = paths.gameDir.resolve("resourcepacks").resolve(packName)
            Files.createDirectories(target.parent)
            if (Files.isDirectory(pack)) SafeZip.copyTree(pack, target) else Files.copy(pack, target, StandardCopyOption.REPLACE_EXISTING)
            GameOptions.enableResourcePack(paths.gameDir.resolve("options.txt"), packName, compatible = false, stale = emptyList())
        }
        val log = paths.gameDir.resolve("logs").resolve("latest.log")
        Files.deleteIfExists(log)
        progress.update(null, "Starting Minecraft for a smoke test...")
        val client = ClientRunner(paths, settings, supervisor, java, tool("wizard-client-boot.jar"))
            .launch(VersionProfile.load(paths, game.fabricId(modpack.loaderVersion())), Account.offline("WizardSmoke"), null)
        val deadline = System.currentTimeMillis() + timeoutSeconds * 1000
        val markers = linkedMapOf(
            "Fabric loaded mods" to Regex("Loading \\d+ mods"),
            "Legacy pack support active" to Regex("Wizard Legacy Packs active"),
            "Legacy pack read natively" to Regex("Reading '.*' \\(pack_format \\d+\\) natively"),
            "Block atlas built" to Regex("Created: .*minecraft:textures/atlas/blocks\\.png-atlas"),
            "Sound engine started" to Regex("Sound engine started"),
        )
        val seen = LinkedHashSet<String>()
        var text = ""
        while (System.currentTimeMillis() < deadline && client.isAlive) {
            Thread.sleep(2000)
            text = runCatching { Files.readString(log) }.getOrDefault("")
            markers.forEach { (name, re) -> if (re.containsMatchIn(text)) seen += name }
            val needed = if (packName != null) markers.keys else markers.keys - "Legacy pack read natively"
            if (seen.containsAll(needed)) break
        }
        val alive = client.isAlive
        client.toHandle().descendants().forEach { it.destroyForcibly() }
        client.destroyForcibly()
        val errors = text.lines().filter { it.contains("/ERROR]") || it.contains("Exception") }.take(15)
        return buildString {
            appendLine("Client was ${if (alive) "running" else "not running (exit ${runCatching { client.exitValue() }.getOrDefault(-1)})"} at the end of the test")
            markers.keys.forEach { appendLine((if (it in seen) "[ok] " else "[--] ") + it) }
            if (errors.isNotEmpty()) {
                appendLine("Errors in latest.log:")
                errors.forEach { appendLine("  $it") }
            }
        }
    }

    fun selfTestWorld(progress: Progress, playerName: String = "WizardTest"): String {
        if (supervisor.anyRunning()) throw LauncherException("Stop the game first.")
        val java = JavaLocator.find(settings, Catalog.current.minecraft.requiredJava)
        ContentInstaller(paths, state, downloader(Catalog.current.download.content), progress).ensureWorld()
        val server = ServerRunner(paths, settings, state, supervisor, java, tool("wizard-server-host.jar"))
        this.server = server
        progress.update(null, "Starting the world...")
        val started = System.currentTimeMillis()
        try {
            server.verifyBundledJars()
            server.configure(playerName)
            server.start()
            val status = dev.wizardlauncher.core.game.StatusPing.ping(server.ports.proxy)
            val version = status.getAsJsonObject("version")
            Thread.sleep(3000)
            val rssMb = supervisor.get("server")?.let { residentMb(it.pid) }
            return buildString {
                appendLine("World server: OK (${(System.currentTimeMillis() - started) / 1000}s to ready)")
                appendLine("Bridge answers a 1.20.1 client as: ${version.get("name").asString}, protocol ${version.get("protocol").asInt}")
                appendLine("Mode: ${Catalog.current.server.mode}, heap limit ${settings.effectiveServerRamMb} MB" +
                    (rssMb?.let { ", resident memory now $it MB" } ?: ""))
            }
        } finally {
            progress.update(null, "Saving and stopping the world...")
            server.stop()
        }
    }

    private fun residentMb(pid: Long): Long? = runCatching {
        when (Platform.current) {
            Platform.LINUX -> Files.readAllLines(Path.of("/proc/$pid/status")).first { it.startsWith("VmRSS:") }
                .filter(Char::isDigit).toLong() / 1024
            else -> {
                val cmd = if (Platform.current == Platform.WINDOWS) listOf("tasklist", "/FI", "PID eq $pid", "/FO", "CSV", "/NH")
                    else listOf("ps", "-o", "rss=", "-p", pid.toString())
                val out = ProcessBuilder(cmd).start().inputStream.readBytes().toString(Charsets.UTF_8)
                if (Platform.current == Platform.WINDOWS) out.split("\",\"").last().filter(Char::isDigit).toLong() / 1024
                else out.trim().toLong() / 1024
            }
        }
    }.getOrNull()

    fun stop(report: (String) -> Unit = {}) {
        supervisor.stop("client", graceful = false)
        server?.stop(report) ?: supervisor.stopAll(report)
    }

    fun backupWorld(): Path? {
        if (!Files.isDirectory(paths.worldDir)) return null
        val target = paths.backups.resolve("world-" + LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss")))
        SafeZip.copyTree(paths.worldDir, target)
        Files.list(paths.backups).use { s -> s.sorted(Comparator.reverseOrder()).skip(5).forEach(SafeZip::deleteTree) }
        return target
    }

    fun resetWorld(progress: Progress) {
        if (supervisor.anyRunning()) throw LauncherException("Stop the game first.")
        backupWorld()?.let { Log.info("World backed up to $it") }
        ContentInstaller(paths, state, downloader(Catalog.current.download.content), progress).ensureWorld(force = true)
    }

    fun tool(name: String): Path {
        val candidates = listOfNotNull(
            System.getProperty("wizard.tools")?.let { Path.of(it, name) },
            AppPaths.bundledResources()?.parent?.resolve("tools")?.resolve(name),
            runCatching { Path.of(Launcher::class.java.protectionDomain.codeSource.location.toURI()).parent.resolve(name) }.getOrNull(),
        )
        return candidates.firstOrNull(Files::isRegularFile)
            ?: throw LauncherException("$name is missing from this installation. Reinstall Wizard Launcher.")
    }
}

object Bootstrap {
    fun copyBundledResources(paths: AppPaths) {
        val bundled = AppPaths.bundledResources() ?: return
        runCatching {
            Files.walk(bundled).use { stream ->
                stream.filter(Files::isRegularFile).forEach { src ->
                    val dst = paths.resources.resolve(bundled.relativize(src).toString())

                    val replace = !Files.exists(dst) ||
                        (src.fileName.toString().endsWith(".jar") && Files.size(src) != Files.size(dst))
                    if (replace) {
                        Files.createDirectories(dst.parent)
                        Files.copy(src, dst, java.nio.file.StandardCopyOption.REPLACE_EXISTING)
                    }
                }
            }
        }.onFailure { Log.error("Could not copy bundled game files: ${it.message}", it) }
    }
}
