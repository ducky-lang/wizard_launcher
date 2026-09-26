package dev.wizardlauncher.core

import dev.wizardlauncher.core.auth.Account
import dev.wizardlauncher.core.auth.AccountManager
import dev.wizardlauncher.core.game.ClientRunner
import dev.wizardlauncher.core.game.GameOptions
import dev.wizardlauncher.core.game.ModConfigs
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
import dev.wizardlauncher.core.instance.Instance
import dev.wizardlauncher.core.instance.InstanceManager
import dev.wizardlauncher.core.net.SecureDownloader
import dev.wizardlauncher.core.runtime.JavaLocator
import dev.wizardlauncher.core.runtime.JavaRuntimes
import dev.wizardlauncher.core.security.SecretStore
import java.net.InetAddress
import java.net.ServerSocket
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.concurrent.ExecutionException
import java.util.concurrent.FutureTask
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.locks.ReentrantLock

class Launcher(val paths: AppPaths = AppPaths.default().ensure()) {
    val settings = Settings(paths.root.resolve("settings.json")).load()
    val state = InstallState(paths.root.resolve("install_state.json"))
    val supervisor = ProcessSupervisor(paths.root.resolve("runtime_state.json"))
    val secrets = SecretStore(paths.secrets)
    val accounts = AccountManager(paths.root.resolve("account.json"), secrets, settings)
    val instances = InstanceManager(paths, settings)
    private val playLock = ReentrantLock()
    @Volatile var server: ServerRunner? = null
        private set

    init {
        Log.init(paths.logs, settings.keepLogDays)
        Catalog.load(paths.root)
        Bootstrap.copyBundledResources(paths)
        paths.instanceId = instances.selected().id
    }

    private fun downloader(domains: Set<String>) = Catalog.current.download.let {
        SecureDownloader(domains, it.maxRetries, it.backoffMs)
    }

    inner class Setup(val instance: Instance, val progress: Progress) {
        private val catalog = Catalog.current
        val gameDir: Path = paths.gameDir(instance.id)
        val content = ContentInstaller(paths, state, downloader(catalog.download.content), progress, online = !settings.offlineOnly)
        val modpack = ModpackInstaller(paths, state, downloader(instance.modHosts), progress, instance)
        val game = MinecraftInstaller(paths, state, downloader(catalog.download.game), progress, instance.minecraft)
        val runtimes = JavaRuntimes(paths, settings, downloader(catalog.download.game), progress)

        fun profileOrNull(): VersionProfile? = runCatching { VersionProfile.load(paths, game.fabricId(modpack.loaderVersion())) }.getOrNull()

        fun javaFor(profile: VersionProfile) =
            runtimes.locate(profile.javaMajor ?: instance.requiredJava, profile.javaComponent, online = !settings.offlineOnly)
    }

    fun setup(instance: Instance = instances.selected(), progress: Progress = Progress.NONE) = Setup(instance, progress)

    fun readyOffline(instance: Instance = instances.selected()): Boolean {
        val s = setup(instance)
        if (!s.modpack.isInstalled() || !s.game.isInstalled(s.modpack.loaderVersion()) || !s.content.isWorldInstalled()) return false
        val profile = s.profileOrNull() ?: return false
        return s.runtimes.hasJava(profile.javaMajor ?: instance.requiredJava, profile.javaComponent)
    }

    fun play(account: Account, progress: Progress, stage: (Int) -> Unit = {}): Process {
        if (!playLock.tryLock()) throw LauncherException("A launch is already in progress.")
        var serverStart: FutureTask<Unit>? = null
        var serverThread: Thread? = null
        try {
            supervisor.reapOrphans()
            if (supervisor.isRunning("client")) throw LauncherException("The game is already running.")
            val catalog = Catalog.current
            val instance = instances.selected()
            val s = setup(instance, progress)
            val offline = settings.offlineOnly
            if (!readyOffline(instance)) {
                if (offline) throw LauncherException(
                    "Offline mode is on, but ${instance.name} is not fully installed yet.\n\n" +
                        "Turn offline mode off and connect once, or import an offline bundle (Tools menu).")
                ContentInstaller.requireSpace(paths, catalog.approxDownloadMb)
            }
            val serverJava = JavaLocator.find(settings, catalog.minecraft.requiredJava)

            stage(0)
            progress.update(0.02, "Preparing the castle...")
            val latest = AtomicReference<Pair<Double?, String>>(null to "Downloading the castle...")
            val quiet = Progress { fraction, message -> latest.set(fraction to message) }
            val contentJob = FutureTask {
                val background = ContentInstaller(paths, state, downloader(catalog.download.content), quiet, online = !offline)
                background.ensureWorld()
                background.fetch(catalog.resource("resource_pack"))
                Unit
            }
            Thread(contentJob, "content-download").apply { isDaemon = true; start() }

            stage(1)
            if (!s.modpack.isInstalled()) s.modpack.fetchArchive()
            val versionId = s.game.ensure(s.modpack.loaderVersion())
            val profile = VersionProfile.load(paths, versionId)
            val java = s.javaFor(profile)
            stage(2)
            s.modpack.ensure()
            ModConfigs.enforce(s.gameDir)
            val clientWaitsForWorld = s.content.ensureLegacyPackSupport(instance, s.gameDir)
            val legacyReader = Files.isRegularFile(s.gameDir.resolve("mods").resolve(ContentInstaller.LEGACY_MOD))
            while (!contentJob.isDone) {
                val (fraction, message) = latest.get()
                progress.update(fraction, message)
                Thread.sleep(250)
            }
            try {
                contentJob.get()
            } catch (e: ExecutionException) {
                throw e.cause as? Exception ?: e
            }
            val packName = s.content.ensureResourcePack(s.gameDir)

            stage(3)
            progress.update(0.75, "Opening the portal...")
            val server = ServerRunner(paths, settings, state, supervisor, serverJava, tool("wizard-server-host.jar"))
            this.server = server
            server.verifyBundledJars()
            server.configure(account.name)

            val address = "127.0.0.1:${server.ports.proxy}"
            inheritOptions(instance, s.gameDir)
            ServersDat.upsert(s.gameDir.resolve("servers.dat"), catalog.server.entryName, address)
            GameOptions.enableResourcePack(s.gameDir.resolve("options.txt"), packName,
                compatible = legacyReader, stale = s.content.staleNames().filter { it != packName })

            if (clientWaitsForWorld) {
                serverStart = FutureTask { server.start() }
                serverThread = Thread(serverStart, "server-start").apply { isDaemon = true; start() }
            } else {
                server.start()
            }

            stage(4)
            progress.update(0.9, "Launching Minecraft ${instance.minecraft}...")
            Log.info("Launching ${instance.name} as ${account.name}${if (account.isMicrosoft) "" else " (offline name)"} with Java ${java.major}...")
            val client = ClientRunner(paths, settings, supervisor, java.path, tool("wizard-client-boot.jar"), s.gameDir, java.major)
                .launch(profile, account, address, waitForWorld = clientWaitsForWorld)
            serverStart?.let { start ->
                progress.update(0.95, "Minecraft is loading while the world finishes starting...")
                try {
                    start.get()
                } catch (e: ExecutionException) {
                    supervisor.stop("client", graceful = false)
                    throw e.cause as? Exception ?: e
                }
            }
            server.watchClient(client.pid())
            server.superviseWhile({ client.isAlive }) { Log.info(it) }
            progress.update(1.0, "Enjoy the castle!")
            return client
        } catch (e: Exception) {
            serverThread?.let {
                it.interrupt()
                it.join(10_000)
            }
            server?.stop()
            throw e
        } finally {
            playLock.unlock()
        }
    }

    private fun inheritOptions(instance: Instance, gameDir: Path) {
        val options = gameDir.resolve("options.txt")
        if (Files.exists(options)) return
        val donor = instances.all().filter { it.id != instance.id }
            .map { paths.gameDir(it.id).resolve("options.txt") }
            .filter(Files::isRegularFile)
            .maxByOrNull { Files.getLastModifiedTime(it) } ?: return
        runCatching {
            Files.createDirectories(gameDir)
            Files.copy(donor, options)
            Log.info("Brought your controls and video settings over from ${donor.parent.parent.fileName}.")
        }
    }

    fun verifyInstall(progress: Progress, repair: Boolean = true, instanceId: String? = null): String {
        val catalog = Catalog.current
        val instance = instanceId?.let { instances.require(it) } ?: instances.selected()
        val s = setup(instance, progress)
        if (repair) {
            if (!s.modpack.isInstalled()) s.modpack.fetchArchive()
            s.game.ensure(s.modpack.loaderVersion(), verify = true)
            s.modpack.ensure()
            s.content.ensureLegacyPackSupport(instance, s.gameDir)
        }
        ModConfigs.enforce(s.gameDir)
        val loader = s.modpack.loaderVersion()
        val missing = s.game.missingFiles(loader)
        if (missing.isNotEmpty()) {
            throw LauncherException("${missing.size} game file(s) are missing:\n" + missing.take(10).joinToString("\n"))
        }
        val profile = VersionProfile.load(paths, s.game.fabricId(loader))
        val java = s.javaFor(profile)
        val boot = tool("wizard-client-boot.jar")
        val command = ClientRunner(paths, settings, supervisor, java.path, boot, s.gameDir, java.major)
            .command(profile, Account.offline("WizardCheck"), "127.0.0.1:25566")
        val legacy = Files.isRegularFile(s.gameDir.resolve("mods").resolve(ContentInstaller.LEGACY_MOD))
        return buildString {
            appendLine("Installation: ${instance.name} (${instance.id})")
            appendLine("Game: ${profile.id} (${profile.libraries.size} libraries, main ${profile.mainClass})")
            appendLine("Modpack: ${s.modpack.name} ${s.modpack.version}, installed=${s.modpack.isInstalled()}")
            appendLine("Legacy pack support: ${if (legacy) "installed" else "not available"}")
            appendLine("Java ${java.major}: ${java.path}")
            appendLine("Launch command: ${command.size} arguments, all ${profile.libraries.size + 2} classpath entries present")
        }
    }

    fun smokeClient(progress: Progress, timeoutSeconds: Long, pack: Path?, instanceId: String? = null): String {
        verifyInstall(progress, instanceId = instanceId)
        val instance = instanceId?.let { instances.require(it) } ?: instances.selected()
        val s = setup(instance, progress)
        val profile = VersionProfile.load(paths, s.game.fabricId(s.modpack.loaderVersion()))
        val java = s.javaFor(profile)
        var packName: String? = null
        if (pack != null) {
            packName = pack.fileName.toString()
            val target = s.gameDir.resolve("resourcepacks").resolve(packName)
            Files.createDirectories(target.parent)
            if (Files.isDirectory(pack)) SafeZip.copyTree(pack, target) else Files.copy(pack, target, StandardCopyOption.REPLACE_EXISTING)
            GameOptions.enableResourcePack(s.gameDir.resolve("options.txt"), packName, compatible = true, stale = emptyList())
        }
        val log = s.gameDir.resolve("logs").resolve("latest.log")
        Files.deleteIfExists(log)
        val closedPort = ServerSocket(0, 0, InetAddress.getLoopbackAddress()).use { it.localPort }
        progress.update(null, "Starting Minecraft ${instance.minecraft} for a smoke test...")
        val client = ClientRunner(paths, settings, supervisor, java.path, tool("wizard-client-boot.jar"), s.gameDir, java.major)
            .launch(profile, Account.offline("WizardSmoke"), "127.0.0.1:$closedPort", waitForWorld = true)
        val deadline = System.currentTimeMillis() + timeoutSeconds * 1000
        val markers = linkedMapOf(
            "Fabric loaded mods" to Regex("Loading \\d+ mods"),
            "Legacy pack support active" to Regex("Wizard Legacy Packs active"),
            "Legacy pack read natively" to Regex("Reading '.*' \\(pack_format \\d+\\) natively"),
            "Block atlas built" to Regex("Created: .*minecraft:textures/atlas/blocks\\.png-atlas"),
            "One-click join waits for the world" to Regex("Holding the one-click join"),
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
        val diagnostics = text.lines().filter {
            it.contains("WizardLegacyPacks") || it.contains("Reloading ResourceManager") || it.contains("resource pack", ignoreCase = true) ||
                (packName != null && it.contains(packName))
        }.take(25)
        val culling = runCatching { Files.readAllLines(s.gameDir.resolve("config").resolve("moreculling.toml")) }.getOrDefault(emptyList())
            .any { it.replace(" ", "") == "useBlockStateCulling=false" }
        val brokenModels = if (packName == null) emptyList() else text.lines().filter {
            BROKEN_MODEL.containsMatchIn(it) && LEGACY_MODEL_IDS.containsMatchIn(it)
        }.take(10)
        return buildString {
            appendLine("${instance.name}: client was ${if (alive) "running" else "not running (exit ${runCatching { client.exitValue() }.getOrDefault(-1)})"} at the end of the test")
            markers.keys.forEach { appendLine((if (it in seen) "[ok] " else "[--] ") + it) }
            appendLine((if (culling) "[ok] " else "[--] ") + "Culling matches vanilla for remodelled blocks")
            if (packName != null) {
                appendLine((if (brokenModels.isEmpty()) "[ok] " else "[--] ") + "Legacy door, vine and fire models load cleanly")
                brokenModels.forEach { appendLine("  $it") }
            }
            if (diagnostics.isNotEmpty()) {
                appendLine("Resource pack lines in latest.log:")
                diagnostics.forEach { appendLine("  $it") }
            }
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
                appendLine("Bridge answers a ${Catalog.current.minecraft.clientVersion} client as: ${version.get("name").asString}, protocol ${version.get("protocol").asInt}")
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

    private companion object {
        val BROKEN_MODEL = Regex("Unable to load model|Exception loading blockstate definition|Unable to resolve texture reference|Missing textures in model")
        val LEGACY_MODEL_IDS = Regex("door|vine|fire_floor|soul_fire")
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
