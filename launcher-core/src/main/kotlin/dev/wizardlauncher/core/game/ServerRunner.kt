package dev.wizardlauncher.core.game

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import dev.wizardlauncher.core.AppPaths
import dev.wizardlauncher.core.Catalog
import dev.wizardlauncher.core.Json
import dev.wizardlauncher.core.LauncherException
import dev.wizardlauncher.core.Log
import dev.wizardlauncher.core.Settings
import dev.wizardlauncher.core.install.InstallState
import dev.wizardlauncher.core.security.Hashes
import java.io.File
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.file.Files
import java.nio.file.Path

/** Ports actually in use this session (the catalog's, unless taken). */
data class SessionPorts(val server: Int, val proxy: Int)

/**
 * Prepares and runs the 1.16.5 world server and the version bridge.
 *
 * Default is *hosted*: one JVM running dev.wizardlauncher.host.ServerHost,
 * which loads the server and ViaProxy side by side (see server-host). The
 * 1.x two-JVM layout remains as `split` in the catalog, as a fallback.
 */
class ServerRunner(
    private val paths: AppPaths,
    private val settings: Settings,
    private val state: InstallState,
    private val supervisor: ProcessSupervisor,
    private val javaExe: Path,
    private val hostJar: Path,
) {
    private val catalog = Catalog.current
    @Volatile var ports = SessionPorts(catalog.server.port, catalog.server.proxyPort)
        private set

    // ------------------------------------------------------------------ preparation
    fun verifyBundledJars() {
        for (pin in listOf(catalog.server.serverJar, catalog.server.proxyJar)) {
            val file = paths.resources.resolve(pin.path)
            if (!Files.isRegularFile(file)) throw LauncherException(
                "A game component is missing:\n$file\n\nReinstall Wizard Launcher, or check that your antivirus has not quarantined it.")
            if (pin.sha256.isBlank()) continue
            // Hashing 80 MB on every launch is wasted work; size+mtime+pin is
            // recorded after one successful check.
            val stamp = InstallState.fingerprint(pin.sha256, Files.size(file), Files.getLastModifiedTime(file).toMillis())
            if (state.matches("verified:${pin.path}", stamp)) continue
            if (!Hashes.matches(Hashes.of(file, "SHA-256"), pin.sha256)) throw LauncherException(
                "${file.fileName} does not match the version this launcher was built with and was not started.\n\n" +
                    "Reinstall Wizard Launcher to restore the original file.")
            state.mark("verified:${pin.path}", stamp)
        }
    }

    fun configure(playerName: String?) {
        val dir = paths.serverDir
        Files.createDirectories(dir)
        Files.writeString(dir.resolve("eula.txt"), "# Accepted via the Wizard Launcher installer (Minecraft EULA)\neula=true\n")
        ports = choosePorts()
        writeProperties(dir.resolve("server.properties"))
        if (playerName != null) writePlayerAccess(dir, playerName)
        dir.resolve("wizard-log4j2.xml").let { target ->
            javaClass.getResourceAsStream("/dev/wizardlauncher/core/wizard-log4j2.xml")?.use {
                Files.copy(it, target, java.nio.file.StandardCopyOption.REPLACE_EXISTING)
            }
        }
    }

    private fun choosePorts(): SessionPorts {
        fun free(port: Int) = runCatching { ServerSocket().use { it.reuseAddress = true; it.bind(InetSocketAddress("127.0.0.1", port)) }; true }.getOrDefault(false)
        fun pick(preferred: Int, avoid: Int): Int = if (free(preferred) && preferred != avoid) preferred
            else ServerSocket(0, 0, java.net.InetAddress.getLoopbackAddress()).use { it.localPort }
        val server = pick(catalog.server.port, -1)
        val proxy = pick(catalog.server.proxyPort, server)
        if (server != catalog.server.port || proxy != catalog.server.proxyPort) {
            Log.info("Default ports are busy; using $server/$proxy for this session.")
        }
        return SessionPorts(server, proxy)
    }

    /**
     * Rewritten every launch (the server rewrites it on shutdown). Gameplay
     * values from the catalog first, security values last so they always win:
     * a hand-edited catalog can tune flight but can never re-open RCON or bind
     * the world to the whole network.
     */
    private fun writeProperties(file: Path) {
        val lan = settings.allowLan
        val enforced = LinkedHashMap(catalog.server.properties)
        enforced["view-distance"] = settings.viewDistance.toString()
        enforced.putAll(mapOf(
            // The server itself only ever listens on loopback; LAN players
            // come in through the bridge, which is the one bound wider.
            "server-ip" to "127.0.0.1",
            "server-port" to ports.server.toString(),
            "online-mode" to "false",
            "enable-rcon" to "false",
            "rcon.password" to "",
            "enable-query" to "false",
            "enable-jmx-monitoring" to "false",
            "snooper-enabled" to "false",
            "prevent-proxy-connections" to "false",
            // Offline mode means a name is all it takes to join. With the
            // bridge on loopback nobody else can reach it anyway; the
            // whitelist is defence in depth for the moment someone turns LAN on.
            "white-list" to (!lan).toString(),
            "enforce-whitelist" to (!lan).toString(),
        ))
        val lines = if (Files.isRegularFile(file)) Files.readAllLines(file) else emptyList()
        val seen = HashSet<String>()
        val out = lines.map { line ->
            val key = line.substringBefore('=', "").trim()
            if (line.trimStart().startsWith("#") || key.isEmpty() || key !in enforced) line
            else { seen += key; "$key=${enforced[key]}" }
        }.toMutableList()
        enforced.filterKeys { it !in seen }.forEach { (k, v) -> out += "$k=$v" }
        Files.write(file, out)
    }

    /** Operator (singleplayer-with-cheats parity) and whitelist, keyed by the offline UUID. */
    private fun writePlayerAccess(dir: Path, name: String) {
        val uuid = OfflineUuid.of(name).toString()
        Json.write(dir.resolve("ops.json"), JsonArray().apply {
            add(JsonObject().apply {
                addProperty("uuid", uuid); addProperty("name", name)
                addProperty("level", 4); addProperty("bypassesPlayerLimit", true)
            })
        })
        Json.write(dir.resolve("whitelist.json"), JsonArray().apply {
            add(JsonObject().apply { addProperty("uuid", uuid); addProperty("name", name) })
        })
    }

    // ------------------------------------------------------------------ processes
    fun start() {
        val serverJar = paths.resources.resolve(catalog.server.serverJar.path)
        val proxyJar = paths.resources.resolve(catalog.server.proxyJar.path)
        val heap = settings.effectiveServerRamMb
        val proxyBind = "${settings.bindAddress}:${ports.proxy}"
        Files.createDirectories(paths.proxyDir)

        if (catalog.server.mode == "split") {
            startSplit(serverJar, proxyJar, heap, proxyBind)
        } else {
            Log.info("Starting the world ($heap MB, one JVM with the version bridge)...")
            val cmd = listOf(javaExe.toString()) + JvmFlags.server(heap) + listOf(
                "-javaagent:$hostJar",
                "-cp", listOf(hostJar, proxyJar).joinToString(File.pathSeparator),
                "-DskipUpdateCheck=true",
                "dev.wizardlauncher.host.ServerHost",
                "--server-jar", serverJar.toString(),
                "--server-port", ports.server.toString(),
                "--proxy-bind", proxyBind,
                "--target-version", catalog.minecraft.serverVersion,
            )
            val process = ProcessBuilder(cmd).directory(paths.serverDir.toFile()).redirectErrorStream(true)
                .apply { environment()["VP_RUN_DIR"] = paths.proxyDir.toString() }
                .start()
            supervisor.register("server", process, gracefulCommand = "stop", pipeLabel = "SERVER")
            waitForPort(ports.proxy, process, "The world")
        }
        Log.info("The world is ready.")
    }

    private fun startSplit(serverJar: Path, proxyJar: Path, heap: Int, proxyBind: String) {
        Log.info("Starting the world ($heap MB)...")
        val server = ProcessBuilder(listOf(javaExe.toString()) + JvmFlags.server(heap) +
            listOf("-Dlog4j.configurationFile=wizard-log4j2.xml", "-jar", serverJar.toString(), "nogui"))
            .directory(paths.serverDir.toFile()).redirectErrorStream(true).start()
        supervisor.register("server", server, gracefulCommand = "stop", pipeLabel = "SERVER")
        waitForPort(ports.server, server, "The world server")
        val proxy = ProcessBuilder(listOf(javaExe.toString(), "-Xms64M", "-Xmx320M", "-XX:+UseSerialGC", "-DskipUpdateCheck=true",
            "-jar", proxyJar.toString(), "cli", "--target-address", "127.0.0.1:${ports.server}",
            "--bind-address", proxyBind, "--target-version", catalog.minecraft.serverVersion,
            "--fake-accept-resource-packs", "true") + JvmFlags.HARDENING)
            .directory(paths.proxyDir.toFile()).redirectErrorStream(true).start()
        supervisor.register("proxy", proxy, pipeLabel = "PROXY")
        waitForPort(ports.proxy, proxy, "The version bridge")
    }

    private fun waitForPort(port: Int, process: Process, what: String) {
        val deadline = System.currentTimeMillis() + 180_000
        while (System.currentTimeMillis() < deadline) {
            if (portOpen(port)) return
            if (!process.isAlive) throw LauncherException(
                "$what stopped while starting.\n\nMost common causes: not enough free memory, or a damaged world.\n" +
                    "Close other heavy programs, lower the memory profile in Settings, or see the log folder for details.")
            Thread.sleep(250)
        }
        throw LauncherException("$what did not finish starting within 3 minutes. Press Play again - the first start is the slowest.")
    }

    /** Hosted mode: the server shuts itself down (saving) when the game exits. */
    fun watchClient(pid: Long) {
        if (catalog.server.mode != "split") supervisor.send("server", "watch $pid")
    }

    /**
     * Brings the server back if it dies while the game is still running, up
     * to [maxRestarts] times. Without it an out-of-memory mid-session drops
     * the player to "connection lost" with no way back but a restart.
     */
    fun superviseWhile(clientAlive: () -> Boolean, maxRestarts: Int = 2, onEvent: (String) -> Unit) {
        if (!settings.autoRestartServer) return
        Thread({
            var restarts = 0
            while (clientAlive()) {
                Thread.sleep(3000)
                if (supervisor.isRunning("server") || !clientAlive() || stopping) continue
                if (restarts++ >= maxRestarts) { onEvent("The world server crashed too many times; not restarting it again."); return@Thread }
                onEvent("The world server stopped unexpectedly - restarting it ($restarts/$maxRestarts)...")
                runCatching { supervisor.stop("proxy", graceful = false); start() }
                    .onSuccess { onEvent("The world is back - rejoin from the Multiplayer menu.") }
                    .onFailure { onEvent("The world server could not be restarted: ${it.message}"); return@Thread }
            }
        }, "server-supervisor").apply { isDaemon = true }.start()
    }

    @Volatile var stopping = false

    fun stop(report: (String) -> Unit = {}) {
        stopping = true
        try { supervisor.stopAll(report) } finally { stopping = false }
    }

    companion object {
        fun portOpen(port: Int) = runCatching { Socket().use { it.connect(InetSocketAddress("127.0.0.1", port), 400) }; true }.getOrDefault(false)
    }
}
