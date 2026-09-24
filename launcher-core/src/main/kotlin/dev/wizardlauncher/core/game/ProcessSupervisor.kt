package dev.wizardlauncher.core.game

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import dev.wizardlauncher.core.Json
import dev.wizardlauncher.core.Log
import java.io.BufferedReader
import java.io.InputStreamReader
import java.nio.file.Files
import java.nio.file.Path
import java.time.Instant
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit

/**
 * Owns the child processes (world server, proxy in split mode, client).
 *
 * Identity is PID **and** start time, recorded in `runtime_state.json`: after
 * a launcher crash the next launcher reaps exactly its own orphans and can
 * never kill an unrelated process that inherited a recycled PID.
 *
 * Stopping escalates: graceful command on stdin (the server saves the world)
 * -> terminate the process tree -> kill it. Each step only if the previous one
 * did not finish in time.
 */
class ProcessSupervisor(private val stateFile: Path) {
    class Managed(val name: String, val process: Process, val gracefulCommand: String?) {
        val pid = process.pid()
        val started: Instant? = process.info().startInstant().orElse(null)
        val alive get() = process.isAlive
    }

    private val processes = CopyOnWriteArrayList<Managed>()

    fun register(name: String, process: Process, gracefulCommand: String? = null, pipeLabel: String? = null): Managed {
        processes.removeIf { it.name == name && !it.alive }
        val managed = Managed(name, process, gracefulCommand)
        processes += managed
        if (pipeLabel != null) pipe(process, pipeLabel)
        save()
        Log.file("Started $name (pid ${managed.pid})")
        return managed
    }

    fun get(name: String): Managed? = processes.lastOrNull { it.name == name && it.alive }
    fun isRunning(name: String) = get(name) != null
    fun anyRunning() = processes.any { it.alive }

    /** Sends one line to a process's stdin (server control channel). */
    fun send(name: String, line: String): Boolean {
        val p = get(name) ?: return false
        return runCatching {
            p.process.outputStream.write((line + "\n").toByteArray())
            p.process.outputStream.flush()
        }.isSuccess
    }

    fun stop(name: String, graceful: Boolean = true, report: (String) -> Unit = {}): Boolean {
        val targets = processes.filter { it.name == name && it.alive }
        for (p in targets) {
            if (graceful && p.gracefulCommand != null) {
                report("Saving the world and stopping $name...")
                runCatching {
                    p.process.outputStream.write((p.gracefulCommand + "\n").toByteArray())
                    p.process.outputStream.flush()
                }
                if (p.process.waitFor(GRACEFUL_SECONDS, TimeUnit.SECONDS)) continue
                Log.file("$name ignored the stop command for ${GRACEFUL_SECONDS}s; terminating.")
            }
            terminateTree(p.process.toHandle())
        }
        processes.removeIf { it.name == name && !it.alive }
        save()
        return targets.none { it.alive }
    }

    fun stopAll(report: (String) -> Unit = {}) {
        listOf("proxy", "server").forEach { stop(it, report = report) }
        processes.removeIf { !it.alive }
        save()
    }

    private fun terminateTree(handle: ProcessHandle) {
        val tree = handle.descendants().toList() + handle
        tree.forEach { it.destroy() }
        val deadline = System.currentTimeMillis() + 8000
        while (tree.any { it.isAlive } && System.currentTimeMillis() < deadline) Thread.sleep(100)
        tree.filter { it.isAlive }.forEach { it.destroyForcibly() }
    }

    private fun pipe(process: Process, label: String) {
        Thread({
            runCatching {
                BufferedReader(InputStreamReader(process.inputStream)).useLines { lines ->
                    lines.forEach { if (it.isNotBlank()) Log.file("[$label] $it") }
                }
            }
        }, "pipe-$label").apply { isDaemon = true }.start()
    }

    // ------------------------------------------------------------------ crash recovery
    private fun save() {
        val list = JsonArray()
        processes.filter { it.alive }.forEach { p ->
            list.add(JsonObject().apply {
                addProperty("name", p.name)
                addProperty("pid", p.pid)
                addProperty("started", p.started?.toEpochMilli() ?: 0)
            })
        }
        runCatching {
            Json.write(stateFile, JsonObject().apply {
                addProperty("launcher_pid", ProcessHandle.current().pid())
                add("processes", list)
            })
        }
    }

    /** Stops leftovers from a launcher that died without cleaning up. */
    fun reapOrphans(): Int {
        val state = Json.read(stateFile)?.takeIf { it.isJsonObject }?.asJsonObject ?: return 0
        if (state.get("launcher_pid")?.asLong == ProcessHandle.current().pid()) return 0
        val launcherAlive = state.get("launcher_pid")?.asLong?.let { ProcessHandle.of(it).map { h -> h.isAlive }.orElse(false) } ?: false
        if (launcherAlive) return 0
        var reaped = 0
        for (e in state.getAsJsonArray("processes") ?: JsonArray()) {
            val o = e.asJsonObject
            val name = o.get("name").asString
            // A server still watching a running game is not an orphan: the
            // player closed the launcher mid-game on purpose.
            if (name == "client") continue
            val handle = ProcessHandle.of(o.get("pid").asLong).orElse(null) ?: continue
            val recorded = o.get("started")?.asLong ?: 0
            val actual = handle.info().startInstant().map { it.toEpochMilli() }.orElse(-1)
            if (recorded == 0L || kotlin.math.abs(actual - recorded) > 1000) continue
            if (state.getAsJsonArray("processes").any { it.asJsonObject.get("name").asString == "client" &&
                    ProcessHandle.of(it.asJsonObject.get("pid").asLong).map { h -> h.isAlive }.orElse(false) }) continue
            Log.info("Stopping a leftover $name from a previous session...")
            terminateTree(handle)
            reaped++
        }
        Files.deleteIfExists(stateFile)
        return reaped
    }

    companion object {
        const val GRACEFUL_SECONDS = 40L
    }
}
