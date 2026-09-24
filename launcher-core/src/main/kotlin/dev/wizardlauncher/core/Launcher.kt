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
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.concurrent.locks.ReentrantLock

/**
 * The launcher's one entry point for the UI and the CLI.
 *
 * Play is offline-first: every install step first asks its local record
 * "is this already done?" and only goes to the network when the answer is
 * no. On a machine that has played once, a launch performs no network I/O.
 */
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

    /** True when nothing needs the network before the game can start. */
    fun readyOffline(): Boolean {
        val modpack = ModpackInstaller(paths, state, downloader(Catalog.current.download.mods), Progress.NONE)
        return modpack.isInstalled() &&
            MinecraftInstaller(paths, state, downloader(Catalog.current.download.game), Progress.NONE).isInstalled(modpack.loaderVersion()) &&
            ContentInstaller(paths, state, downloader(Catalog.current.download.content), Progress.NONE).isWorldInstalled()
    }

    /**
     * Install whatever is missing, start the world, start the game.
     * Returns the client process; the world stops by itself when it exits.
     */
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
            val packName = runCatching { content.ensureResourcePack(settings.convertResourcePack) }
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
                    compatible = settings.convertResourcePack, stale = listOf(catalog.resource("resource_pack").name))
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

    /**
     * Starts only the world and the bridge, pings it the way a 1.20.1 client
     * would, reports memory use and stops it again (saving). Needs no
     * internet and no game install: a quick way to check the world server
     * on a new machine.
     */
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

    /** Copies the world aside before anything destructive. */
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

    /** The helper jars shipped with the launcher (server host, client boot). */
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

/** First-run copy of the jars shipped with the installer into the data folder. */
object Bootstrap {
    fun copyBundledResources(paths: AppPaths) {
        val bundled = AppPaths.bundledResources() ?: return
        runCatching {
            Files.walk(bundled).use { stream ->
                stream.filter(Files::isRegularFile).forEach { src ->
                    val dst = paths.resources.resolve(bundled.relativize(src).toString())
                    // Only missing files, and jars that differ from the shipped
                    // ones (an update ships a new server/ViaProxy). A world or a
                    // server.properties already in the data folder is never replaced.
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
